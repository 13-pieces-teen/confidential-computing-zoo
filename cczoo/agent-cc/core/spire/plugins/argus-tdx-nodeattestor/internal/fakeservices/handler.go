package fakeservices

import (
	"bytes"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/internal/protocol"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/internal/trustee"
)

const trusteeProtocolVersion = 1

// Config controls deterministic mock claims and injected failure modes. These
// services exercise protocol binding and fail-closed behavior; they do not
// collect or cryptographically verify a hardware TDX quote.
type Config struct {
	InstanceID         string
	AllowedInstanceIDs []string
	WorkloadID         string
	WorkloadPolicyID   string
	WorkloadDecision   string
	TCBStatus          string
	MRTD               string
	RTMR               map[string]*string
	DebugEnabled       bool
	ReplayEvidence     bool
	EvidenceStatus     int
	EvidenceDelay      time.Duration
	TrusteeStatus      int
	TrusteeDelay       time.Duration
	Now                func() time.Time
}

type Handler struct {
	config           Config
	mu               sync.Mutex
	replayedEvidence []byte
	counters         map[counterKey]uint64
}

type counterKey struct {
	service string
	result  string
}

type evidenceDocument struct {
	ProtocolVersion int                    `json:"protocol_version"`
	BindingClaims   protocol.BindingClaims `json:"binding_claims"`
	Quote           fakeQuote              `json:"quote"`
}

type fakeQuote struct {
	Type         string             `json:"type"`
	ReportData   string             `json:"report_data"`
	TCBStatus    string             `json:"tcb_status"`
	MRTD         string             `json:"mrtd"`
	RTMR         map[string]*string `json:"rtmr"`
	DebugEnabled bool               `json:"debug_enabled"`
}

type verifyRequest struct {
	ProtocolVersion       int             `json:"protocol_version"`
	SessionID             string          `json:"session_id"`
	Evidence              json.RawMessage `json:"evidence"`
	EvidenceRequest       json.RawMessage `json:"evidence_request"`
	EvidenceRequestDigest string          `json:"evidence_request_digest"`
	AttestationKeyDigest  string          `json:"attestation_key_digest"`
	PolicyID              string          `json:"policy_id"`
	PolicyDigest          string          `json:"policy_digest"`
}

type verifyResponse struct {
	ProtocolVersion       int                         `json:"protocol_version"`
	SessionID             string                      `json:"session_id"`
	Decision              string                      `json:"decision"`
	StableErrorCode       string                      `json:"stable_error_code"`
	VerifiedClaims        *trustee.VerifiedNodeClaims `json:"verified_claims"`
	EvidenceRequestDigest string                      `json:"evidence_request_digest"`
	AttestationKeyDigest  string                      `json:"attestation_key_digest"`
	PolicyID              string                      `json:"policy_id"`
	PolicyDigest          string                      `json:"policy_digest"`
	IssuedAt              string                      `json:"issued_at"`
	ExpiresAt             string                      `json:"expires_at"`
}

type workloadEvidenceRequest struct {
	ProtocolVersion int    `json:"protocol_version"`
	Nonce           string `json:"nonce"`
	PID             int32  `json:"pid"`
}

type workloadEvidenceDocument struct {
	ProtocolVersion   int               `json:"protocol_version"`
	Nonce             string            `json:"nonce"`
	PID               int32             `json:"pid"`
	WorkloadID        string            `json:"workload_id"`
	PolicyID          string            `json:"policy_id"`
	LaunchID          string            `json:"launch_id"`
	ContainerID       string            `json:"container_id"`
	ProcessStartTime  string            `json:"process_start_time"`
	ImageConfigDigest string            `json:"image_config_digest"`
	RekorUUID         string            `json:"rekor_uuid"`
	Quote             workloadFakeQuote `json:"quote"`
}

type workloadFakeQuote struct {
	Format string `json:"format"`
	Body   string `json:"body"`
}

type workloadVerifyRequest struct {
	ProtocolVersion int             `json:"protocol_version"`
	Nonce           string          `json:"nonce"`
	PID             int32           `json:"pid"`
	Evidence        json.RawMessage `json:"evidence"`
}

