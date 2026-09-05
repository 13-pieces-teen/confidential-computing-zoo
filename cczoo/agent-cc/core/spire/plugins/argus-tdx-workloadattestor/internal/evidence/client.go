package evidence

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"time"

	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-workloadattestor/internal/protocol"
)

// Client talks to the agent-local Evidence Provider. It only collects a
// typed Quote document; it neither appraises trust nor creates selectors.
type Client struct {
	httpClient *http.Client
	endpoint   string
	maxBytes   int64
}

// NewClient builds a bounded client for the local Unix-domain socket.
func NewClient(endpoint *url.URL, timeout time.Duration, maxBytes int64) (*Client, error) {
	if endpoint == nil || timeout <= 0 || maxBytes <= 0 {
		return nil, fmt.Errorf("Evidence Provider client arguments are invalid")
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	requestURL := endpoint.String()
	switch endpoint.Scheme {
	case "unix":
		socketPath := endpoint.Path
		transport.DialContext = func(ctx context.Context, _, _ string) (net.Conn, error) {
			return (&net.Dialer{}).DialContext(ctx, "unix", socketPath)
		}
		requestURL = "http://unix/ra/v1/workload-evidence"
	default:
		return nil, fmt.Errorf("Evidence Provider endpoint must use unix")
	}
	return &Client{
		httpClient: &http.Client{Transport: transport, Timeout: timeout, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }},
		endpoint:   requestURL,
		maxBytes:   maxBytes,
	}, nil
}

// Collect obtains PID-bound evidence for one nonce. The plugin deliberately
// checks only transport, size, and JSON syntax here; evidence interpretation
// belongs to the Trustee.
func (client *Client) Collect(ctx context.Context, input protocol.EvidenceRequest) (protocol.Evidence, error) {
	body, err := json.Marshal(input)
	if err != nil {
		return protocol.Evidence{}, fmt.Errorf("marshal workload evidence request: %w", err)
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, client.endpoint, bytes.NewReader(body))
	if err != nil {
		return protocol.Evidence{}, fmt.Errorf("create workload evidence request: %w", err)
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	response, err := client.httpClient.Do(request)
	if err != nil {
		return protocol.Evidence{}, fmt.Errorf("call Evidence Provider: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
		return protocol.Evidence{}, fmt.Errorf("Evidence Provider returned HTTP %d", response.StatusCode)
	}
	// Read one byte past the limit so an oversized response cannot be mistaken
	// for a complete, valid evidence document.
	document, err := io.ReadAll(io.LimitReader(response.Body, client.maxBytes+1))
	if err != nil {
		return protocol.Evidence{}, fmt.Errorf("read workload evidence: %w", err)
	}
	if int64(len(document)) > client.maxBytes {
		return protocol.Evidence{}, fmt.Errorf("workload evidence exceeds %d bytes", client.maxBytes)
	}
	var result protocol.Evidence
	if err := protocol.Decode(document, &result); err != nil {
		return protocol.Evidence{}, fmt.Errorf("invalid evidence: %w", err)
	}
	return result, nil
}
