package trustee

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"

	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-workloadattestor/internal/protocol"
)

func TestVerifyUsesWorkloadTrusteeContract(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodPost || request.URL.Path != "/v1/verify/tdx-workload" {
			t.Fatalf("request = %s %s", request.Method, request.URL.Path)
		}
		var input protocol.VerifyRequest
		if err := json.NewDecoder(request.Body).Decode(&input); err != nil {
			t.Fatal(err)
		}
		if input.PID != 4321 || input.Nonce != "workload-nonce" {
			t.Fatalf("input = %#v", input)
		}
		writer.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(writer).Encode(protocol.Verdict{
			ProtocolVersion: protocol.Version,
			Nonce:           input.Nonce,
			PID:             input.PID,
			Decision:        "allow",
			StableErrorCode: "OK",
			WorkloadID:      "openviking-cmem",
			PolicyID:        "openviking-cmem-v1",
		})
	}))
	defer server.Close()
	client := &Client{httpClient: server.Client(), endpoint: server.URL + "/v1/verify/tdx-workload", maxResponseBytes: 4096}

	verdict, err := client.Verify(context.Background(), protocol.VerifyRequest{
		ProtocolVersion: protocol.Version,
		Nonce:           "workload-nonce",
		PID:             4321,
		Evidence:        json.RawMessage(`{"protocol_version":1}`),
	})
	if err != nil {
		t.Fatal(err)
	}
	if verdict.Decision != "allow" || verdict.WorkloadID != "openviking-cmem" {
		t.Fatalf("verdict = %#v", verdict)
	}
}

func TestVerifyTrusteeIdentityRequiresExactSPIFFEID(t *testing.T) {
	want, err := url.Parse("spiffe://argus.local/trustee")
	if err != nil {
		t.Fatal(err)
	}
	other, err := url.Parse("spiffe://argus.local/other")
	if err != nil {
		t.Fatal(err)
	}
	if err := verifyTrusteeIdentity(tls.ConnectionState{PeerCertificates: []*x509.Certificate{{URIs: []*url.URL{want}}}}, want.String()); err != nil {
		t.Fatalf("exact identity rejected: %v", err)
	}
	if err := verifyTrusteeIdentity(tls.ConnectionState{PeerCertificates: []*x509.Certificate{{URIs: []*url.URL{other}}}}, want.String()); err == nil {
		t.Fatal("different SPIFFE ID was accepted")
	}
}
