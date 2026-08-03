package trustee

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"time"

	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/internal/policy"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/internal/protocol"
)

const ProtocolVersion = 1

var (
	digestPattern      = regexp.MustCompile(`^[a-z0-9]+:[0-9a-f]+$`)
	measurementPattern = regexp.MustCompile(`^[0-9a-f]+$`)
	identifierPattern  = regexp.MustCompile(`^[\x21-\x7e]+$`)
)

type VerifyInput struct {
	SessionID           []byte
	EvidenceJSON        []byte
	EvidenceRequestJSON []byte
	AttestationKey      []byte
	Policy              *policy.Policy
}

type VerifiedNodeClaims struct {
	QuoteVerified         bool               `json:"quote_verified"`
	ReportDataVerified    bool               `json:"report_data_verified"`
	TCBStatus             string             `json:"tcb_status"`
	MRTD                  string             `json:"mrtd"`
	RTMR                  map[string]*string `json:"rtmr"`
	DebugEnabled          bool               `json:"debug_enabled"`
	InstanceID            string             `json:"instance_id"`
	LaunchID              *string            `json:"launch_id"`
	PolicyID              string             `json:"policy_id"`
	PolicyDigest          string             `json:"policy_digest"`
	AttestationKeyDigest  string             `json:"attestation_key_digest"`
	EvidenceRequestDigest string             `json:"evidence_request_digest"`
	VerifiedAt            string             `json:"verified_at"`
	ExpiresAt             string             `json:"expires_at"`
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
	ProtocolVersion       int                 `json:"protocol_version"`
	SessionID             string              `json:"session_id"`
	Decision              string              `json:"decision"`
	StableErrorCode       string              `json:"stable_error_code"`
	VerifiedClaims        *VerifiedNodeClaims `json:"verified_claims"`
	EvidenceRequestDigest string              `json:"evidence_request_digest"`
	AttestationKeyDigest  string              `json:"attestation_key_digest"`
	PolicyID              string              `json:"policy_id"`
	PolicyDigest          string              `json:"policy_digest"`
	IssuedAt              string              `json:"issued_at"`
	ExpiresAt             string              `json:"expires_at"`
}

type Client struct {
	httpClient       *http.Client
	endpoint         string
	expectedSPIFFEID string
	maxResponseBytes int64
	maxAttempts      int
	now              func() time.Time
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
		expectedSPIFFEID: expectedSPIFFEID,
		maxResponseBytes: maxResponseBytes,
		maxAttempts:      2,
		now:              time.Now,
	}, nil
}

func (client *Client) VerifyNode(ctx context.Context, input VerifyInput) (VerifiedNodeClaims, error) {
	request, err := buildRequest(input)
	if err != nil {
		return VerifiedNodeClaims{}, err
	}
	requestBody, err := json.Marshal(request)
	if err != nil {
		return VerifiedNodeClaims{}, fmt.Errorf("marshal Trustee request: %w", err)
	}
	response, err := client.doRequest(ctx, requestBody)
	if err != nil {
		return VerifiedNodeClaims{}, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
		return VerifiedNodeClaims{}, fmt.Errorf("Trustee returned HTTP %d", response.StatusCode)
	}
	contents, err := io.ReadAll(io.LimitReader(response.Body, client.maxResponseBytes+1))
	if err != nil {
		return VerifiedNodeClaims{}, fmt.Errorf("read Trustee response: %w", err)
	}
	if int64(len(contents)) > client.maxResponseBytes {
		return VerifiedNodeClaims{}, fmt.Errorf("Trustee response exceeds %d bytes", client.maxResponseBytes)
	}
	parsed, err := parseResponse(contents)
	if err != nil {
		return VerifiedNodeClaims{}, err
	}
	if err := validateResponse(parsed, request, input.Policy, client.now()); err != nil {
		return VerifiedNodeClaims{}, err
	}
	return *parsed.VerifiedClaims, nil
}

func (client *Client) doRequest(ctx context.Context, requestBody []byte) (*http.Response, error) {
	attempts := client.maxAttempts
	if attempts <= 0 {
		attempts = 1
	}
	var lastErr error
	for attempt := 0; attempt < attempts; attempt++ {
		httpRequest, err := http.NewRequestWithContext(ctx, http.MethodPost, client.endpoint, bytes.NewReader(requestBody))
		if err != nil {
			return nil, fmt.Errorf("create Trustee request: %w", err)
		}
		httpRequest.Header.Set("Content-Type", "application/json")
		httpRequest.Header.Set("Accept", "application/json")
		response, err := client.httpClient.Do(httpRequest)
		if err != nil {
			lastErr = err
			if ctx.Err() != nil {
				break
			}
			continue
		}
		retryable := response.StatusCode == http.StatusTooManyRequests || response.StatusCode >= 500
		if !retryable || attempt == attempts-1 {
			return response, nil
		}
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
		_ = response.Body.Close()
	}
	if ctx.Err() != nil {
		return nil, fmt.Errorf("call Trustee: %w", ctx.Err())
	}
	return nil, fmt.Errorf("call Trustee: %w", lastErr)
}

