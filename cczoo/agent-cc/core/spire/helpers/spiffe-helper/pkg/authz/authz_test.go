package authz

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"encoding/pem"
	"github.com/spiffe/go-spiffe/v2/spiffeid"
	"math/big"
	"net/url"
	"testing"
	"time"
)

func TestActualTLSClientIdentityAndSVIDProfile(t *testing.T) {
	id := spiffeid.RequireFromString("spiffe://argus.local/agent/openclaw")
	key, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	now := time.Now()
	for _, name := range []string{"valid", "wrong identity", "multiple SANs", "CA leaf", "expired", "missing EKU", "unverified TLS"} {
		t.Run(name, func(t *testing.T) {
			c := &x509.Certificate{SerialNumber: big.NewInt(1), NotBefore: now.Add(-time.Minute), NotAfter: now.Add(time.Minute), KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth, x509.ExtKeyUsageServerAuth}, URIs: []*url.URL{id.URL()}, BasicConstraintsValid: true}
			state := "SUCCESS"
			switch name {
			case "wrong identity":
				c.URIs = []*url.URL{spiffeid.RequireFromString("spiffe://argus.local/agent/other").URL()}
			case "multiple SANs":
				c.URIs = append(c.URIs, id.URL())
			case "CA leaf":
				c.IsCA = true
			case "expired":
				c.NotAfter = now.Add(-time.Second)
			case "missing EKU":
				c.ExtKeyUsage = nil
			case "unverified TLS":
				state = "NONE"
			}
			der, err := x509.CreateCertificate(rand.Reader, c, c, key.Public(), key)
			if err != nil {
				t.Fatal(err)
			}
			encoded := url.PathEscape(string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})))
			err = Authorize(encoded, state, id, now)
			if (err == nil) != (name == "valid") {
				t.Fatalf("authorization = %v", err)
			}
		})
	}
}
