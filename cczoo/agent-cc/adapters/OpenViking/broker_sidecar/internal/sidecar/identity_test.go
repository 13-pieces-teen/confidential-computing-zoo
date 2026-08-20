package sidecar

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"math/big"
	"net/url"
	"testing"
	"time"

	"github.com/spiffe/go-spiffe/v2/exp/proto/spiffe/broker"
)

func TestIdentityStoreTreatsEveryBrokerResponseAsFullSnapshot(t *testing.T) {
	targetID := "spiffe://argus.local/service/openviking-cmem"
	svid := testBrokerSVID(t, targetID)
	store := NewIdentityStore(targetID)

	found, err := store.ApplySnapshot(&broker.SubscribeToX509SVIDResponse{Svids: []*broker.X509SVID{svid}})
	if err != nil {
		t.Fatal(err)
	}
	if !found || store.Current() == nil {
		t.Fatal("target SVID was not installed")
	}

	found, err = store.ApplySnapshot(&broker.SubscribeToX509SVIDResponse{})
	if err != nil {
		t.Fatal(err)
	}
	if found || store.Current() != nil {
		t.Fatal("missing target in a full snapshot did not clear the old identity")
	}
}

func TestAuthorizeClientRequiresExactSPIFFEID(t *testing.T) {
	want, err := url.Parse("spiffe://argus.local/agent/openclaw")
	if err != nil {
		t.Fatal(err)
	}
	other, err := url.Parse("spiffe://argus.local/agent/other")
	if err != nil {
		t.Fatal(err)
	}
	if err := authorizeClient([]*x509.Certificate{{URIs: []*url.URL{want}}}, want.String()); err != nil {
		t.Fatalf("exact client rejected: %v", err)
	}
	if err := authorizeClient([]*x509.Certificate{{URIs: []*url.URL{other}}}, want.String()); err == nil {
		t.Fatal("different client SPIFFE ID was accepted")
	}
}

func testBrokerSVID(t *testing.T, id string) *broker.X509SVID {
	t.Helper()
	now := time.Now()
	caKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	caTemplate := &x509.Certificate{
		SerialNumber:          big.NewInt(1),
		Subject:               pkix.Name{CommonName: "test-ca"},
		NotBefore:             now.Add(-time.Minute),
		NotAfter:              now.Add(time.Hour),
		IsCA:                  true,
		BasicConstraintsValid: true,
		KeyUsage:              x509.KeyUsageCertSign,
	}
	caDER, err := x509.CreateCertificate(rand.Reader, caTemplate, caTemplate, &caKey.PublicKey, caKey)
	if err != nil {
		t.Fatal(err)
	}
	ca, err := x509.ParseCertificate(caDER)
	if err != nil {
		t.Fatal(err)
	}
	leafKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	uri, err := url.Parse(id)
	if err != nil {
		t.Fatal(err)
	}
	leafTemplate := &x509.Certificate{
		SerialNumber: big.NewInt(2),
		NotBefore:    now.Add(-time.Minute),
		NotAfter:     now.Add(time.Hour),
		URIs:         []*url.URL{uri},
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}
	leafDER, err := x509.CreateCertificate(rand.Reader, leafTemplate, ca, &leafKey.PublicKey, caKey)
	if err != nil {
		t.Fatal(err)
	}
	privateKey, err := x509.MarshalPKCS8PrivateKey(leafKey)
	if err != nil {
		t.Fatal(err)
	}
	return &broker.X509SVID{
		SpiffeId:    id,
		X509Svid:    leafDER,
		X509SvidKey: privateKey,
		Bundle:      caDER,
	}
}