func buildRequest(input VerifyInput) (verifyRequest, error) {
	if len(input.SessionID) != protocol.SessionIDSize {
		return verifyRequest{}, fmt.Errorf("session ID must be %d bytes", protocol.SessionIDSize)
	}
	if len(input.AttestationKey) != protocol.PublicKeySize {
		return verifyRequest{}, fmt.Errorf("attestation key must be %d bytes", protocol.PublicKeySize)
	}
	if input.Policy == nil {
		return verifyRequest{}, fmt.Errorf("policy is required")
	}
	if err := requireJSONObject(input.EvidenceJSON); err != nil {
		return verifyRequest{}, fmt.Errorf("evidence: %w", err)
	}
	canonicalRequest, _, err := protocol.CanonicalEvidenceRequest(input.EvidenceRequestJSON)
	if err != nil {
		return verifyRequest{}, fmt.Errorf("EvidenceRequest: %w", err)
	}
	requestDigest, err := protocol.EvidenceRequestDigest(canonicalRequest)
	if err != nil {
		return verifyRequest{}, err
	}
	keyDigest := sha256.Sum256(input.AttestationKey)
	return verifyRequest{
		ProtocolVersion:       ProtocolVersion,
		SessionID:             base64.RawURLEncoding.EncodeToString(input.SessionID),
		Evidence:              append(json.RawMessage(nil), input.EvidenceJSON...),
		EvidenceRequest:       append(json.RawMessage(nil), canonicalRequest...),
		EvidenceRequestDigest: requestDigest,
		AttestationKeyDigest:  "sha256:" + hex.EncodeToString(keyDigest[:]),
		PolicyID:              input.Policy.Model.PolicyID,
		PolicyDigest:          input.Policy.Digest,
	}, nil
}

func parseResponse(contents []byte) (verifyResponse, error) {
	canonical, err := protocol.CanonicalizeJSON(contents)
	if err != nil {
		return verifyResponse{}, fmt.Errorf("Trustee response JSON: %w", err)
	}
	if err := requireFields(canonical, []string{
		"protocol_version", "session_id", "decision", "stable_error_code", "verified_claims",
		"evidence_request_digest", "attestation_key_digest", "policy_id", "policy_digest",
		"issued_at", "expires_at",
	}); err != nil {
		return verifyResponse{}, fmt.Errorf("Trustee response: %w", err)
	}
	var raw struct {
		VerifiedClaims json.RawMessage `json:"verified_claims"`
	}
	if err := json.Unmarshal(canonical, &raw); err != nil {
		return verifyResponse{}, fmt.Errorf("decode Trustee response claims: %w", err)
	}
	if !bytes.Equal(raw.VerifiedClaims, []byte("null")) {
		if err := requireFields(raw.VerifiedClaims, []string{
			"quote_verified", "report_data_verified", "tcb_status", "mrtd", "rtmr", "debug_enabled",
			"instance_id", "launch_id", "policy_id", "policy_digest", "attestation_key_digest",
			"evidence_request_digest", "verified_at", "expires_at",
		}); err != nil {
			return verifyResponse{}, fmt.Errorf("Trustee verified_claims: %w", err)
		}
		var claimsRaw struct {
			RTMR json.RawMessage `json:"rtmr"`
		}
		if err := json.Unmarshal(raw.VerifiedClaims, &claimsRaw); err != nil {
			return verifyResponse{}, fmt.Errorf("decode Trustee RTMR claims: %w", err)
		}
		if err := requireFields(claimsRaw.RTMR, []string{"0", "1", "2", "3"}); err != nil {
			return verifyResponse{}, fmt.Errorf("Trustee verified_claims.rtmr: %w", err)
		}
	}
	var response verifyResponse
	decoder := json.NewDecoder(bytes.NewReader(canonical))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&response); err != nil {
		return verifyResponse{}, fmt.Errorf("decode Trustee response: %w", err)
	}
	return response, nil
}

