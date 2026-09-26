package authz

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"encoding/pem"
	"math/big"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
	"time"

	"github.com/spiffe/go-spiffe/v2/spiffeid"
)

func TestExactAllowlistOverActualMutualTLS(t *testing.T) {
	now := time.Now()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	ca := &x509.Certificate{SerialNumber: big.NewInt(1), NotBefore: now.Add(-time.Hour), NotAfter: now.Add(time.Hour), IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign}
	der, err := x509.CreateCertificate(rand.Reader, ca, ca, key.Public(), key)
	if err != nil {
		t.Fatal(err)
	}
	pool := x509.NewCertPool()
	pool.AppendCertsFromPEM(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}))
	allowed := []spiffeid.ID{spiffeid.RequireFromString("spiffe://example.org/client/a"), spiffeid.RequireFromString("spiffe://example.org/client/b")}
	handler := HandlerForIDs(allowed)
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.TLS == nil || len(r.TLS.VerifiedChains) == 0 {
			t.Error("mutual TLS was not verified")
			return
		}
		// Model the trusted NGINX overwrite boundary, never accept caller values.
		r.Header.Set("X-Argus-TLS-Verified", "SUCCESS")
		r.Header.Set("X-Argus-TLS-Cert", url.PathEscape(string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: r.TLS.PeerCertificates[0].Raw}))))
		handler.ServeHTTP(w, r)
	}))
	server.TLS = &tls.Config{MinVersion: tls.VersionTLS12, ClientAuth: tls.RequireAndVerifyClientCert, ClientCAs: pool}
	server.StartTLS()
	defer server.Close()
	serverRoots := x509.NewCertPool()
	serverRoots.AddCert(server.Certificate())
	for index, id := range []string{allowed[0].String(), allowed[1].String(), allowed[0].String() + "/child", "spiffe://other.org/client/a"} {
		t.Run(id, func(t *testing.T) {
			clientKey, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
			u, _ := url.Parse(id)
			leaf := &x509.Certificate{SerialNumber: big.NewInt(int64(index + 2)), NotBefore: now.Add(-time.Minute), NotAfter: now.Add(time.Minute), URIs: []*url.URL{u}, KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth, x509.ExtKeyUsageServerAuth}}
			bytes, err := x509.CreateCertificate(rand.Reader, leaf, ca, clientKey.Public(), key)
			if err != nil {
				t.Fatal(err)
			}
			client := &http.Client{Transport: &http.Transport{TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12, RootCAs: serverRoots, Certificates: []tls.Certificate{{Certificate: [][]byte{bytes}, PrivateKey: clientKey}}}}, Timeout: time.Second}
			defer client.CloseIdleConnections()
			request, _ := http.NewRequest("GET", server.URL+"/authorize", nil)
			request.Header.Set("X-Argus-TLS-Cert", "spoofed")
			response, err := client.Do(request)
			if err != nil {
				t.Fatal(err)
			}
			response.Body.Close()
			want := http.StatusForbidden
			if index < 2 {
				want = http.StatusNoContent
			}
			if response.StatusCode != want {
				t.Fatalf("got %d, want %d", response.StatusCode, want)
			}
		})
	}
}
