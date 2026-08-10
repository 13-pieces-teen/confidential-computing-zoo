package trustee

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
	"time"

	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/internal/policy"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/internal/protocol"
)

func TestVerifyNodeAcceptsBoundPolicyCompliantResponse(t *testing.T) {
	now := time.Date(2026, 7, 28, 1, 0, 0, 0, time.UTC)
	input := validInput(t)
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodPost || request.Header.Get("Content-Type") != "application/json" {
			t.Errorf("invalid Trustee request metadata")
		}
		contents, err := io.ReadAll(request.Body)
		if err != nil {
			t.Error(err)
		}
		var raw map[string]json.RawMessage
		if err := json.Unmarshal(contents, &raw); err != nil {
			t.Error(err)
		}
		var evidence map[string]any
		if err := json.Unmarshal(raw["evidence"], &evidence); err != nil || evidence["quote"] != "fixture" {
			t.Errorf("evidence was not embedded as an object: %s", raw["evidence"])
		}
		var parsed verifyRequest
		if err := json.Unmarshal(contents, &parsed); err != nil {
			t.Error(err)
		}
		response := validResponse(parsed, now)
		writer.Header().Set("Content-Type", "application/json")
		if err := json.NewEncoder(writer).Encode(response); err != nil {
			t.Error(err)
		}
	}))
	defer server.Close()
	client := &Client{httpClient: server.Client(), endpoint: server.URL, maxResponseBytes: 1 << 20, now: func() time.Time { return now }}
	claims, err := client.VerifyNode(context.Background(), input)
	if err != nil {
		t.Fatal(err)
	}
	if claims.InstanceID != "tdvm-0001" || claims.TCBStatus != "up_to_date" {
		t.Fatalf("claims = %#v", claims)
	}
}

func TestVerifyNodeRejectsMismatchedAndDeniedResponses(t *testing.T) {
	now := time.Date(2026, 7, 28, 1, 0, 0, 0, time.UTC)
	for name, mutate := range map[string]func(*verifyResponse){
		"session": func(response *verifyResponse) {
			response.SessionID = base64.RawURLEncoding.EncodeToString(bytes.Repeat([]byte{9}, 32))
		},
		"policy": func(response *verifyResponse) {
			response.PolicyDigest = "sha256:" + string(bytes.Repeat([]byte{'f'}, 64))
		},
		"deny": func(response *verifyResponse) {
			response.Decision = "deny"
			response.StableErrorCode = "POLICY_REJECTED"
			response.VerifiedClaims = nil
		},
		"debug": func(response *verifyResponse) { response.VerifiedClaims.DebugEnabled = true },
		"rtmr":  func(response *verifyResponse) { value := "ffff"; response.VerifiedClaims.RTMR["0"] = &value },
	} {
		t.Run(name, func(t *testing.T) {
			input := validInput(t)
			server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
				var parsed verifyRequest
				if err := json.NewDecoder(request.Body).Decode(&parsed); err != nil {
					t.Error(err)
				}
				response := validResponse(parsed, now)
				mutate(&response)
				_ = json.NewEncoder(writer).Encode(response)
			}))
			defer server.Close()
			client := &Client{httpClient: server.Client(), endpoint: server.URL, maxResponseBytes: 1 << 20, now: func() time.Time { return now }}
			if _, err := client.VerifyNode(context.Background(), input); err == nil {
				t.Fatal("invalid Trustee response was accepted")
			}
		})
	}
}

func TestVerifyNodeRejectsUnknownResponseField(t *testing.T) {
	now := time.Date(2026, 7, 28, 1, 0, 0, 0, time.UTC)
	input := validInput(t)
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		var parsed verifyRequest
		_ = json.NewDecoder(request.Body).Decode(&parsed)
		contents, _ := json.Marshal(validResponse(parsed, now))
		contents = append(contents[:len(contents)-1], []byte(`,"unexpected":true}`)...)
		_, _ = writer.Write(contents)
	}))
	defer server.Close()
	client := &Client{httpClient: server.Client(), endpoint: server.URL, maxResponseBytes: 1 << 20, now: func() time.Time { return now }}
	if _, err := client.VerifyNode(context.Background(), input); err == nil {
		t.Fatal("unknown Trustee response field was accepted")
	}
}

func TestVerifyTrusteeIdentityRequiresExactSPIFFEID(t *testing.T) {
	expected, _ := url.Parse("spiffe://argus.local/service/trustee")
	other, _ := url.Parse("spiffe://argus.local/service/other")
	if err := verifyTrusteeIdentity(tls.ConnectionState{PeerCertificates: []*x509.Certificate{{URIs: []*url.URL{expected}}}}, expected.String()); err != nil {
		t.Fatal(err)
	}
	if err := verifyTrusteeIdentity(tls.ConnectionState{PeerCertificates: []*x509.Certificate{{URIs: []*url.URL{other}}}}, expected.String()); err == nil {
		t.Fatal("unexpected Trustee SPIFFE ID was accepted")
	}
}

