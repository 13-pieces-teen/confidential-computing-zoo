package protocol

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/url"
	"regexp"
	"sort"
	"strings"
	"time"
)

var (
	asciiValuePattern = regexp.MustCompile(`^[\x20-\x7e]+$`)
	digestPattern     = regexp.MustCompile(`^[a-z0-9]+:[0-9a-f]+$`)
)

// TargetService identifies both the logical attestation target and its
// proof-key-derived URI.
type TargetService struct {
	ServiceName string `json:"service_name"`
	TargetURI   string `json:"target_uri"`
}

// EvidenceRequest is the Server-authored request bound into TDX REPORTDATA.
type EvidenceRequest struct {
	Version         string        `json:"version"`
	Nonce           string        `json:"nonce"`
	CallerID        string        `json:"caller_id"`
	Target          TargetService `json:"target"`
	RequestedClaims []string      `json:"requested_claims"`
	ProfileDigest   string        `json:"profile_digest"`
}

type ServiceIdentity struct {
	ServiceName      string  `json:"service_name"`
	ServiceID        *string `json:"service_id"`
	InstanceID       string  `json:"instance_id"`
	InstanceScope    string  `json:"instance_scope"`
	ImageDigest      *string `json:"image_digest"`
	ExecutableDigest *string `json:"executable_digest"`
	SPIFFEID         *string `json:"spiffe_id"`
}

type RuntimeBinding struct {
	Endpoint         string  `json:"endpoint"`
	OwningPID        uint32  `json:"owning_pid"`
	ProcessStartTime string  `json:"process_start_time"`
	ContainerID      *string `json:"container_id"`
	PodUID           *string `json:"pod_uid"`
	VMInstanceID     *string `json:"vm_instance_id"`
	Namespace        *string `json:"namespace"`
	CgroupPath       *string `json:"cgroup_path"`
}

// BindingClaims describe which service instance and runtime produced the
// evidence, plus the provenance and assurance assigned to each claim.
type BindingClaims struct {
	AssuranceLevel           string               `json:"assurance_level"`
	ServiceIdentity          ServiceIdentity      `json:"service_identity"`
	RuntimeBinding           RuntimeBinding       `json:"runtime_binding"`
	ClaimSupport             map[string][]string  `json:"claim_support"`
	VerifierValidatedSupport *map[string][]string `json:"verifier_validated_support"`
	ProviderClaimAssurance   map[string]string    `json:"provider_claim_assurance"`
}

// CanonicalEvidenceRequest accepts only the v1 field set and returns the JCS
// form used in REPORTDATA and Trustee request digests.
func CanonicalEvidenceRequest(input []byte) ([]byte, EvidenceRequest, error) {
	canonical, err := canonicalizeJSON(input)
	if err != nil {
		return nil, EvidenceRequest{}, err
	}
	if err := validateObjectFields(canonical, []string{
		"version", "nonce", "caller_id", "target", "requested_claims", "profile_digest",
	}); err != nil {
		return nil, EvidenceRequest{}, fmt.Errorf("EvidenceRequest: %w", err)
	}

	var raw struct {
		Target json.RawMessage `json:"target"`
	}
	if err := json.Unmarshal(canonical, &raw); err != nil {
		return nil, EvidenceRequest{}, fmt.Errorf("decode EvidenceRequest target: %w", err)
	}
	if err := validateObjectFields(raw.Target, []string{"service_name", "target_uri"}); err != nil {
		return nil, EvidenceRequest{}, fmt.Errorf("EvidenceRequest.target: %w", err)
	}

	var request EvidenceRequest
	if err := decodeExact(canonical, &request); err != nil {
		return nil, EvidenceRequest{}, fmt.Errorf("decode EvidenceRequest: %w", err)
	}
	if err := validateEvidenceRequest(request); err != nil {
		return nil, EvidenceRequest{}, err
	}
	return canonical, request, nil
}

