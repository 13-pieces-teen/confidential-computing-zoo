package evidence

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
	"time"

	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-workloadattestor/internal/protocol"
)

func TestCollectUsesWorkloadEvidenceContract(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodPost || request.URL.Path != "/ra/v1/workload-evidence" {
			t.Fatalf("request = %s %s", request.Method, request.URL.Path)
		}
		var input protocol.EvidenceRequest
		if err := json.NewDecoder(request.Body).Decode(&input); err != nil {
			t.Fatal(err)
		}
		if input.PID != 4321 || input.Nonce != "workload-nonce" {
			t.Fatalf("input = %#v", input)
		}
		writer.Header().Set("Content-Type", "application/json")
		_, _ = writer.Write([]byte(`{"protocol_version":1,"nonce":"workload-nonce","pid":4321}`))
	}))
	defer server.Close()
	endpoint, err := url.Parse(server.URL + "/ra/v1/workload-evidence")
	if err != nil {
		t.Fatal(err)
	}
	client, err := NewClient(endpoint, time.Second, 4096)
	if err != nil {
		t.Fatal(err)
	}

	document, err := client.Collect(context.Background(), protocol.EvidenceRequest{
		ProtocolVersion: protocol.Version,
		Nonce:           "workload-nonce",
		PID:             4321,
	})
	if err != nil {
		t.Fatal(err)
	}
	if string(document) != `{"protocol_version":1,"nonce":"workload-nonce","pid":4321}` {
		t.Fatalf("document = %s", document)
	}
}