type workloadVerifyResponse struct {
	ProtocolVersion int    `json:"protocol_version"`
	Nonce           string `json:"nonce"`
	PID             int32  `json:"pid"`
	Decision        string `json:"decision"`
	StableErrorCode string `json:"stable_error_code"`
	WorkloadID      string `json:"workload_id"`
	PolicyID        string `json:"policy_id"`
}

func NewHandler(config Config) (*Handler, error) {
	if config.InstanceID == "" || config.TCBStatus == "" || config.MRTD == "" {
		return nil, fmt.Errorf("instance ID, TCB status, and MRTD are required")
	}
	if len(config.RTMR) != 4 {
		return nil, fmt.Errorf("RTMR map must contain indexes 0 through 3")
	}
	for _, index := range []string{"0", "1", "2", "3"} {
		if _, ok := config.RTMR[index]; !ok {
			return nil, fmt.Errorf("RTMR map is missing index %s", index)
		}
	}
	if config.Now == nil {
		config.Now = time.Now
	}
	if len(config.AllowedInstanceIDs) == 0 {
		config.AllowedInstanceIDs = []string{config.InstanceID}
	}
	for _, instanceID := range config.AllowedInstanceIDs {
		if instanceID == "" {
			return nil, fmt.Errorf("allowed instance ID must not be empty")
		}
	}
	if config.WorkloadID == "" || config.WorkloadPolicyID == "" {
		return nil, fmt.Errorf("workload ID and workload policy ID are required")
	}
	if config.WorkloadDecision != "allow" && config.WorkloadDecision != "deny" {
		return nil, fmt.Errorf("workload decision must be allow or deny")
	}
	return &Handler{config: config, counters: make(map[counterKey]uint64)}, nil
}

// ServeHTTP exposes the combined mock Evidence Provider and Trustee used by
// single-process integration tests. Split test deployments use the narrower
// handlers below to preserve the Evidence Provider/Trustee role separation.
func (handler *Handler) ServeHTTP(writer http.ResponseWriter, request *http.Request) {
	switch request.URL.Path {
	case "/ra/v1/evidence":
		handler.handleEvidence(writer, request)
	case "/ra/v1/workload-evidence":
		handler.handleWorkloadEvidence(writer, request)
	case "/v1/verify/tdx-node":
		handler.handleVerify(writer, request)
	case "/v1/verify/tdx-workload":
		handler.handleWorkloadVerify(writer, request)
	case "/healthz":
		writer.WriteHeader(http.StatusNoContent)
	case "/metrics":
		handler.handleMetrics(writer, request)
	default:
		http.NotFound(writer, request)
	}
}

// EvidenceHTTPHandler exposes only the Evidence Provider API. It is used by
// the split v2 deployment so the service-side process cannot accidentally
// serve Trustee verification requests.
func (handler *Handler) EvidenceHTTPHandler() http.Handler {
	return http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		switch request.URL.Path {
		case "/ra/v1/evidence":
			handler.handleEvidence(writer, request)
		case "/ra/v1/workload-evidence":
			handler.handleWorkloadEvidence(writer, request)
		case "/healthz":
			writer.WriteHeader(http.StatusNoContent)
		case "/metrics":
			handler.handleMetrics(writer, request)
		default:
			http.NotFound(writer, request)
		}
	})
}

// TrusteeHTTPHandler exposes only the Trustee API. It is used by the split v2
// deployment so the center-side process cannot act as an Evidence Provider.
func (handler *Handler) TrusteeHTTPHandler() http.Handler {
	return http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		switch request.URL.Path {
		case "/v1/verify/tdx-node":
			handler.handleVerify(writer, request)
		case "/v1/verify/tdx-workload":
			handler.handleWorkloadVerify(writer, request)
		case "/healthz":
			writer.WriteHeader(http.StatusNoContent)
		case "/metrics":
			handler.handleMetrics(writer, request)
		default:
			http.NotFound(writer, request)
		}
	})
}

