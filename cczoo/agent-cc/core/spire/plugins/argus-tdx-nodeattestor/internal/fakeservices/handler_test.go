package fakeservices

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/internal/protocol"
)

func TestEvidenceAndTrusteeRoundTrip(t *testing.T) {
	handler := newTestHandler(t)
	requestBody, requestDigest, keyDigest := testEvidenceRequest(t)
	evidence := requestEvidence(t, handler, requestBody)
	response := requestVerification(t, handler, verifyRequest{
		ProtocolVersion: trusteeProtocolVersion,
		SessionID:       base64.RawURLEncoding.EncodeToString(bytes.Repeat([]byte{1}, protocol.SessionIDSize)),
		Evidence:        evidence, EvidenceRequest: requestBody, EvidenceRequestDigest: requestDigest,
		AttestationKeyDigest: keyDigest, PolicyID: "openviking-prod-v1", PolicyDigest: testPolicyDigest(),
	})
	if response.Decision != "allow" || response.VerifiedClaims == nil {
		t.Fatalf("response = %#v", response)
	}
	if !response.VerifiedClaims.QuoteVerified || !response.VerifiedClaims.ReportDataVerified {
		t.Fatal("fake Trustee did not preserve verified claim semantics")
	}
	if response.VerifiedClaims.InstanceID != "tdvm-m3-0001" {
		t.Fatalf("instance ID = %q", response.VerifiedClaims.InstanceID)
	}
}

func TestWorkloadEvidenceAndTrusteeRoundTrip(t *testing.T) {
	handler := newTestHandler(t)
	evidenceRequest := workloadEvidenceRequest{
		ProtocolVersion: 1,
		Nonce:           "workload-nonce",
		PID:             4321,
	}
	evidenceBody, err := json.Marshal(evidenceRequest)
	if err != nil {
		t.Fatal(err)
	}
	evidenceHTTP := httptest.NewRequest(http.MethodPost, "/ra/v1/workload-evidence", bytes.NewReader(evidenceBody))
	evidenceHTTP.Header.Set("Content-Type", "application/json")
	evidenceRecorder := httptest.NewRecorder()
	handler.EvidenceHTTPHandler().ServeHTTP(evidenceRecorder, evidenceHTTP)
	if evidenceRecorder.Code != http.StatusOK {
		t.Fatalf("workload evidence status = %d, body = %s", evidenceRecorder.Code, evidenceRecorder.Body.String())
	}

	verifyBody, err := json.Marshal(workloadVerifyRequest{
		ProtocolVersion: 1,
		Nonce:           "workload-nonce",
		PID:             4321,
		Evidence:        evidenceRecorder.Body.Bytes(),
	})
	if err != nil {
		t.Fatal(err)
	}
	verifyHTTP := httptest.NewRequest(http.MethodPost, "/v1/verify/tdx-workload", bytes.NewReader(verifyBody))
	verifyHTTP.Header.Set("Content-Type", "application/json")
	verifyRecorder := httptest.NewRecorder()
	handler.TrusteeHTTPHandler().ServeHTTP(verifyRecorder, verifyHTTP)
	if verifyRecorder.Code != http.StatusOK {
		t.Fatalf("workload verification status = %d, body = %s", verifyRecorder.Code, verifyRecorder.Body.String())
	}
	var verdict workloadVerifyResponse
	if err := json.Unmarshal(verifyRecorder.Body.Bytes(), &verdict); err != nil {
		t.Fatal(err)
	}
	if verdict.Decision != "allow" || verdict.StableErrorCode != "OK" {
		t.Fatalf("workload verdict = %#v", verdict)
	}
	if verdict.PID != 4321 || verdict.Nonce != "workload-nonce" {
		t.Fatalf("workload verdict binding = %#v", verdict)
	}
	if verdict.WorkloadID != "openviking-cmem" || verdict.PolicyID != "openviking-cmem-v1" {
		t.Fatalf("workload verdict selectors = %#v", verdict)
	}
}