// CanonicalBindingClaims validates and normalizes claim-source sets before
// returning the one representation that may be hashed into REPORTDATA.
func CanonicalBindingClaims(input []byte) ([]byte, BindingClaims, error) {
	canonical, err := canonicalizeJSON(input)
	if err != nil {
		return nil, BindingClaims{}, err
	}
	if err := validateObjectFields(canonical, []string{
		"assurance_level", "service_identity", "runtime_binding", "claim_support",
		"verifier_validated_support", "provider_claim_assurance",
	}); err != nil {
		return nil, BindingClaims{}, fmt.Errorf("BindingClaims: %w", err)
	}

	var raw struct {
		ServiceIdentity json.RawMessage `json:"service_identity"`
		RuntimeBinding  json.RawMessage `json:"runtime_binding"`
	}
	if err := json.Unmarshal(canonical, &raw); err != nil {
		return nil, BindingClaims{}, fmt.Errorf("decode BindingClaims children: %w", err)
	}
	if err := validateObjectFields(raw.ServiceIdentity, []string{
		"service_name", "service_id", "instance_id", "instance_scope", "image_digest",
		"executable_digest", "spiffe_id",
	}); err != nil {
		return nil, BindingClaims{}, fmt.Errorf("BindingClaims.service_identity: %w", err)
	}
	if err := validateObjectFields(raw.RuntimeBinding, []string{
		"endpoint", "owning_pid", "process_start_time", "container_id", "pod_uid",
		"vm_instance_id", "namespace", "cgroup_path",
	}); err != nil {
		return nil, BindingClaims{}, fmt.Errorf("BindingClaims.runtime_binding: %w", err)
	}

	var claims BindingClaims
	if err := decodeExact(canonical, &claims); err != nil {
		return nil, BindingClaims{}, fmt.Errorf("decode BindingClaims: %w", err)
	}
	if err := validateBindingClaims(&claims); err != nil {
		return nil, BindingClaims{}, err
	}

	normalized, err := json.Marshal(claims)
	if err != nil {
		return nil, BindingClaims{}, fmt.Errorf("marshal normalized BindingClaims: %w", err)
	}
	canonical, err = jcsCanonicalizeValidated(normalized)
	if err != nil {
		return nil, BindingClaims{}, err
	}
	return canonical, claims, nil
}

func validateEvidenceRequest(request EvidenceRequest) error {
	if request.Version != "v1" {
		return fmt.Errorf("unsupported EvidenceRequest version %q", request.Version)
	}
	nonce, err := base64.RawURLEncoding.DecodeString(request.Nonce)
	if err != nil || len(nonce) != NonceSize {
		return fmt.Errorf("EvidenceRequest nonce must be %d-byte unpadded base64url", NonceSize)
	}
	if err := validateSPIFFEID(request.CallerID); err != nil {
		return fmt.Errorf("EvidenceRequest caller_id: %w", err)
	}
	if !isASCII(request.Target.ServiceName) || !isASCII(request.Target.TargetURI) {
		return fmt.Errorf("EvidenceRequest target fields must be normalized ASCII")
	}
	if len(request.RequestedClaims) != 2 || request.RequestedClaims[0] != "TeeQuote" || request.RequestedClaims[1] != "IdentityClaims" {
		return fmt.Errorf("EvidenceRequest requested_claims must be [TeeQuote, IdentityClaims]")
	}
	if !digestPattern.MatchString(request.ProfileDigest) {
		return fmt.Errorf("EvidenceRequest profile_digest is not normalized")
	}
	return nil
}