func (handler *Handler) handleWorkloadEvidence(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		handler.record("workload_evidence", "error")
		writeError(writer, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED")
		return
	}
	if handler.config.EvidenceDelay > 0 {
		timer := time.NewTimer(handler.config.EvidenceDelay)
		defer timer.Stop()
		select {
		case <-request.Context().Done():
			handler.record("workload_evidence", "timeout")
			return
		case <-timer.C:
		}
	}
	if handler.config.EvidenceStatus != 0 {
		handler.record("workload_evidence", "error")
		writeError(writer, handler.config.EvidenceStatus, "EVIDENCE_PROVIDER_FAULT")
		return
	}
	var input workloadEvidenceRequest
	if err := decodeJSON(request, &input); err != nil || input.ProtocolVersion != trusteeProtocolVersion || input.Nonce == "" || input.PID <= 0 {
		handler.record("workload_evidence", "error")
		writeError(writer, http.StatusBadRequest, "INVALID_WORKLOAD_EVIDENCE_REQUEST")
		return
	}
	// The workload document is synthetic and deterministic apart from the clock;
	// it exists only to exercise the WorkloadAttestor protocol.
	now := handler.config.Now().UTC().Truncate(time.Second)
	document := workloadEvidenceDocument{
		ProtocolVersion:   trusteeProtocolVersion,
		Nonce:             input.Nonce,
		PID:               input.PID,
		WorkloadID:        handler.config.WorkloadID,
		PolicyID:          handler.config.WorkloadPolicyID,
		LaunchID:          fmt.Sprintf("mock-launch-%d", input.PID),
		ContainerID:       fmt.Sprintf("mock-container-%d", input.PID),
		ProcessStartTime:  now.Format(time.RFC3339),
		ImageConfigDigest: "sha256:" + strings.Repeat("a", 64),
		RekorUUID:         fmt.Sprintf("mock-rekor-%d", input.PID),
		Quote:             workloadFakeQuote{Format: "mock-tdx", Body: "mock-quote"},
	}
	handler.record("workload_evidence", "ok")
	writeJSON(writer, http.StatusOK, document)
}

func (handler *Handler) handleWorkloadVerify(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		handler.record("workload_trustee", "error")
		writeError(writer, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED")
		return
	}
	if handler.config.TrusteeDelay > 0 {
		timer := time.NewTimer(handler.config.TrusteeDelay)
		defer timer.Stop()
		select {
		case <-request.Context().Done():
			handler.record("workload_trustee", "timeout")
			return
		case <-timer.C:
		}
	}
	if handler.config.TrusteeStatus != 0 {
		handler.record("workload_trustee", "error")
		writeError(writer, handler.config.TrusteeStatus, "TRUSTEE_FAULT")
		return
	}
	var input workloadVerifyRequest
	if err := decodeJSON(request, &input); err != nil || input.ProtocolVersion != trusteeProtocolVersion || input.Nonce == "" || input.PID <= 0 {
		handler.record("workload_trustee", "error")
		writeError(writer, http.StatusBadRequest, "INVALID_WORKLOAD_VERIFY_REQUEST")
		return
	}
	var evidence workloadEvidenceDocument
	if err := decodeBytes(input.Evidence, &evidence); err != nil || evidence.ProtocolVersion != trusteeProtocolVersion || evidence.Nonce != input.Nonce || evidence.PID != input.PID {
		handler.record("workload_trustee", "denied")
		writeError(writer, http.StatusUnprocessableEntity, "WORKLOAD_EVIDENCE_REJECTED")
		return
	}
	response := workloadVerifyResponse{
		ProtocolVersion: trusteeProtocolVersion,
		Nonce:           input.Nonce,
		PID:             input.PID,
		Decision:        handler.config.WorkloadDecision,
		StableErrorCode: "POLICY_MISMATCH",
	}
	if handler.config.WorkloadDecision == "allow" {
		if evidence.WorkloadID != handler.config.WorkloadID || evidence.PolicyID != handler.config.WorkloadPolicyID {
			handler.record("workload_trustee", "denied")
			writeError(writer, http.StatusUnprocessableEntity, "WORKLOAD_EVIDENCE_REJECTED")
			return
		}
		response.StableErrorCode = "OK"
		response.WorkloadID = handler.config.WorkloadID
		response.PolicyID = handler.config.WorkloadPolicyID
		handler.record("workload_trustee", "ok")
	} else {
		handler.record("workload_trustee", "denied")
	}
	writeJSON(writer, http.StatusOK, response)
}