func validateResponse(response verifyResponse, request verifyRequest, expectedPolicy *policy.Policy, now time.Time) error {
	if response.ProtocolVersion != ProtocolVersion || response.SessionID != request.SessionID {
		return fmt.Errorf("Trustee response protocol or session mismatch")
	}
	if response.EvidenceRequestDigest != request.EvidenceRequestDigest || response.AttestationKeyDigest != request.AttestationKeyDigest {
		return fmt.Errorf("Trustee response binding digest mismatch")
	}
	if response.PolicyID != request.PolicyID || response.PolicyDigest != request.PolicyDigest {
		return fmt.Errorf("Trustee response policy mismatch")
	}
	issuedAt, expiresAt, err := validateWindow(response.IssuedAt, response.ExpiresAt, now)
	if err != nil {
		return fmt.Errorf("Trustee response validity: %w", err)
	}
	if response.Decision != "allow" || response.StableErrorCode != "OK" || response.VerifiedClaims == nil {
		return fmt.Errorf("Trustee denied attestation with code %q", response.StableErrorCode)
	}
	claims := response.VerifiedClaims
	if !claims.QuoteVerified || !claims.ReportDataVerified {
		return fmt.Errorf("Trustee did not verify quote and report_data")
	}
	if claims.PolicyID != request.PolicyID || claims.PolicyDigest != request.PolicyDigest || claims.AttestationKeyDigest != request.AttestationKeyDigest || claims.EvidenceRequestDigest != request.EvidenceRequestDigest {
		return fmt.Errorf("verified claims binding mismatch")
	}
	if !expectedPolicy.AllowsTCBStatus(claims.TCBStatus) || !expectedPolicy.AllowsMRTD(claims.MRTD) {
		return fmt.Errorf("verified claims violate TCB or MRTD policy")
	}
	if claims.DebugEnabled && !expectedPolicy.Model.TEE.AllowDebug {
		return fmt.Errorf("debug TD is not allowed")
	}
	if !identifierPattern.MatchString(claims.InstanceID) {
		return fmt.Errorf("verified instance ID is invalid")
	}
	for index := range expectedPolicy.Model.TEE.AllowedRTMR {
		measurement := claims.RTMR[index]
		if measurement == nil || !measurementPattern.MatchString(*measurement) || !expectedPolicy.AllowsRTMR(index, *measurement) {
			return fmt.Errorf("verified RTMR %s violates policy", index)
		}
	}
	claimsVerifiedAt, claimsExpiresAt, err := validateWindow(claims.VerifiedAt, claims.ExpiresAt, now)
	if err != nil {
		return fmt.Errorf("verified claims validity: %w", err)
	}
	if claimsVerifiedAt.Before(issuedAt) || claimsExpiresAt.After(expiresAt) {
		return fmt.Errorf("verified claims validity exceeds Trustee response window")
	}
	return nil
}

func validateWindow(issued, expires string, now time.Time) (time.Time, time.Time, error) {
	issuedAt, err := time.Parse("2006-01-02T15:04:05Z", issued)
	if err != nil || issuedAt.Format("2006-01-02T15:04:05Z") != issued {
		return time.Time{}, time.Time{}, fmt.Errorf("issued time is not canonical UTC RFC3339")
	}
	expiresAt, err := time.Parse("2006-01-02T15:04:05Z", expires)
	if err != nil || expiresAt.Format("2006-01-02T15:04:05Z") != expires {
		return time.Time{}, time.Time{}, fmt.Errorf("expiry time is not canonical UTC RFC3339")
	}
	if expiresAt.Before(now) || !expiresAt.After(issuedAt) || issuedAt.After(now.Add(time.Minute)) {
		return time.Time{}, time.Time{}, fmt.Errorf("validity window is not current")
	}
	return issuedAt, expiresAt, nil
}

func requireJSONObject(contents []byte) error {
	canonical, err := protocol.CanonicalizeJSON(contents)
	if err != nil {
		return err
	}
	var object map[string]json.RawMessage
	if err := json.Unmarshal(canonical, &object); err != nil || object == nil {
		return fmt.Errorf("must be a JSON object")
	}
	return nil
}

func requireFields(contents []byte, expected []string) error {
	var object map[string]json.RawMessage
	if err := json.Unmarshal(contents, &object); err != nil || object == nil {
		return fmt.Errorf("expected JSON object")
	}
	if len(object) != len(expected) {
		return fmt.Errorf("expected fields %v", expected)
	}
	for _, field := range expected {
		if _, ok := object[field]; !ok {
			return fmt.Errorf("missing field %q", field)
		}
	}
	return nil
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
