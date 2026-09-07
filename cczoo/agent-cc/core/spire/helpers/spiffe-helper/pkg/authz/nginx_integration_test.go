//go:build linux

package authz

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"fmt"
	"github.com/spiffe/go-spiffe/v2/bundle/x509bundle"
	"github.com/spiffe/go-spiffe/v2/spiffeid"
	"github.com/spiffe/go-spiffe/v2/spiffetls/tlsconfig"
	"github.com/spiffe/go-spiffe/v2/svid/x509svid"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestNGINXMTLSAuthzAndRotation(t *testing.T) {
	if os.Getenv("ARGUS_NGINX_TESTS") != "1" {
		t.Skip("set ARGUS_NGINX_TESTS=1 on Linux with nginx to execute integration")
	}
	nginx, err := exec.LookPath("nginx")
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	now := time.Now()
	caKey, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	ca := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "NGINX test CA"}, NotBefore: now.Add(-time.Hour), NotAfter: now.Add(time.Hour), IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign}
	root, err := x509.CreateCertificate(rand.Reader, ca, ca, caKey.Public(), caKey)
	if err != nil {
		t.Fatal(err)
	}
	bundle := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: root})
	issue := func(id string, serial int64) ([]byte, []byte) {
		key, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
		u, _ := url.Parse(id)
		leaf := &x509.Certificate{SerialNumber: big.NewInt(serial), NotBefore: now.Add(-time.Minute), NotAfter: now.Add(time.Hour), URIs: []*url.URL{u}, KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth, x509.ExtKeyUsageServerAuth}, BasicConstraintsValid: true}
		der, err := x509.CreateCertificate(rand.Reader, leaf, ca, key.Public(), caKey)
		if err != nil {
			t.Fatal(err)
		}
		pk, _ := x509.MarshalPKCS8PrivateKey(key)
		return pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: pk})
	}
	const serverID = "spiffe://argus.local/service/openviking-cmem"
	const clientID = "spiffe://argus.local/agent/openclaw"
	write := func(name string, b []byte) {
		t.Helper()
		if err := os.WriteFile(filepath.Join(dir, name), b, 0600); err != nil {
			t.Fatal(err)
		}
	}
	publish := func(serial int64) {
		generation := fmt.Sprintf("generation-%d", serial)
		if err := os.Mkdir(filepath.Join(dir, generation), 0700); err != nil {
			t.Fatal(err)
		}
		cert, key := issue(serverID, serial)
		write(generation+"/svid.pem", cert)
		write(generation+"/key.pem", key)
		write(generation+"/bundle.pem", bundle)
		if err := os.Symlink(generation, filepath.Join(dir, "next")); err != nil {
			t.Fatal(err)
		}
		if err := os.Rename(filepath.Join(dir, "next"), filepath.Join(dir, "current")); err != nil {
			t.Fatal(err)
		}
	}
	publish(10)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte("openviking-test-response")) }))
	defer upstream.Close()
	socketPath := filepath.Join(dir, "authz.sock")
	listener, err := net.Listen("unix", socketPath)
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	authServer := &http.Server{Handler: Handler(spiffeid.RequireFromString(clientID)), ReadHeaderTimeout: time.Second}
	go authServer.Serve(listener)
	defer authServer.Close()
	free, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := free.Addr().String()
	free.Close()
	conf := fmt.Sprintf(`user root; worker_processes 1; daemon off; pid %s/nginx.pid;
 error_log %s/error.log info; worker_shutdown_timeout 1s;
 events { worker_connections 64; } http { access_log off; client_body_temp_path %s/body; proxy_temp_path %s/proxy;
 server { listen %s ssl; ssl_certificate %s/current/svid.pem; ssl_certificate_key %s/current/key.pem;
 ssl_client_certificate %s/current/bundle.pem; ssl_verify_client on; ssl_verify_depth 5;
 ssl_protocols TLSv1.2 TLSv1.3; ssl_session_cache off; ssl_session_tickets off;
 location = /_auth { internal; proxy_pass http://unix:%s:/authorize; proxy_method GET; proxy_pass_request_body off;
 proxy_set_header Content-Length ""; proxy_set_header X-Argus-TLS-Cert $ssl_client_escaped_cert; proxy_set_header X-Argus-TLS-Verified $ssl_client_verify; }
 location / { auth_request /_auth; proxy_pass %s; }
 }}`, dir, dir, dir, dir, addr, dir, dir, dir, socketPath, upstream.URL)
	write("nginx.conf", []byte(conf))
	command := exec.Command(nginx, "-c", filepath.Join(dir, "nginx.conf"))
	if err = command.Start(); err != nil {
		t.Fatal(err)
	}
	ended := make(chan error, 1)
	go func() { ended <- command.Wait() }()
	defer func() {
		_ = command.Process.Signal(syscall.SIGQUIT)
		select {
		case <-ended:
		case <-time.After(3 * time.Second):
			_ = command.Process.Kill()
			<-ended
		}
	}()
	b, err := x509bundle.Parse(spiffeid.RequireFromString(serverID).TrustDomain(), bundle)
	if err != nil {
		t.Fatal(err)
	}
	clientFor := func(identity string) *http.Client {
		cert, key := issue(identity, 20)
		s, err := x509svid.Parse(cert, key)
		if err != nil {
			t.Fatal(err)
		}
		return &http.Client{Transport: &http.Transport{TLSClientConfig: tlsconfig.MTLSClientConfig(s, b, tlsconfig.AuthorizeID(spiffeid.RequireFromString(serverID))), DisableKeepAlives: true}, Timeout: time.Second}
	}
	good := clientFor(clientID)
	bad := clientFor("spiffe://argus.local/agent/wrong")
	endpoint := "https://" + addr
	fetch := func(client *http.Client) (int, string, *tls.ConnectionState, error) {
		res, err := client.Get(endpoint)
		if err != nil {
			return 0, "", nil, err
		}
		defer res.Body.Close()
		body, _ := io.ReadAll(res.Body)
		return res.StatusCode, string(body), res.TLS, nil
	}
	deadline := time.Now().Add(5 * time.Second)
	for {
		status, body, state, err := fetch(good)
		if err == nil && status == 200 && body == "openviking-test-response" && state.PeerCertificates[0].SerialNumber.Int64() == 10 {
			break
		}
		if time.Now().After(deadline) {
			logs, _ := os.ReadFile(filepath.Join(dir, "error.log"))
			t.Fatalf("NGINX never ready: %d %v\n%s", status, err, logs)
		}
		time.Sleep(50 * time.Millisecond)
	}
	if status, _, _, err := fetch(bad); err != nil || status != 403 {
		t.Fatalf("wrong client identity: HTTP %d %v", status, err)
	}
	forged, _ := http.NewRequest("GET", endpoint, nil)
	forged.Header.Set("X-Argus-TLS-Verified", "SUCCESS")
	forged.Header.Set("X-Argus-TLS-Cert", "forged")
	res, err := bad.Do(forged)
	if err != nil {
		t.Fatal(err)
	}
	res.Body.Close()
	if res.StatusCode != 403 {
		t.Fatal("client-controlled auth headers bypassed NGINX")
	}
	publish(11)
	if output, err := exec.Command(nginx, "-t", "-c", filepath.Join(dir, "nginx.conf")).CombinedOutput(); err != nil {
		t.Fatalf("nginx -t: %v %s", err, output)
	}
	if err := command.Process.Signal(syscall.SIGHUP); err != nil {
		t.Fatal(err)
	}
	deadline = time.Now().Add(5 * time.Second)
	for {
		status, _, state, err := fetch(good)
		if err == nil && status == 200 && state.PeerCertificates[0].SerialNumber.Int64() == 11 {
			break
		}
		if time.Now().After(deadline) {
			logs, _ := os.ReadFile(filepath.Join(dir, "error.log"))
			t.Fatalf("NGINX did not rotate certificate: %d %v\n%s", status, err, logs)
		}
		time.Sleep(50 * time.Millisecond)
	}
	// An invalid new config is rejected before the helper can report readiness.
	write("nginx.conf", []byte(strings.Replace(conf, "ssl_verify_client on;", "invalid_directive on;", 1)))
	if err := exec.Command(nginx, "-t", "-c", filepath.Join(dir, "nginx.conf")).Run(); err == nil {
		t.Fatal("invalid reload configuration accepted")
	}
}