func (handler *Handler) handleEvidence(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		handler.record("evidence", "error")
		writeError(writer, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED")
		return
	}
	if handler.config.EvidenceDelay > 0 {
		timer := time.NewTimer(handler.config.EvidenceDelay)
		defer timer.Stop()
		select {
		case <-request.Context().Done():
			handler.record("evidence", "timeout")
			return
		case <-timer.C:
		}
	}
	if handler.config.EvidenceStatus != 0 {
		handler.record("evidence", "error")
		writeError(writer, handler.config.EvidenceStatus, "EVIDENCE_PROVIDER_FAULT")
		return
	}
	// Replay mode deliberately reuses the first response so later challenges
	// fail their request/REPORTDATA binding checks.
	if evidence := handler.cachedEvidence(); evidence != nil {
		handler.record("evidence", "replay")
		writeBytes(writer, http.StatusOK, evidence)
		return
	}
	var requestBody json.RawMessage
	if err := decodeJSON(request, &requestBody); err != nil {
		writeError(writer, http.StatusBadRequest, "INVALID_EVIDENCE_REQUEST")
		return
	}
	canonicalRequest, _, err := protocol.CanonicalEvidenceRequest(requestBody)
	if err != nil {
		writeError(writer, http.StatusBadRequest, "INVALID_EVIDENCE_REQUEST")
		return
	}
	claims := handler.bindingClaims()
	claimsJSON, err := json.Marshal(claims)
	if err != nil {
		writeError(writer, http.StatusInternalServerError, "INTERNAL_ERROR")
		return
	}
	reportData, err := protocol.BindingReportData(canonicalRequest, claimsJSON)
	if err != nil {
		writeError(writer, http.StatusInternalServerError, "INTERNAL_ERROR")
		return
	}
	// fakeQuote mirrors the fields consumed by the mock Trustee but carries no
	// hardware signature or collateral.
	document, err := json.Marshal(evidenceDocument{
		ProtocolVersion: trusteeProtocolVersion,
		BindingClaims:   claims,
		Quote: fakeQuote{
			Type: "tdx", ReportData: hex.EncodeToString(reportData[:]),
			TCBStatus: handler.config.TCBStatus, MRTD: handler.config.MRTD,
			RTMR: cloneRTMR(handler.config.RTMR), DebugEnabled: handler.config.DebugEnabled,
		},
	})
	if err != nil {
		handler.record("evidence", "error")
		writeError(writer, http.StatusInternalServerError, "INTERNAL_ERROR")
		return
	}
	handler.cacheEvidence(document)
	handler.record("evidence", "ok")
	writeBytes(writer, http.StatusOK, document)
}

func (handler *Handler) handleVerify(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		handler.record("trustee", "error")
		writeError(writer, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED")
		return
	}
	if handler.config.TrusteeDelay > 0 {
		timer := time.NewTimer(handler.config.TrusteeDelay)
		defer timer.Stop()
		select {
		case <-request.Context().Done():
			handler.record("trustee", "timeout")
			return
		case <-timer.C:
		}
	}
	if handler.config.TrusteeStatus != 0 {
		handler.record("trustee", "error")
		writeError(writer, handler.config.TrusteeStatus, "TRUSTEE_FAULT")
		return
	}
	var input verifyRequest
	if err := decodeJSON(request, &input); err != nil {
		writeError(writer, http.StatusBadRequest, "INVALID_VERIFY_REQUEST")
		return
	}
	claims, err := handler.verify(input)
	if err != nil {
		handler.record("trustee", "denied")
		writeError(writer, http.StatusUnprocessableEntity, "EVIDENCE_REJECTED")
		return
	}
	handler.record("trustee", "ok")
	now := handler.config.Now().UTC().Truncate(time.Second)
	issuedAt := now.Add(-time.Second).Format(time.RFC3339)
	expiresAt := now.Add(time.Minute).Format(time.RFC3339)
	claims.VerifiedAt = issuedAt
	claims.ExpiresAt = expiresAt
	writeJSON(writer, http.StatusOK, verifyResponse{
		ProtocolVersion: trusteeProtocolVersion, SessionID: input.SessionID,
		Decision: "allow", StableErrorCode: "OK", VerifiedClaims: &claims,
		EvidenceRequestDigest: input.EvidenceRequestDigest, AttestationKeyDigest: input.AttestationKeyDigest,
		PolicyID: input.PolicyID, PolicyDigest: input.PolicyDigest, IssuedAt: issuedAt, ExpiresAt: expiresAt,
	})
}

