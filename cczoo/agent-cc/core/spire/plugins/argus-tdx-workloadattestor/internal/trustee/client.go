package trustee

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"time"

	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-workloadattestor/internal/protocol"
)

type Client struct {
	httpClient       *http.Client
	endpoint         string
	maxResponseBytes int64
}

func NewClient(endpoint *url.URL, tlsConfig *tls.Config, expectedSPIFFEID string, timeout time.Duration, maxResponseBytes int64) (*Client, error) {
	if endpoint == nil || endpoint.Scheme != "https" {
		return nil, fmt.Errorf("Trustee endpoint must use HTTPS")
	}
	if tlsConfig == nil || len(tlsConfig.Certificates) == 0 || tlsConfig.RootCAs == nil {
		return nil, fmt.Errorf("Trustee mTLS configuration is incomplete")
	}
	if expectedSPIFFEID == "" || timeout <= 0 || maxResponseBytes <= 0 {
		return nil, fmt.Errorf("Trustee client arguments are invalid")
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.TLSClientConfig = tlsConfig.Clone()
	transport.TLSClientConfig.VerifyConnection = func(state tls.ConnectionState) error {
		return verifyTrusteeIdentity(state, expectedSPIFFEID)
	}
	return &Client{
		httpClient:       &http.Client{Transport: transport, Timeout: timeout},
		endpoint:         endpoint.String(),
		maxResponseBytes: maxResponseBytes,
	}, nil
}

func (client *Client) Verify(ctx context.Context, input protocol.VerifyRequest) (protocol.Verdict, error) {
	body, err := json.Marshal(input)
	if err != nil {
		return protocol.Verdict{}, fmt.Errorf("marshal Trustee request: %w", err)
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, client.endpoint, bytes.NewReader(body))
	if err != nil {
		return protocol.Verdict{}, fmt.Errorf("create Trustee request: %w", err)
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	response, err := client.httpClient.Do(request)
	if err != nil {
		return protocol.Verdict{}, fmt.Errorf("call Trustee: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
		return protocol.Verdict{}, fmt.Errorf("Trustee returned HTTP %d", response.StatusCode)
	}
	contents, err := io.ReadAll(io.LimitReader(response.Body, client.maxResponseBytes+1))
	if err != nil {
		return protocol.Verdict{}, fmt.Errorf("read Trustee response: %w", err)
	}
	if int64(len(contents)) > client.maxResponseBytes {
		return protocol.Verdict{}, fmt.Errorf("Trustee response exceeds %d bytes", client.maxResponseBytes)
	}
	var verdict protocol.Verdict
	decoder := json.NewDecoder(bytes.NewReader(contents))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&verdict); err != nil {
		return protocol.Verdict{}, fmt.Errorf("decode Trustee response: %w", err)
	}
	return verdict, nil
}

func verifyTrusteeIdentity(state tls.ConnectionState, expectedSPIFFEID string) error {
	if len(state.PeerCertificates) == 0 {
		return fmt.Errorf("Trustee presented no certificate")
	}
	for _, uri := range state.PeerCertificates[0].URIs {
		if uri.String() == expectedSPIFFEID {
			return nil
		}
	}
	return fmt.Errorf("Trustee SPIFFE ID mismatch")
}
