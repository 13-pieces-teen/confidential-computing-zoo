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

type Client struct {
	httpClient *http.Client
	endpoint   string
	maxBytes   int64
}

func NewClient(endpoint *url.URL, timeout time.Duration, maxBytes int64) (*Client, error) {
	if endpoint == nil || timeout <= 0 || maxBytes <= 0 {
		return nil, fmt.Errorf("Evidence Provider client arguments are invalid")
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	requestURL := endpoint.String()
	switch endpoint.Scheme {
	case "http":
	case "unix":
		socketPath := endpoint.Path
		transport.DialContext = func(ctx context.Context, _, _ string) (net.Conn, error) {
			return (&net.Dialer{}).DialContext(ctx, "unix", socketPath)
		}
		requestURL = "http://unix/ra/v1/workload-evidence"
	default:
		return nil, fmt.Errorf("Evidence Provider endpoint must use http or unix")
	}
	return &Client{
		httpClient: &http.Client{Transport: transport, Timeout: timeout},
		endpoint:   requestURL,
		maxBytes:   maxBytes,
	}, nil
}

func (client *Client) Collect(ctx context.Context, input protocol.EvidenceRequest) (json.RawMessage, error) {
	body, err := json.Marshal(input)
	if err != nil {
		return nil, fmt.Errorf("marshal workload evidence request: %w", err)
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, client.endpoint, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("create workload evidence request: %w", err)
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	response, err := client.httpClient.Do(request)
	if err != nil {
		return nil, fmt.Errorf("call Evidence Provider: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
		return nil, fmt.Errorf("Evidence Provider returned HTTP %d", response.StatusCode)
	}
	document, err := io.ReadAll(io.LimitReader(response.Body, client.maxBytes+1))
	if err != nil {
		return nil, fmt.Errorf("read workload evidence: %w", err)
	}
	if int64(len(document)) > client.maxBytes {
		return nil, fmt.Errorf("workload evidence exceeds %d bytes", client.maxBytes)
	}
	if !json.Valid(document) {
		return nil, fmt.Errorf("Evidence Provider returned invalid JSON")
	}
	return json.RawMessage(document), nil
}