func (handler *Handler) verify(input verifyRequest) (trustee.VerifiedNodeClaims, error) {
	// This mock verifier checks protocol and REPORTDATA relationships only. Its
	// QuoteVerified result must not be interpreted as real TDX verification.
	if input.ProtocolVersion != trusteeProtocolVersion || input.PolicyID == "" || input.PolicyDigest == "" {
		return trustee.VerifiedNodeClaims{}, fmt.Errorf("invalid protocol or policy")
	}
	sessionID, err := base64.RawURLEncoding.DecodeString(input.SessionID)
	if err != nil || len(sessionID) != protocol.SessionIDSize {
		return trustee.VerifiedNodeClaims{}, fmt.Errorf("invalid session ID")
	}
	canonicalRequest, parsedRequest, err := protocol.CanonicalEvidenceRequest(input.EvidenceRequest)
	if err != nil {
		return trustee.VerifiedNodeClaims{}, err
	}
	requestDigest, err := protocol.EvidenceRequestDigest(canonicalRequest)
	if err != nil || requestDigest != input.EvidenceRequestDigest {
		return trustee.VerifiedNodeClaims{}, fmt.Errorf("evidence request digest mismatch")
	}
	if parsedRequest.ProfileDigest != input.PolicyDigest {
		return trustee.VerifiedNodeClaims{}, fmt.Errorf("policy digest mismatch")
	}
	keyID, ok := strings.CutPrefix(input.AttestationKeyDigest, "sha256:")
	if !ok || parsedRequest.Target.TargetURI != "argus-node:"+keyID {
		return trustee.VerifiedNodeClaims{}, fmt.Errorf("attestation key target mismatch")
	}
	var evidence evidenceDocument
	if err := decodeBytes(input.Evidence, &evidence); err != nil || evidence.ProtocolVersion != trusteeProtocolVersion || evidence.Quote.Type != "tdx" {
		return trustee.VerifiedNodeClaims{}, fmt.Errorf("invalid evidence document")
	}
	claimsJSON, err := json.Marshal(evidence.BindingClaims)
	if err != nil {
		return trustee.VerifiedNodeClaims{}, err
	}
	canonicalClaims, claims, err := protocol.CanonicalBindingClaims(claimsJSON)
	if err != nil {
		return trustee.VerifiedNodeClaims{}, err
	}
	reportData, err := protocol.BindingReportData(canonicalRequest, canonicalClaims)
	if err != nil || !strings.EqualFold(evidence.Quote.ReportData, hex.EncodeToString(reportData[:])) {
		return trustee.VerifiedNodeClaims{}, fmt.Errorf("report data mismatch")
	}
	if !handler.allowsInstance(claims.ServiceIdentity.InstanceID) {
		return trustee.VerifiedNodeClaims{}, fmt.Errorf("instance ID mismatch")
	}
	return trustee.VerifiedNodeClaims{
		QuoteVerified: true, ReportDataVerified: true,
		TCBStatus: evidence.Quote.TCBStatus, MRTD: evidence.Quote.MRTD,
		RTMR: cloneRTMR(evidence.Quote.RTMR), DebugEnabled: evidence.Quote.DebugEnabled,
		InstanceID: claims.ServiceIdentity.InstanceID, PolicyID: input.PolicyID, PolicyDigest: input.PolicyDigest,
		AttestationKeyDigest: input.AttestationKeyDigest, EvidenceRequestDigest: input.EvidenceRequestDigest,
	}, nil
}

