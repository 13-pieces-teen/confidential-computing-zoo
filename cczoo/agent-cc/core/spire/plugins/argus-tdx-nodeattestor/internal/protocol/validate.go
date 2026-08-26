package protocol

import (
	"encoding/json"
	"fmt"
	"regexp"
	"time"

	nodeattestorv1 "github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/gen/argus/spire/nodeattestor/v1"
	"google.golang.org/protobuf/proto"
)

var capabilityPattern = regexp.MustCompile(`^[a-z0-9_-]{1,64}$`)

// ValidateAgentHello enforces the bounded, versioned first handshake message
// before the Server derives identity from its proof key.
func ValidateAgentHello(hello *nodeattestorv1.AgentHello) error {
	if hello == nil {
		return fmt.Errorf("AgentHello is required")
	}
	if proto.Size(hello) > MaxAgentHelloSize {
		return fmt.Errorf("AgentHello exceeds %d bytes", MaxAgentHelloSize)
	}
	if hello.ProtocolVersion != Version {
		return fmt.Errorf("unsupported AgentHello protocol version %d", hello.ProtocolVersion)
	}
	if len(hello.AttestationPublicKey) != PublicKeySize {
		return fmt.Errorf("attestation public key must be %d bytes", PublicKeySize)
	}
	if len(hello.AgentNonce) != NonceSize {
		return fmt.Errorf("agent nonce must be %d bytes", NonceSize)
	}
	if len(hello.InstanceHint) > MaxInstanceHint || (hello.InstanceHint != "" && !asciiValuePattern.MatchString(hello.InstanceHint)) {
		return fmt.Errorf("instance hint is invalid")
	}
	if len(hello.Capabilities) > MaxCapabilities {
		return fmt.Errorf("too many capabilities")
	}
	seen := make(map[string]struct{}, len(hello.Capabilities))
	for _, capability := range hello.Capabilities {
		if !capabilityPattern.MatchString(capability) {
			return fmt.Errorf("invalid capability %q", capability)
		}
		if _, duplicate := seen[capability]; duplicate {
			return fmt.Errorf("duplicate capability %q", capability)
		}
		seen[capability] = struct{}{}
	}
	return nil
}

// ValidateServerChallenge verifies both the challenge envelope and the nonce
// copied into its canonical EvidenceRequest.
func ValidateServerChallenge(challenge *nodeattestorv1.ServerChallenge) error {
	return validateServerChallengeAt(challenge, time.Now().Unix())
}

func validateServerChallengeAt(challenge *nodeattestorv1.ServerChallenge, now int64) error {
	if challenge == nil {
		return fmt.Errorf("ServerChallenge is required")
	}
	if proto.Size(challenge) > MaxChallengeSize {
		return fmt.Errorf("ServerChallenge exceeds %d bytes", MaxChallengeSize)
	}
	if challenge.ProtocolVersion != Version {
		return fmt.Errorf("unsupported ServerChallenge protocol version %d", challenge.ProtocolVersion)
	}
	if len(challenge.SessionId) != SessionIDSize || len(challenge.Nonce) != NonceSize {
		return fmt.Errorf("challenge session ID and nonce must each be %d bytes", NonceSize)
	}
	if challenge.IssuedAtUnix <= 0 || challenge.ExpiresAtUnix <= challenge.IssuedAtUnix {
		return fmt.Errorf("challenge validity window is invalid")
	}
	// A small symmetric tolerance accommodates known guest/host clock skew while
	// still rejecting challenges that are not current.
	issuedTooFarAhead := now < challenge.IssuedAtUnix &&
		challenge.IssuedAtUnix-now > ChallengeClockSkewSeconds
	expiredTooLongAgo := now >= challenge.ExpiresAtUnix &&
		now-challenge.ExpiresAtUnix >= ChallengeClockSkewSeconds
	if issuedTooFarAhead || expiredTooLongAgo {
		return fmt.Errorf("challenge is not currently valid")
	}
	if !isASCII(challenge.PolicyId) {
		return fmt.Errorf("challenge policy ID is invalid")
	}
	if len(challenge.EvidenceRequestJson) == 0 || len(challenge.EvidenceRequestJson) > MaxChallengeSize {
		return fmt.Errorf("challenge EvidenceRequest size is outside the allowed range")
	}
	_, request, err := CanonicalEvidenceRequest(challenge.EvidenceRequestJson)
	if err != nil {
		return fmt.Errorf("challenge EvidenceRequest: %w", err)
	}
	if request.Nonce != encodeBase64URL(challenge.Nonce) {
		return fmt.Errorf("challenge nonce does not match EvidenceRequest nonce")
	}
	return nil
}

// ValidateEvidenceResponse checks the session binding and ensures evidence is
// a bounded, canonicalizable JSON object before signature verification.
func ValidateEvidenceResponse(response *nodeattestorv1.EvidenceResponse, expectedSessionID []byte) error {
	if response == nil {
		return fmt.Errorf("EvidenceResponse is required")
	}
	if proto.Size(response) > MaxEvidenceSize {
		return fmt.Errorf("EvidenceResponse exceeds %d bytes", MaxEvidenceSize)
	}
	if response.ProtocolVersion != Version {
		return fmt.Errorf("unsupported EvidenceResponse protocol version %d", response.ProtocolVersion)
	}
	if len(expectedSessionID) != SessionIDSize || !equalBytes(response.SessionId, expectedSessionID) {
		return fmt.Errorf("EvidenceResponse session ID mismatch")
	}
	if len(response.TranscriptSignature) != SignatureSize {
		return fmt.Errorf("transcript signature must be %d bytes", SignatureSize)
	}
	if len(response.EvidenceJson) == 0 || len(response.EvidenceJson) > MaxEvidenceSize {
		return fmt.Errorf("evidence JSON size is outside the allowed range")
	}
	canonical, err := canonicalizeJSON(response.EvidenceJson)
	if err != nil {
		return fmt.Errorf("evidence JSON: %w", err)
	}
	var object map[string]json.RawMessage
	if err := json.Unmarshal(canonical, &object); err != nil || object == nil {
		return fmt.Errorf("evidence JSON must be an object")
	}
	return nil
}
