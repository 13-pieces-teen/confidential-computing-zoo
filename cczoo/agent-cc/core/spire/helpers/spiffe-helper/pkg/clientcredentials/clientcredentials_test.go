package clientcredentials

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"fmt"
	"math/big"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	api "github.com/spiffe/go-spiffe/v2/exp/proto/spiffe/broker"
	"github.com/spiffe/go-spiffe/v2/spiffeid"
	"github.com/spiffe/spiffe-helper/pkg/broker"
)

var clientID = spiffeid.RequireFromString("spiffe://argus.local/agent/openclaw")

func snapshot(t *testing.T) *api.SubscribeToX509SVIDResponse {
	t.Helper()
	key, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	ca := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "test"}, IsCA: true, BasicConstraintsValid: true,
		NotBefore: time.Now().Add(-time.Minute), NotAfter: time.Now().Add(time.Hour), KeyUsage: x509.KeyUsageCertSign}
	caDER, err := x509.CreateCertificate(rand.Reader, ca, ca, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	uri, _ := url.Parse(clientID.String())
	leaf := &x509.Certificate{SerialNumber: big.NewInt(31), URIs: []*url.URL{uri}, NotBefore: ca.NotBefore, NotAfter: ca.NotAfter,
		KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth, x509.ExtKeyUsageServerAuth}, BasicConstraintsValid: true}
	der, err := x509.CreateCertificate(rand.Reader, leaf, ca, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	pkcs8, _ := x509.MarshalPKCS8PrivateKey(key)
	return &api.SubscribeToX509SVIDResponse{Svids: []*api.X509SVID{{SpiffeId: clientID.String(), X509Svid: der, X509SvidKey: pkcs8, Bundle: caDER}}}
}

func TestCompleteGenerationAndLease(t *testing.T) {
	p := &publisher{dir: t.TempDir(), gid: -1}
	material, err := broker.Snapshot(snapshot(t), clientID, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	if err = p.publish(material, now); err != nil {
		t.Fatal(err)
	}
	data, _ := os.ReadFile(filepath.Join(p.dir, "ready.json"))
	var ready lease
	if err = json.Unmarshal(data, &ready); err != nil {
		t.Fatal(err)
	}
	if ready.Serial != "1f" || ready.Until > now.Add(2500*time.Millisecond).UnixMilli() || ready.Expires != material.Expires.UnixMilli() {
		t.Fatalf("unexpected lease %+v", ready)
	}
	for _, name := range []string{"key.pem", "svid.pem", "bundle.pem"} {
		if _, err = os.Stat(filepath.Join(p.dir, ready.Generation, name)); err != nil {
			t.Fatal(err)
		}
	}
	old := ready.Generation
	if err = p.publish(material, now); err != nil {
		t.Fatal(err)
	}
	if _, err = os.Stat(filepath.Join(p.dir, old)); !os.IsNotExist(err) {
		t.Fatal("old PEM generation retained")
	}
	if err = p.renew(material.Expires); err == nil {
		t.Fatal("expired lease renewed")
	}
	if err = p.clear(); err != nil {
		t.Fatal(err)
	}
	entries, _ := os.ReadDir(p.dir)
	if len(entries) != 0 {
		t.Fatal("credentials retained")
	}
}

func TestSubscriberFailureRemovesPublishedPEM(t *testing.T) {
	for _, failure := range []string{"removed", "duplicate", "wrong-id", "disconnect", "target-exit", "self-removed", "publish-failed", "cancelled"} {
		t.Run(failure, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			p := &publisher{dir: t.TempDir(), gid: -1}
			first := snapshot(t)
			ended := make(chan error, 1)
			calls := 0
			self := func() error {
				if failure == "self-removed" && p.current != nil {
					return fmt.Errorf("self removed")
				}
				return nil
			}
			recv := func() (*api.SubscribeToX509SVIDResponse, error) {
				calls++
				if calls == 1 {
					return first, nil
				}
				// The consumer receives synchronously on an unbuffered channel but
				// publication follows delivery. Wait for the actual ready lease.
				for {
					if _, err := os.Stat(filepath.Join(p.dir, "ready.json")); err == nil {
						break
					}
					select {
					case <-ctx.Done():
						return nil, ctx.Err()
					case <-time.After(time.Millisecond):
					}
				}
				switch failure {
				case "removed":
					return &api.SubscribeToX509SVIDResponse{}, nil
				case "duplicate":
					return &api.SubscribeToX509SVIDResponse{Svids: []*api.X509SVID{first.Svids[0], first.Svids[0]}}, nil
				case "wrong-id":
					return &api.SubscribeToX509SVIDResponse{Svids: []*api.X509SVID{{SpiffeId: "spiffe://argus.local/infra/helper"}}}, nil
				case "disconnect":
					return nil, fmt.Errorf("stream disconnected")
				case "target-exit":
					ended <- fmt.Errorf("pidfd readable")
					<-ctx.Done()
					return nil, ctx.Err()
				case "cancelled":
					cancel()
					return nil, ctx.Err()
				case "publish-failed":
					os.Remove(filepath.Join(p.dir, "ready.json"))
					os.Mkdir(filepath.Join(p.dir, "ready.json"), 0700)
					return first, nil
				default:
					return first, nil
				}
			}
			err := consume(ctx, recv, ended, p, clientID, self)
			if err == nil || strings.Contains(err.Error(), "deadline exceeded") {
				t.Fatalf("expected specific failure, got %v", err)
			}
			entries, _ := os.ReadDir(p.dir)
			if len(entries) != 0 {
				t.Fatalf("credential files retained after %s", failure)
			}
		})
	}
}