func TestWorkloadTrusteeDenyPreservesRequestBinding(t *testing.T) {
	handler := newTestHandler(t)
	handler.config.WorkloadDecision = "deny"
	requestBody, err := json.Marshal(workloadVerifyRequest{
		ProtocolVersion: 1,
		Nonce:           "workload-nonce",
		PID:             4321,
		Evidence:        json.RawMessage(`{"protocol_version":1,"nonce":"workload-nonce","pid":4321}`),
	})
	if err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest(http.MethodPost, "/v1/verify/tdx-workload", bytes.NewReader(requestBody))
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()
	handler.TrusteeHTTPHandler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", response.Code, response.Body.String())
	}
	var verdict workloadVerifyResponse
	if err := json.Unmarshal(response.Body.Bytes(), &verdict); err != nil {
		t.Fatal(err)
	}
	if verdict.Decision != "deny" || verdict.StableErrorCode != "POLICY_MISMATCH" {
		t.Fatalf("verdict = %#v", verdict)
	}
	if verdict.PID != 4321 || verdict.Nonce != "workload-nonce" {
		t.Fatalf("verdict binding = %#v", verdict)
	}
}

func TestTrusteeRejectsTamperedReportData(t *testing.T) {
	handler := newTestHandler(t)
	requestBody, requestDigest, keyDigest := testEvidenceRequest(t)
	evidence := requestEvidence(t, handler, requestBody)
	var document evidenceDocument
	if err := json.Unmarshal(evidence, &document); err != nil {
		t.Fatal(err)
	}
	document.Quote.ReportData = hex.EncodeToString(bytes.Repeat([]byte{0xff}, 64))
	evidence, _ = json.Marshal(document)
	status := verificationStatus(t, handler, verifyRequest{
		ProtocolVersion: trusteeProtocolVersion,
		SessionID:       base64.RawURLEncoding.EncodeToString(bytes.Repeat([]byte{1}, protocol.SessionIDSize)),
		Evidence:        evidence, EvidenceRequest: requestBody, EvidenceRequestDigest: requestDigest,
		AttestationKeyDigest: keyDigest, PolicyID: "openviking-prod-v1", PolicyDigest: testPolicyDigest(),
	})
	if status != http.StatusUnprocessableEntity {
		t.Fatalf("status = %d, want %d", status, http.StatusUnprocessableEntity)
	}
}

func TestTrusteeRejectsAttestationKeyTargetMismatch(t *testing.T) {
	handler := newTestHandler(t)
	requestBody, requestDigest, _ := testEvidenceRequest(t)
	evidence := requestEvidence(t, handler, requestBody)
	status := verificationStatus(t, handler, verifyRequest{
		ProtocolVersion: trusteeProtocolVersion,
		SessionID:       base64.RawURLEncoding.EncodeToString(bytes.Repeat([]byte{1}, protocol.SessionIDSize)),
		Evidence:        evidence, EvidenceRequest: requestBody, EvidenceRequestDigest: requestDigest,
		AttestationKeyDigest: "sha256:" + string(bytes.Repeat([]byte{'b'}, 64)),
		PolicyID:             "openviking-prod-v1", PolicyDigest: testPolicyDigest(),
	})
	if status != http.StatusUnprocessableEntity {
		t.Fatalf("status = %d, want %d", status, http.StatusUnprocessableEntity)
	}
}