func validInput(t *testing.T) VerifyInput {
	t.Helper()
	loadedPolicy, err := policy.Parse([]byte(`
version: 1
policy_id: openviking-prod-v1
tee:
  type: tdx
  allow_debug: false
  allowed_tcb_status: [up_to_date]
  allowed_mrtd: [aabb]
  allowed_rtmr:
    "0": [0011]
binding:
  require_report_data: true
  require_attestation_key_digest: true
  require_instance_id: true
`))
	if err != nil {
		t.Fatal(err)
	}
	nonce := base64.RawURLEncoding.EncodeToString(bytes.Repeat([]byte{3}, protocol.NonceSize))
	request, err := json.Marshal(map[string]any{
		"version": "v1", "nonce": nonce, "caller_id": "spiffe://argus.local/spire/server",
		"target":           map[string]any{"service_name": "argus-tdx-node", "target_uri": "argus-node:" + string(bytes.Repeat([]byte{'a'}, 64))},
		"requested_claims": []string{"TeeQuote", "IdentityClaims"}, "profile_digest": loadedPolicy.Digest,
	})
	if err != nil {
		t.Fatal(err)
	}
	return VerifyInput{
		SessionID: bytes.Repeat([]byte{1}, protocol.SessionIDSize), EvidenceJSON: []byte(`{"quote":"fixture"}`),
		EvidenceRequestJSON: request, AttestationKey: bytes.Repeat([]byte{2}, protocol.PublicKeySize), Policy: loadedPolicy,
	}
}

func validResponse(request verifyRequest, now time.Time) verifyResponse {
	rtmr := "0011"
	claims := &VerifiedNodeClaims{
		QuoteVerified: true, ReportDataVerified: true, TCBStatus: "up_to_date", MRTD: "aabb",
		RTMR: map[string]*string{"0": &rtmr, "1": nil, "2": nil, "3": nil}, DebugEnabled: false,
		InstanceID: "tdvm-0001", PolicyID: request.PolicyID, PolicyDigest: request.PolicyDigest,
		AttestationKeyDigest: request.AttestationKeyDigest, EvidenceRequestDigest: request.EvidenceRequestDigest,
		VerifiedAt: now.Add(-time.Second).Format("2006-01-02T15:04:05Z"), ExpiresAt: now.Add(time.Minute).Format("2006-01-02T15:04:05Z"),
	}
	return verifyResponse{
		ProtocolVersion: ProtocolVersion, SessionID: request.SessionID, Decision: "allow", StableErrorCode: "OK",
		VerifiedClaims: claims, EvidenceRequestDigest: request.EvidenceRequestDigest, AttestationKeyDigest: request.AttestationKeyDigest,
		PolicyID: request.PolicyID, PolicyDigest: request.PolicyDigest,
		IssuedAt: now.Add(-time.Second).Format("2006-01-02T15:04:05Z"), ExpiresAt: now.Add(time.Minute).Format("2006-01-02T15:04:05Z"),
	}
}

func TestVerifyNodeRejectsIncompleteRTMRSchema(t *testing.T) {
	now := time.Date(2026, 7, 28, 1, 0, 0, 0, time.UTC)
	input := validInput(t)
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		var parsed verifyRequest
		_ = json.NewDecoder(request.Body).Decode(&parsed)
		response := validResponse(parsed, now)
		delete(response.VerifiedClaims.RTMR, "3")
		_ = json.NewEncoder(writer).Encode(response)
	}))
	defer server.Close()
	client := &Client{httpClient: server.Client(), endpoint: server.URL, maxResponseBytes: 1 << 20, now: func() time.Time { return now }}
	if _, err := client.VerifyNode(context.Background(), input); err == nil {
		t.Fatal("incomplete RTMR object was accepted")
	}
}

func TestVerifyNodeRetriesSameRequestOnTransientFailure(t *testing.T) {
	now := time.Date(2026, 7, 28, 1, 0, 0, 0, time.UTC)
	input := validInput(t)
	var bodies [][]byte
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		contents, _ := io.ReadAll(request.Body)
		bodies = append(bodies, contents)
		if len(bodies) == 1 {
			writer.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		var parsed verifyRequest
		_ = json.Unmarshal(contents, &parsed)
		_ = json.NewEncoder(writer).Encode(validResponse(parsed, now))
	}))
	defer server.Close()
	client := &Client{
		httpClient: server.Client(), endpoint: server.URL, maxResponseBytes: 1 << 20,
		maxAttempts: 2, now: func() time.Time { return now },
	}
	if _, err := client.VerifyNode(context.Background(), input); err != nil {
		t.Fatal(err)
	}
	if len(bodies) != 2 || !bytes.Equal(bodies[0], bodies[1]) {
		t.Fatal("Trustee retry did not reuse the exact request body")
	}
}
