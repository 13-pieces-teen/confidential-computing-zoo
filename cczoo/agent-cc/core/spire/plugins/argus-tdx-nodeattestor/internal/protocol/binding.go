package protocol

import (
	"crypto/sha256"
	"crypto/sha512"
	"encoding/hex"
	"fmt"

	nodeattestorv1 "github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/gen/argus/spire/nodeattestor/v1"
	"google.golang.org/protobuf/proto"
)

// BindingReportData derives the 64-byte TDX REPORTDATA value from the exact
// EvidenceRequest and BindingClaims. SHA-384 fills the first 48 bytes; the TDX
// field's remaining 16 bytes intentionally stay zero.
func BindingReportData(evidenceRequestJSON, bindingClaimsJSON []byte) ([64]byte, error) {
	var reportData [64]byte
	canonicalRequest, _, err := CanonicalEvidenceRequest(evidenceRequestJSON)
	if err != nil {
		return reportData, fmt.Errorf("canonicalize EvidenceRequest: %w", err)
	}
	canonicalClaims, _, err := CanonicalBindingClaims(bindingClaimsJSON)
	if err != nil {
		return reportData, fmt.Errorf("canonicalize BindingClaims: %w", err)
	}

	hasher := sha512.New384()
	_, _ = hasher.Write(bindingDomain)
	_, _ = hasher.Write(canonicalRequest)
	_, _ = hasher.Write(canonicalClaims)
	copy(reportData[:48], hasher.Sum(nil))
	return reportData, nil
}

// EvidenceRequestDigest names the canonical request used by both the Server
// and Trustee, preventing semantically equivalent JSON encodings from drifting.
func EvidenceRequestDigest(evidenceRequestJSON []byte) (string, error) {
	canonical, _, err := CanonicalEvidenceRequest(evidenceRequestJSON)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(canonical)
	return "sha256:" + hex.EncodeToString(digest[:]), nil
}

// KeyID is the stable public identifier for the Agent's Ed25519 proof key.
func KeyID(attestationPublicKey []byte) (string, error) {
	if len(attestationPublicKey) != PublicKeySize {
		return "", fmt.Errorf("attestation public key must be %d bytes", PublicKeySize)
	}
	digest := sha256.Sum256(attestationPublicKey)
	return hex.EncodeToString(digest[:]), nil
}

// AgentSPIFFEID derives node identity from the proof key rather than a
// self-reported instance name.
func AgentSPIFFEID(trustDomain string, attestationPublicKey []byte) (string, error) {
	if trustDomain == "" {
		return "", fmt.Errorf("trust domain is required")
	}
	keyID, err := KeyID(attestationPublicKey)
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("spiffe://%s/spire/agent/argus_tdx/%s", trustDomain, keyID), nil
}

// TranscriptHash binds the complete hello, challenge, and evidence bytes. The
// Agent signs this hash so none of them can be substituted independently.
func TranscriptHash(hello *nodeattestorv1.AgentHello, challenge *nodeattestorv1.ServerChallenge, evidenceJSON []byte) ([32]byte, error) {
	var result [32]byte
	if err := ValidateAgentHello(hello); err != nil {
		return result, err
	}
	if err := ValidateServerChallenge(challenge); err != nil {
		return result, err
	}
	if len(evidenceJSON) == 0 || len(evidenceJSON) > MaxEvidenceSize {
		return result, fmt.Errorf("evidence JSON size is outside the allowed range")
	}

	marshal := proto.MarshalOptions{Deterministic: true}
	helloBytes, err := marshal.Marshal(hello)
	if err != nil {
		return result, fmt.Errorf("marshal AgentHello: %w", err)
	}
	challengeBytes, err := marshal.Marshal(challenge)
	if err != nil {
		return result, fmt.Errorf("marshal ServerChallenge: %w", err)
	}
	evidenceDigest := sha256.Sum256(evidenceJSON)

	hasher := sha256.New()
	_, _ = hasher.Write(transcriptDomain)
	_, _ = hasher.Write(helloBytes)
	_, _ = hasher.Write(challengeBytes)
	_, _ = hasher.Write(evidenceDigest[:])
	copy(result[:], hasher.Sum(nil))
	return result, nil
}
