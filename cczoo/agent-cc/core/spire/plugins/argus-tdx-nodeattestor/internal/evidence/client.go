package evidence

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"time"
)

// Client talks only to the guest-local Evidence Provider. Remote evidence
// verification is deliberately handled by the Server-side Trustee client.
type Client struct {
	httpClient *http.Client
	requestURL string
	maxBytes   int64
}

func NewClient(endpoint *url.URL, timeout time.Duration, maxBytes int64) (*Client, error) {
	if endpoint == nil {
		return nil, fmt.Errorf("evidence endpoint is required")
	}
	if timeout <= 0 {
		return nil, fmt.Errorf("evidence timeout must be positive")
	}
	if maxBytes <= 0 {
		return nil, fmt.Errorf("maximum evidence size must be positive")
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	requestURL := endpoint.String()
	if endpoint.Scheme == "unix" {
		socketPath := endpoint.Path
		transport.DialContext = func(ctx context.Context, _, _ string) (net.Conn, error) {
			return (&net.Dialer{}).DialContext(ctx, "unix", socketPath)
		}
		// net/http still needs an HTTP URL; DialContext redirects the synthetic
		// host to the configured Unix socket.
		requestURL = "http://unix/ra/v1/evidence"
	}
	return &Client{
		httpClient: &http.Client{Transport: transport, Timeout: timeout},
		requestURL: requestURL,
		maxBytes:   maxBytes,
	}, nil
}

func (client *Client) GetEvidence(ctx context.Context, request []byte) ([]byte, error) {
	httpRequest, err := http.NewRequestWithContext(ctx, http.MethodPost, client.requestURL, bytes.NewReader(request))
	if err != nil {
		return nil, fmt.Errorf("create evidence request: %w", err)
	}
	httpRequest.Header.Set("Content-Type", "application/json")
	httpRequest.Header.Set("Accept", "application/json")

	response, err := client.httpClient.Do(httpRequest)
	if err != nil {
		return nil, fmt.Errorf("request evidence: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
		return nil, fmt.Errorf("evidence provider returned HTTP %d", response.StatusCode)
	}
	// Read one byte past the limit so an exactly-full response is distinguishable
	// from a truncated oversized response.
	contents, err := io.ReadAll(io.LimitReader(response.Body, client.maxBytes+1))
	if err != nil {
		return nil, fmt.Errorf("read evidence response: %w", err)
	}
	if int64(len(contents)) > client.maxBytes {
		return nil, fmt.Errorf("evidence response exceeds %d bytes", client.maxBytes)
	}
	return contents, nil
}
