package sidecar

import (
	"bytes"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"math/big"
	"net/url"
	"testing"
	"time"

	"github.com/spiffe/go-spiffe/v2/exp/proto/spiffe/broker"
)

func TestIdentityStoreUsesFullBrokerSnapshots(t *testing.T) {
	targetID := "spiffe://argus.local/agent/openclaw"
	store := NewIdentityStore(targetID)
	found, err := store.ApplySnapshot(&broker.SubscribeToX509SVIDResponse{
		Svids: []*broker.X509SVID{testBrokerSVID(t, targetID)},
	})
	if err != nil {
		t.Fatal(err)
	}
	if !found || store.Current() == nil {
		t.Fatal("OpenClaw SVID was not installed")
	}
	found, err = store.ApplySnapshot(&broker.SubscribeToX509SVIDResponse{})
	if err != nil {
		t.Fatal(err)
	}
	if found || store.Current() != nil {
		t.Fatal("missing target in a full snapshot did not clear the old identity")
	}
}

func TestIdentityStoreReplacesSVIDFromNewSnapshot(t *testing.T) {
	const targetID = "spiffe://argus.local/agent/openclaw"
	store := NewIdentityStore(targetID)
	if _, err := store.ApplySnapshot(&broker.SubscribeToX509SVIDResponse{
		Svids: []*broker.X509SVID{testBrokerSVID(t, targetID)},
	}); err != nil {
		t.Fatal(err)
	}
	first := append([]byte(nil), store.Current().Certificate.Leaf.Raw...)
	if _, err := store.ApplySnapshot(&broker.SubscribeToX509SVIDResponse{
		Svids: []*broker.X509SVID{testBrokerSVID(t, targetID)},
	}); err != nil {
		t.Fatal(err)
	}
	if bytes.Equal(first, store.Current().Certificate.Leaf.Raw) {
		t.Fatal("new Broker snapshot did not replace the in-memory OpenClaw SVID")
	}
}

func TestPeerCertificateRequiresExactlyOneExpectedSPIFFEID(t *testing.T) {
	expected, err := url.Parse("spiffe://argus.local/service/openviking-cmem")
	if err != nil {
		t.Fatal(err)
	}
	other, err := url.Parse("spiffe://argus.local/service/other")
	if err != nil {
		t.Fatal(err)
	}
	if err := authorizeSPIFFEID(&x509.Certificate{URIs: []*url.URL{expected}}, expected.String()); err != nil {
		t.Fatalf("expected identity rejected: %v", err)
	}
	if err := authorizeSPIFFEID(&x509.Certificate{URIs: []*url.URL{expected, other}}, expected.String()); err == nil {
		t.Fatal("certificate with an additional URI identity was accepted")
	}
}

func testBrokerSVID(t *testing.T, id string) *broker.X509SVID {
	svid, _ := testIdentityMaterial(t, id, "spiffe://argus.local/service/openviking-cmem")
	return svid
}

func testIdentityMaterial(t *testing.T, clientID string, serverID string) (*broker.X509SVID, tls.Certificate) {
	t.Helper()
	now := time.Now()
	caKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	caTemplate := &x509.Certificate{
		SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "test-ca"},
		NotBefore: now.Add(-time.Minute), NotAfter: now.Add(time.Hour), IsCA: true,
		BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign,
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
	spiffeID, err := url.Parse(clientID)
	if err != nil {
		t.Fatal(err)
	}
	leafTemplate := &x509.Certificate{
		SerialNumber: big.NewInt(2), NotBefore: now.Add(-time.Minute), NotAfter: now.Add(time.Hour),
		URIs: []*url.URL{spiffeID}, KeyUsage: x509.KeyUsageDigitalSignature,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth},
	}
	leafDER, err := x509.CreateCertificate(rand.Reader, leafTemplate, ca, &leafKey.PublicKey, caKey)
	if err != nil {
		t.Fatal(err)
	}
	privateKey, err := x509.MarshalPKCS8PrivateKey(leafKey)
	if err != nil {
		t.Fatal(err)
	}
	serverKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	serverURI, err := url.Parse(serverID)
	if err != nil {
		t.Fatal(err)
	}
	serverTemplate := &x509.Certificate{
		SerialNumber: big.NewInt(3), NotBefore: now.Add(-time.Minute), NotAfter: now.Add(time.Hour),
		URIs: []*url.URL{serverURI}, KeyUsage: x509.KeyUsageDigitalSignature,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}
	serverDER, err := x509.CreateCertificate(rand.Reader, serverTemplate, ca, &serverKey.PublicKey, caKey)
	if err != nil {
		t.Fatal(err)
	}
	return &broker.X509SVID{SpiffeId: clientID, X509Svid: leafDER, X509SvidKey: privateKey, Bundle: caDER}, tls.Certificate{
		Certificate: [][]byte{serverDER}, PrivateKey: serverKey,
	}
}