func (handler *Handler) allowsInstance(instanceID string) bool {
	for _, allowed := range handler.config.AllowedInstanceIDs {
		if instanceID == allowed {
			return true
		}
	}
	return false
}

func (handler *Handler) cachedEvidence() []byte {
	if !handler.config.ReplayEvidence {
		return nil
	}
	handler.mu.Lock()
	defer handler.mu.Unlock()
	return append([]byte(nil), handler.replayedEvidence...)
}

func (handler *Handler) cacheEvidence(evidence []byte) {
	if !handler.config.ReplayEvidence {
		return
	}
	handler.mu.Lock()
	defer handler.mu.Unlock()
	if handler.replayedEvidence == nil {
		handler.replayedEvidence = append([]byte(nil), evidence...)
	}
}

func (handler *Handler) record(service, result string) {
	handler.mu.Lock()
	defer handler.mu.Unlock()
	handler.counters[counterKey{service: service, result: result}]++
}

func (handler *Handler) handleMetrics(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodGet {
		writeError(writer, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED")
		return
	}
	handler.mu.Lock()
	defer handler.mu.Unlock()
	writer.Header().Set("Content-Type", "text/plain; version=0.0.4")
	for key, value := range handler.counters {
		_, _ = fmt.Fprintf(writer, "argus_m4_fake_requests_total{service=%q,result=%q} %d\n", key.service, key.result, value)
	}
}

func (handler *Handler) bindingClaims() protocol.BindingClaims {
	instanceID := handler.config.InstanceID
	cgroupPath := "/system.slice/spire-agent.service"
	return protocol.BindingClaims{
		AssuranceLevel: "L2",
		ServiceIdentity: protocol.ServiceIdentity{
			ServiceName: "argus-tdx-node", InstanceID: instanceID, InstanceScope: "vm",
		},
		RuntimeBinding: protocol.RuntimeBinding{
			Endpoint: "fake://argus-evidence", OwningPID: 1,
			ProcessStartTime: "2026-01-01T00:00:00Z", VMInstanceID: &instanceID, CgroupPath: &cgroupPath,
		},
		ClaimSupport: map[string][]string{
			"instance_id": {"quote", "runtime"}, "service_name": {"config"},
		},
		VerifierValidatedSupport: nil,
		ProviderClaimAssurance: map[string]string{
			"instance_id": "L2", "service_name": "L1",
		},
	}
}

func decodeJSON(request *http.Request, target any) error {
	if request.Header.Get("Content-Type") != "application/json" {
		return fmt.Errorf("content type must be application/json")
	}
	decoder := json.NewDecoder(http.MaxBytesReader(nil, request.Body, protocol.MaxEvidenceSize))
	decoder.DisallowUnknownFields()
	return decoder.Decode(target)
}

func decodeBytes(input []byte, target any) error {
	decoder := json.NewDecoder(bytes.NewReader(input))
	decoder.DisallowUnknownFields()
	return decoder.Decode(target)
}

func cloneRTMR(input map[string]*string) map[string]*string {
	output := make(map[string]*string, len(input))
	for index, measurement := range input {
		if measurement == nil {
			output[index] = nil
			continue
		}
		value := *measurement
		output[index] = &value
	}
	return output
}

func writeBytes(writer http.ResponseWriter, status int, value []byte) {
	writer.Header().Set("Content-Type", "application/json")
	writer.WriteHeader(status)
	_, _ = writer.Write(value)
}

func writeJSON(writer http.ResponseWriter, status int, value any) {
	writer.Header().Set("Content-Type", "application/json")
	writer.WriteHeader(status)
	_ = json.NewEncoder(writer).Encode(value)
}

func writeError(writer http.ResponseWriter, status int, code string) {
	writeJSON(writer, status, map[string]string{"error": code})
}