func TestTrusteeAcceptsTwoConfiguredInstances(t *testing.T) {
	openclawProvider := newTestHandlerForInstance(t, "tdvm-openclaw-0001", nil)
	trustee := newTestHandlerForInstance(t, "tdvm-openclaw-0001", []string{
		"tdvm-openclaw-0001",
		"tdvm-openviking-0001",
	})
	requestBody, requestDigest, keyDigest := testEvidenceRequest(t)
	evidence := requestEvidence(t, openclawProvider, requestBody)
	response := requestVerification(t, trustee, verifyRequest{
		ProtocolVersion: trusteeProtocolVersion,
		SessionID:       base64.RawURLEncoding.EncodeToString(bytes.Repeat([]byte{1}, protocol.SessionIDSize)),
		Evidence:        evidence, EvidenceRequest: requestBody, EvidenceRequestDigest: requestDigest,
		AttestationKeyDigest: keyDigest, PolicyID: "dual-tdvm-v1", PolicyDigest: testPolicyDigest(),
	})
	if response.VerifiedClaims == nil || response.VerifiedClaims.InstanceID != "tdvm-openclaw-0001" {
		t.Fatalf("response = %#v", response)
	}

	openvikingProvider := newTestHandlerForInstance(t, "tdvm-openviking-0001", nil)
	evidence = requestEvidence(t, openvikingProvider, requestBody)
	response = requestVerification(t, trustee, verifyRequest{
		ProtocolVersion: trusteeProtocolVersion,
		SessionID:       base64.RawURLEncoding.EncodeToString(bytes.Repeat([]byte{2}, protocol.SessionIDSize)),
		Evidence:        evidence, EvidenceRequest: requestBody, EvidenceRequestDigest: requestDigest,
		AttestationKeyDigest: keyDigest, PolicyID: "dual-tdvm-v1", PolicyDigest: testPolicyDigest(),
	})
	if response.VerifiedClaims == nil || response.VerifiedClaims.InstanceID != "tdvm-openviking-0001" {
		t.Fatalf("response = %#v", response)
	}
}

func TestTrusteeRejectsUnconfiguredInstance(t *testing.T) {
	provider := newTestHandlerForInstance(t, "tdvm-other-0001", nil)
	trustee := newTestHandlerForInstance(t, "tdvm-openclaw-0001", []string{
		"tdvm-openclaw-0001",
		"tdvm-openviking-0001",
	})
	requestBody, requestDigest, keyDigest := testEvidenceRequest(t)
	evidence := requestEvidence(t, provider, requestBody)
	status := verificationStatus(t, trustee, verifyRequest{
		ProtocolVersion: trusteeProtocolVersion,
		SessionID:       base64.RawURLEncoding.EncodeToString(bytes.Repeat([]byte{3}, protocol.SessionIDSize)),
		Evidence:        evidence, EvidenceRequest: requestBody, EvidenceRequestDigest: requestDigest,
		AttestationKeyDigest: keyDigest, PolicyID: "dual-tdvm-v1", PolicyDigest: testPolicyDigest(),
	})
	if status != http.StatusUnprocessableEntity {
		t.Fatalf("status = %d, want %d", status, http.StatusUnprocessableEntity)
	}
}

func newTestHandler(t *testing.T) *Handler {
	return newTestHandlerForInstance(t, "tdvm-m3-0001", nil)
}

func newTestHandlerForInstance(t *testing.T, instanceID string, allowedInstanceIDs []string) *Handler {
	t.Helper()
	rtmr0 := "0011"
	handler, err := NewHandler(Config{
		InstanceID: instanceID, AllowedInstanceIDs: allowedInstanceIDs,
		WorkloadID: "openviking-cmem", WorkloadPolicyID: "openviking-cmem-v1", WorkloadDecision: "allow",
		TCBStatus: "up_to_date", MRTD: "aabb",
		RTMR: map[string]*string{"0": &rtmr0, "1": nil, "2": nil, "3": nil},
		Now:  func() time.Time { return time.Date(2026, 7, 28, 1, 0, 0, 0, time.UTC) },
	})
	if err != nil {
		t.Fatal(err)
	}
	return handler
}

func testEvidenceRequest(t *testing.T) ([]byte, string, string) {
	t.Helper()
	publicKey := bytes.Repeat([]byte{2}, protocol.PublicKeySize)
	keyHash := sha256.Sum256(publicKey)
	keyID := hex.EncodeToString(keyHash[:])
	requestBody, err := json.Marshal(protocol.EvidenceRequest{
		Version: "v1", Nonce: base64.RawURLEncoding.EncodeToString(bytes.Repeat([]byte{3}, protocol.NonceSize)),
		CallerID:        "spiffe://argus.local/spire/server",
		Target:          protocol.TargetService{ServiceName: "argus-tdx-node", TargetURI: "argus-node:" + keyID},
		RequestedClaims: []string{"TeeQuote", "IdentityClaims"}, ProfileDigest: testPolicyDigest(),
	})
	if err != nil {
		t.Fatal(err)
	}
	requestDigest, err := protocol.EvidenceRequestDigest(requestBody)
	if err != nil {
		t.Fatal(err)
	}
	return requestBody, requestDigest, "sha256:" + keyID
}

