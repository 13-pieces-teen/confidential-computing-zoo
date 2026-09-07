package main

import (
	"bytes"
	"crypto/tls"
	"encoding/json"
	"flag"
	"fmt"
	"github.com/spiffe/go-spiffe/v2/bundle/x509bundle"
	"github.com/spiffe/go-spiffe/v2/spiffeid"
	"github.com/spiffe/go-spiffe/v2/spiffetls/tlsconfig"
	"github.com/spiffe/go-spiffe/v2/svid/x509svid"
	"io"
	"net"
	"net/http"
	"os"
	"time"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
func run() error {
	cert := flag.String("cert", "", "client SVID PEM")
	key := flag.String("key", "", "client key PEM")
	bundle := flag.String("bundle", "", "trust bundle PEM")
	target := flag.String("server-id", "spiffe://argus.local/service/openviking-cmem", "exact server SPIFFE ID")
	endpoint := flag.String("url", "", "HTTPS business endpoint")
	tlsOnly := flag.Bool("tls-only", false, "check that NGINX loaded the certificate given in -cert")
	flag.Parse()
	id, err := spiffeid.FromString(*target)
	if err != nil {
		return err
	}
	s, err := x509svid.Load(*cert, *key)
	if err != nil {
		return err
	}
	b, err := x509bundle.Load(id.TrustDomain(), *bundle)
	if err != nil {
		return err
	}
	tlsConfig := tlsconfig.MTLSClientConfig(s, b, tlsconfig.AuthorizeID(id))
	tlsConfig.ClientSessionCache = nil
	client := &http.Client{Transport: &http.Transport{TLSClientConfig: tlsConfig, DisableKeepAlives: true}, Timeout: 15 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	req, err := http.NewRequest("GET", *endpoint, nil)
	if err != nil {
		return err
	}
	if req.URL.Scheme != "https" {
		return fmt.Errorf("HTTPS URL required")
	}
	if *tlsOnly {
		deadline := time.Now().Add(3 * time.Second)
		for {
			conn, err := tls.DialWithDialer(&net.Dialer{Timeout: time.Second}, "tcp", req.URL.Host, tlsConfig)
			if err == nil {
				loaded := bytes.Equal(conn.ConnectionState().PeerCertificates[0].Raw, s.Certificates[0].Raw)
				_ = conn.Close()
				if loaded {
					return nil
				}
			}
			if !time.Now().Before(deadline) {
				return fmt.Errorf("NGINX did not load the published certificate")
			}
			time.Sleep(100 * time.Millisecond)
		}
	}
	// Optional business credential stays out of command arguments and evidence.
	if apiKey := os.Getenv("OPENVIKING_API_KEY"); apiKey != "" {
		req.Header.Set("X-API-Key", apiKey)
	}
	res, err := client.Do(req)
	if err != nil {
		return err
	}
	defer res.Body.Close()
	_, err = io.Copy(io.Discard, io.LimitReader(res.Body, 1<<20))
	if err != nil {
		return err
	}
	if res.StatusCode < 200 || res.StatusCode >= 300 {
		return fmt.Errorf("business HTTP %d", res.StatusCode)
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{"result": "PASS", "client_spiffe_id": s.ID.String(), "server_spiffe_id": id.String(), "server_serial": res.TLS.PeerCertificates[0].SerialNumber.String(), "http_status": res.StatusCode, "checked_at": time.Now().UTC()})
}