func validateBindingClaims(claims *BindingClaims) error {
	if !validAssurance(claims.AssuranceLevel) {
		return fmt.Errorf("BindingClaims assurance_level is invalid")
	}
	identity := claims.ServiceIdentity
	for name, value := range map[string]string{
		"service_name":   identity.ServiceName,
		"instance_id":    identity.InstanceID,
		"instance_scope": identity.InstanceScope,
	} {
		if !isASCII(value) {
			return fmt.Errorf("BindingClaims service_identity.%s must be normalized ASCII", name)
		}
	}
	for name, value := range map[string]*string{
		"service_id":        identity.ServiceID,
		"image_digest":      identity.ImageDigest,
		"executable_digest": identity.ExecutableDigest,
	} {
		if value != nil && !isASCII(*value) {
			return fmt.Errorf("BindingClaims service_identity.%s must be normalized ASCII", name)
		}
	}
	if identity.ImageDigest != nil && !digestPattern.MatchString(*identity.ImageDigest) {
		return fmt.Errorf("BindingClaims service_identity.image_digest is not normalized")
	}
	if identity.ExecutableDigest != nil && !digestPattern.MatchString(*identity.ExecutableDigest) {
		return fmt.Errorf("BindingClaims service_identity.executable_digest is not normalized")
	}
	if identity.SPIFFEID != nil {
		if err := validateSPIFFEID(*identity.SPIFFEID); err != nil {
			return fmt.Errorf("BindingClaims service_identity.spiffe_id: %w", err)
		}
	}

	runtime := claims.RuntimeBinding
	for name, value := range map[string]string{
		"endpoint":           runtime.Endpoint,
		"process_start_time": runtime.ProcessStartTime,
	} {
		if !isASCII(value) {
			return fmt.Errorf("BindingClaims runtime_binding.%s must be normalized ASCII", name)
		}
	}
	if parsed, err := time.Parse("2006-01-02T15:04:05Z", runtime.ProcessStartTime); err != nil || parsed.Format("2006-01-02T15:04:05Z") != runtime.ProcessStartTime {
		return fmt.Errorf("BindingClaims runtime_binding.process_start_time is not canonical UTC RFC3339")
	}
	for name, value := range map[string]*string{
		"container_id":   runtime.ContainerID,
		"pod_uid":        runtime.PodUID,
		"vm_instance_id": runtime.VMInstanceID,
		"namespace":      runtime.Namespace,
		"cgroup_path":    runtime.CgroupPath,
	} {
		if value != nil && !isASCII(*value) {
			return fmt.Errorf("BindingClaims runtime_binding.%s must be normalized ASCII", name)
		}
	}

	if err := validateAndNormalizeSupport(claims.ClaimSupport); err != nil {
		return fmt.Errorf("BindingClaims claim_support: %w", err)
	}
	if claims.VerifierValidatedSupport != nil {
		if err := validateAndNormalizeSupport(*claims.VerifierValidatedSupport); err != nil {
			return fmt.Errorf("BindingClaims verifier_validated_support: %w", err)
		}
	}
	for claim, assurance := range claims.ProviderClaimAssurance {
		if !isASCII(claim) || !validAssurance(assurance) {
			return fmt.Errorf("BindingClaims provider_claim_assurance is invalid")
		}
	}
	return nil
}

func validateObjectFields(input []byte, expected []string) error {
	// Exact field sets make schema additions explicit protocol changes instead
	// of silently accepted but uninterpreted metadata.
	var object map[string]json.RawMessage
	if err := json.Unmarshal(input, &object); err != nil {
		return fmt.Errorf("expected JSON object: %w", err)
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

func decodeExact(input []byte, target any) error {
	decoder := json.NewDecoder(bytes.NewReader(input))
	decoder.DisallowUnknownFields()
	return decoder.Decode(target)
}

func validateAndNormalizeSupport(support map[string][]string) error {
	for claim, sources := range support {
		if !isASCII(claim) {
			return fmt.Errorf("claim name %q is not normalized ASCII", claim)
		}
		for _, source := range sources {
			if !isASCII(source) {
				return fmt.Errorf("source for claim %q is not normalized ASCII", claim)
			}
		}
		// Source lists are sets in the protocol; sorting and deduplication make
		// their digest independent of provider ordering.
		sort.Strings(sources)
		output := sources[:0]
		for _, source := range sources {
			if len(output) == 0 || output[len(output)-1] != source {
				output = append(output, source)
			}
		}
		support[claim] = output
	}
	return nil
}

func jcsCanonicalizeValidated(input []byte) ([]byte, error) {
	return canonicalizeJSON(input)
}

func validAssurance(value string) bool {
	return value == "L0" || value == "L1" || value == "L2" || value == "L3"
}

func isASCII(value string) bool {
	return value != "" && asciiValuePattern.MatchString(value)
}

func validateSPIFFEID(value string) error {
	parsed, err := url.Parse(value)
	if err != nil || parsed.Scheme != "spiffe" || parsed.Host == "" || parsed.Path == "" || parsed.RawQuery != "" || parsed.Fragment != "" {
		return fmt.Errorf("invalid SPIFFE ID")
	}
	if parsed.String() != value || strings.Contains(parsed.Path, "//") {
		return fmt.Errorf("SPIFFE ID is not normalized")
	}
	return nil
}
