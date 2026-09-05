package evidence

import (
	"context"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-workloadattestor/internal/protocol"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
	"time"
)

func TestEvidenceRejectsMalformedOversizedOrLegacyResponse(t *testing.T) {
	for _, body := range []string{`{"allow":true}`, `{}` + `{}`, `not-json`, string(make([]byte, 100))} {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte(body)) }))
		c := &Client{httpClient: server.Client(), endpoint: server.URL, maxBytes: 64}
		if _, err := c.Collect(context.Background(), protocol.EvidenceRequest{}); err == nil {
			t.Errorf("accepted %q", body)
		}
		server.Close()
	}
	ep, _ := url.Parse("http://127.0.0.1/evidence")
	if _, err := NewClient(ep, time.Second, 4096); err == nil {
		t.Fatal("accepted non-UDS provider")
	}
}
