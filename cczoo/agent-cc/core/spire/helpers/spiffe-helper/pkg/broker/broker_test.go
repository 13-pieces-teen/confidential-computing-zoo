package broker

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"errors"
	api "github.com/spiffe/go-spiffe/v2/exp/proto/spiffe/broker"
	"github.com/spiffe/go-spiffe/v2/spiffeid"
	"io"
	"math/big"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

const targetURI = "spiffe://argus.local/service/openviking-cmem"

func credentialsFor(t *testing.T, uri string, serial int64) *api.X509SVID {
	t.Helper()
	now := time.Now()
	caKey, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	ca := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "Test CA"}, NotBefore: now.Add(-time.Hour), NotAfter: now.Add(time.Hour), IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign}
	root, err := x509.CreateCertificate(rand.Reader, ca, ca, caKey.Public(), caKey)
	if err != nil {
		t.Fatal(err)
	}
	key, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	u, _ := url.Parse(uri)
	cert := &x509.Certificate{SerialNumber: big.NewInt(serial), NotBefore: now.Add(-time.Minute), NotAfter: now.Add(time.Hour), URIs: []*url.URL{u}, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth, x509.ExtKeyUsageServerAuth}}
	der, err := x509.CreateCertificate(rand.Reader, cert, ca, key.Public(), caKey)
	if err != nil {
		t.Fatal(err)
	}
	pk, _ := x509.MarshalPKCS8PrivateKey(key)
	return &api.X509SVID{SpiffeId: uri, X509Svid: der, X509SvidKey: pk, Bundle: root}
}
func TestSnapshotIsolationAndValidation(t *testing.T) {
	target := spiffeid.RequireFromString(targetURI)
	valid := credentialsFor(t, targetURI, 10)
	c, err := Snapshot(&api.SubscribeToX509SVIDResponse{Svids: []*api.X509SVID{valid}}, target, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	if b, _ := pem.Decode(c.Key); b == nil || b.Type != "PRIVATE KEY" {
		t.Fatal("missing key")
	}
	other := credentialsFor(t, "spiffe://argus.local/infra/openviking-helper", 11)
	tests := map[string]*api.SubscribeToX509SVIDResponse{
		"full snapshot removal": {}, "self identity only": {Svids: []*api.X509SVID{other}},
		"duplicate":    {Svids: []*api.X509SVID{valid, valid}},
		"key mismatch": {Svids: []*api.X509SVID{{SpiffeId: targetURI, X509Svid: valid.X509Svid, X509SvidKey: other.X509SvidKey, Bundle: valid.Bundle}}},
		"forged chain": {Svids: []*api.X509SVID{{SpiffeId: targetURI, X509Svid: valid.X509Svid, X509SvidKey: valid.X509SvidKey, Bundle: other.Bundle}}},
		"wrong SAN":    {Svids: []*api.X509SVID{{SpiffeId: targetURI, X509Svid: other.X509Svid, X509SvidKey: other.X509SvidKey, Bundle: other.Bundle}}},
	}
	for name, r := range tests {
		t.Run(name, func(t *testing.T) {
			if _, err := Snapshot(r, target, time.Now()); err == nil {
				t.Fatal("untrusted snapshot accepted")
			}
		})
	}
	if _, err = Snapshot(&api.SubscribeToX509SVIDResponse{Svids: []*api.X509SVID{valid}}, target, time.Now().Add(2*time.Hour)); err == nil {
		t.Fatal("expired SVID accepted")
	}
}
func testPublisher(t *testing.T) *Publisher {
	t.Helper()
	if runtime.GOOS != "linux" {
		t.Skip("Broker filesystem lifecycle is Linux-only")
	}
	p := &Publisher{Dir: t.TempDir(), checkFS: func(string) error { return nil }, Hook: func(context.Context, string) error { return nil }}
	if err := p.Prepare(); err != nil {
		t.Fatal(err)
	}
	return p
}
func TestAtomicGenerationsAndReloadFailure(t *testing.T) {
	p := testPublisher(t)
	id := spiffeid.RequireFromString(targetURI)
	for _, serial := range []int64{10, 11} {
		c, err := Snapshot(&api.SubscribeToX509SVIDResponse{Svids: []*api.X509SVID{credentialsFor(t, targetURI, serial)}}, id, time.Now())
		if err != nil {
			t.Fatal(err)
		}
		p.Hook = func(context.Context, string) error {
			for _, name := range []string{"svid.pem", "key.pem", "bundle.pem"} {
				if _, err := os.ReadFile(filepath.Join(p.Dir, "current", name)); err != nil {
					return err
				}
			}
			return nil
		}
		if err = p.Publish(context.Background(), c); err != nil {
			t.Fatal(err)
		}
	}
	entries, _ := os.ReadDir(p.Dir)
	n := 0
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), "generation-") {
			n++
		}
	}
	if n != 1 {
		t.Fatal("old private keys retained")
	}
	p.Hook = func(context.Context, string) error { return errors.New("nginx -t failed") }
	if err := p.Publish(context.Background(), &Credentials{Serial: "broken", Expires: time.Now().Add(time.Hour)}); err == nil {
		t.Fatal("reload failure ignored")
	}
	if _, err := os.Stat(filepath.Join(p.Dir, "ready")); !os.IsNotExist(err) {
		t.Fatal("readiness retained on failure")
	}
	entries, _ = os.ReadDir(p.Dir)
	if len(entries) != 0 {
		t.Fatal("credentials retained on failure")
	}
}
func TestPEMPublishFailure(t *testing.T) {
	p := testPublisher(t)
	if err := os.Remove(p.Dir); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p.Dir, []byte("blocks directory"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := p.Publish(context.Background(), &Credentials{Expires: time.Now().Add(time.Hour)}); err == nil {
		t.Fatal("PEM failure ignored")
	}
}
func TestReadinessReadersSeeCompleteGeneration(t *testing.T) {
	p := testPublisher(t)
	for _, serial := range []string{"10", "2000000000000"} {
		var previous *os.File
		if serial != "10" {
			var err error
			previous, err = os.Open(filepath.Join(p.Dir, "ready"))
			if err != nil {
				t.Fatal(err)
			}
			defer previous.Close()
		}
		if err := p.Publish(context.Background(), &Credentials{Serial: serial, Expires: time.Now().Add(time.Hour)}); err != nil {
			t.Fatal(err)
		}
		current, err := os.ReadFile(filepath.Join(p.Dir, "ready"))
		if err != nil || string(current) != serial+"\n" {
			t.Fatalf("new readiness snapshot: %q, %v", current, err)
		}
		if previous != nil {
			old, err := io.ReadAll(previous)
			if err != nil || string(old) != "10\n" {
				t.Fatalf("reader of previous generation observed a mutation: %q, %v", old, err)
			}
		}
	}
}
func TestCredentialsExpireDuringPublication(t *testing.T) {
	p := testPublisher(t)
	p.Hook = func(ctx context.Context, _ string) error {
		<-ctx.Done()
		// Even if a hook returns success at its deadline, readiness must not
		// claim an expired credential is usable.
		return nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	err := p.Publish(ctx, &Credentials{Serial: "10", Expires: time.Now().Add(50 * time.Millisecond)})
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("expected credential expiry, got %v", err)
	}
	if ctx.Err() != nil {
		t.Fatal("publication waited for the outer deadline instead of credential expiry")
	}
	entries, err := os.ReadDir(p.Dir)
	if err != nil || len(entries) != 0 {
		t.Fatalf("expired publication retained credentials/readiness: %v, %v", entries, err)
	}
}
func TestSubscriptionAndTargetFailureTerminate(t *testing.T) {
	for _, kind := range []string{"disconnect", "removal", "target exit", "self removed"} {
		t.Run(kind, func(t *testing.T) {
			p := testPublisher(t)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			exited := make(chan error, 1)
			updates := make(chan *api.SubscribeToX509SVIDResponse, 1)
			recv := func() (*api.SubscribeToX509SVIDResponse, error) {
				select {
				case <-ctx.Done():
					return nil, ctx.Err()
				case r := <-updates:
					if r == nil {
						return nil, io.EOF
					}
					return r, nil
				}
			}
			checkSelf := func() error { return nil }
			switch kind {
			case "disconnect":
				updates <- nil
			case "removal":
				updates <- &api.SubscribeToX509SVIDResponse{}
			case "target exit":
				exited <- errors.New("pidfd exited")
			case "self removed":
				checkSelf = func() error { return errors.New("Helper SVID removed") }
			}
			done := make(chan error, 1)
			go func() { done <- consume(ctx, recv, exited, p, spiffeid.RequireFromString(targetURI), checkSelf) }()
			select {
			case err := <-done:
				if err == nil {
					t.Fatal("failure ignored")
				}
			case <-time.After(2 * time.Second):
				t.Fatal("failure did not stop subscriber")
			}
		})
	}
}