func testPolicyDigest() string {
	return "sha256:" + string(bytes.Repeat([]byte{'c'}, 64))
}

func requestEvidence(t *testing.T, handler http.Handler, body []byte) []byte {
	t.Helper()
	request := httptest.NewRequest(http.MethodPost, "/ra/v1/evidence", bytes.NewReader(body))
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("evidence status = %d, body = %s", response.Code, response.Body.String())
	}
	return response.Body.Bytes()
}

func requestVerification(t *testing.T, handler http.Handler, input verifyRequest) verifyResponse {
	t.Helper()
	body, _ := json.Marshal(input)
	request := httptest.NewRequest(http.MethodPost, "/v1/verify/tdx-node", bytes.NewReader(body))
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("verification status = %d, body = %s", response.Code, response.Body.String())
	}
	var parsed verifyResponse
	if err := json.Unmarshal(response.Body.Bytes(), &parsed); err != nil {
		t.Fatal(err)
	}
	return parsed
}

func verificationStatus(t *testing.T, handler http.Handler, input verifyRequest) int {
	t.Helper()
	body, _ := json.Marshal(input)
	request := httptest.NewRequest(http.MethodPost, "/v1/verify/tdx-node", bytes.NewReader(body))
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	return response.Code
}

func TestReplayEvidenceIsRejectedForFreshRequest(t *testing.T) {
	handler := newTestHandler(t)
	handler.config.ReplayEvidence = true
	firstRequest, _, _ := testEvidenceRequest(t)
	firstEvidence := requestEvidence(t, handler, firstRequest)

	var second protocol.EvidenceRequest
	if err := json.Unmarshal(firstRequest, &second); err != nil {
		t.Fatal(err)
	}
	second.Nonce = base64.RawURLEncoding.EncodeToString(bytes.Repeat([]byte{4}, protocol.NonceSize))
	secondRequest, err := json.Marshal(second)
	if err != nil {
		t.Fatal(err)
	}
	secondDigest, err := protocol.EvidenceRequestDigest(secondRequest)
	if err != nil {
		t.Fatal(err)
	}
	replayedEvidence := requestEvidence(t, handler, secondRequest)
	if !bytes.Equal(firstEvidence, replayedEvidence) {
		t.Fatal("replay mode generated fresh evidence")
	}
	_, _, keyDigest := testEvidenceRequest(t)
	status := verificationStatus(t, handler, verifyRequest{
		ProtocolVersion: trusteeProtocolVersion,
		SessionID:       base64.RawURLEncoding.EncodeToString(bytes.Repeat([]byte{5}, protocol.SessionIDSize)),
		Evidence:        replayedEvidence, EvidenceRequest: secondRequest, EvidenceRequestDigest: secondDigest,
		AttestationKeyDigest: keyDigest, PolicyID: "openviking-prod-v1", PolicyDigest: testPolicyDigest(),
	})
	if status != http.StatusUnprocessableEntity {
		t.Fatalf("replayed evidence status = %d, want %d", status, http.StatusUnprocessableEntity)
	}

	request := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	metrics := response.Body.String()
	for _, expected := range []string{
		`argus_m4_fake_requests_total{service="evidence",result="ok"} 1`,
		`argus_m4_fake_requests_total{service="evidence",result="replay"} 1`,
		`argus_m4_fake_requests_total{service="trustee",result="denied"} 1`,
	} {
		if !strings.Contains(metrics, expected) {
			t.Fatalf("metrics missing %q:\n%s", expected, metrics)
		}
	}
}
