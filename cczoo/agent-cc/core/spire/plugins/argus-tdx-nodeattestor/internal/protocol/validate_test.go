package protocol

import (
	"encoding/base64"
	"strings"
	"testing"

	nodeattestorv1 "github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/gen/argus/spire/nodeattestor/v1"
)

func TestValidateServerChallengeClockSkew(t *testing.T) {
	const now = int64(1_700_000_000)

	tests := []struct {
		name      string
		issuedAt  int64
		expiresAt int64
		wantErr   bool
	}{
		{name: "current", issuedAt: now, expiresAt: now + 30},
		{name: "issued within skew", issuedAt: now + ChallengeClockSkewSeconds, expiresAt: now + 30},
		{name: "issued beyond skew", issuedAt: now + ChallengeClockSkewSeconds + 1, expiresAt: now + 30, wantErr: true},
		{name: "expired within skew", issuedAt: now - 30, expiresAt: now - ChallengeClockSkewSeconds + 1},
		{name: "expired at skew boundary", issuedAt: now - 30, expiresAt: now - ChallengeClockSkewSeconds, wantErr: true},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			challenge := validServerChallenge(test.issuedAt, test.expiresAt)
			err := validateServerChallengeAt(challenge, now)
			if test.wantErr {
				if err == nil || !strings.Contains(err.Error(), "not currently valid") {
					t.Fatalf("expected validity error, got %v", err)
				}
				return
			}
			if err != nil {
				t.Fatalf("expected valid challenge, got %v", err)
			}
		})
	}
}

func validServerChallenge(issuedAt, expiresAt int64) *nodeattestorv1.ServerChallenge {
	nonce := make([]byte, NonceSize)
	nonceValue := base64.RawURLEncoding.EncodeToString(nonce)
	return &nodeattestorv1.ServerChallenge{
		ProtocolVersion:     Version,
		SessionId:           make([]byte, SessionIDSize),
		Nonce:               nonce,
		IssuedAtUnix:        issuedAt,
		ExpiresAtUnix:       expiresAt,
		PolicyId:            "test-policy",
		EvidenceRequestJson: []byte(`{"version":"v1","nonce":"` + nonceValue + `","caller_id":"spiffe://argus.local/spire/server","target":{"service_name":"tdx-node","target_uri":"argus-node:test"},"requested_claims":["TeeQuote","IdentityClaims"],"profile_digest":"sha256:00"}`),
	}
}
