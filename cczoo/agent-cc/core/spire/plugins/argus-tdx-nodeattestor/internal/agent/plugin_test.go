package agent

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/url"
	"testing"
	"time"

	nodeattestorv1 "github.com/spiffe/spire-plugin-sdk/proto/spire/plugin/agent/nodeattestor/v1"
	"google.golang.org/grpc/metadata"
	"google.golang.org/protobuf/proto"

	protocolv1 "github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/gen/argus/spire/nodeattestor/v1"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/internal/protocol"
)

type fakeProvider struct {
	request []byte
	result  []byte
}

func (provider *fakeProvider) GetEvidence(_ context.Context, request []byte) ([]byte, error) {
	provider.request = append([]byte(nil), request...)
	return append([]byte(nil), provider.result...), nil
}

type fakeAidStream struct {
	context   context.Context
	challenge *nodeattestorv1.Challenge
	sent      []*nodeattestorv1.PayloadOrChallengeResponse
}

func (stream *fakeAidStream) Send(response *nodeattestorv1.PayloadOrChallengeResponse) error {
	stream.sent = append(stream.sent, response)
	return nil
}

func (stream *fakeAidStream) Recv() (*nodeattestorv1.Challenge, error) {
	if len(stream.sent) != 1 || len(stream.sent[0].GetPayload()) == 0 {
		return nil, fmt.Errorf("AgentHello was not sent before receiving challenge")
	}
	return stream.challenge, nil
}

func (stream *fakeAidStream) SetHeader(metadata.MD) error  { return nil }
func (stream *fakeAidStream) SendHeader(metadata.MD) error { return nil }
func (stream *fakeAidStream) SetTrailer(metadata.MD)       {}
func (stream *fakeAidStream) Context() context.Context     { return stream.context }
func (stream *fakeAidStream) SendMsg(any) error            { return nil }
func (stream *fakeAidStream) RecvMsg(any) error            { return nil }

func TestAidAttestationSendsHelloBeforeSignedEvidenceResponse(t *testing.T) {
	seed := bytes.Repeat([]byte{0x41}, ed25519.SeedSize)
	privateKey := ed25519.NewKeyFromSeed(seed)
	publicKey := privateKey.Public().(ed25519.PublicKey)
	keyID, err := protocol.KeyID(publicKey)
	if err != nil {
		t.Fatal(err)
	}
	challengeNonce := bytes.Repeat([]byte{0x22}, protocol.NonceSize)
	evidenceRequest := validEvidenceRequest(t, challengeNonce, keyID)
	issuedAt := time.Now().Add(-time.Second).Unix()
	challenge := &protocolv1.ServerChallenge{
		ProtocolVersion:     protocol.Version,
		SessionId:           bytes.Repeat([]byte{0x33}, protocol.SessionIDSize),
		Nonce:               challengeNonce,
		IssuedAtUnix:        issuedAt,
		ExpiresAtUnix:       issuedAt + 30,
		PolicyId:            "openviking-prod-v1",
		EvidenceRequestJson: evidenceRequest,
	}
	challengeBytes, err := (proto.MarshalOptions{Deterministic: true}).Marshal(challenge)
	if err != nil {
		t.Fatal(err)
	}

	provider := &fakeProvider{result: []byte(`{"version":"v1","evidence":"fixture"}`)}
	plugin := New()
	plugin.config = &Config{
		TrustDomain:        "argus.local",
		EvidenceEndpoint:   &url.URL{Scheme: "unix", Path: "/run/argus/evidence.sock"},
		AttestationKeyPath: "/not/read/by-test",
		EvidenceTimeout:    time.Second,
		MaxEvidenceBytes:   protocol.MaxEvidenceSize,
		InstanceHint:       "tdvm-0001",
	}
	plugin.keyLoader = func(string) (ed25519.PrivateKey, error) { return privateKey, nil }
	plugin.random = bytes.NewReader(bytes.Repeat([]byte{0x11}, protocol.NonceSize))
	plugin.providerFactory = func(*Config) (EvidenceProvider, error) { return provider, nil }
	stream := &fakeAidStream{
		context:   context.Background(),
		challenge: &nodeattestorv1.Challenge{Challenge: challengeBytes},
	}

	if err := plugin.AidAttestation(stream); err != nil {
		t.Fatal(err)
	}
	if len(stream.sent) != 2 {
		t.Fatalf("sent message count = %d, want 2", len(stream.sent))
	}
	if !bytes.Equal(provider.request, evidenceRequest) {
		t.Fatal("Evidence Provider did not receive the original EvidenceRequest bytes")
	}

	hello := new(protocolv1.AgentHello)
	if err := proto.Unmarshal(stream.sent[0].GetPayload(), hello); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(hello.AttestationPublicKey, publicKey) {
		t.Fatal("AgentHello public key mismatch")
	}
	if !bytes.Equal(hello.AgentNonce, bytes.Repeat([]byte{0x11}, protocol.NonceSize)) {
		t.Fatal("AgentHello nonce mismatch")
	}

	response := new(protocolv1.EvidenceResponse)
	if err := proto.Unmarshal(stream.sent[1].GetChallengeResponse(), response); err != nil {
		t.Fatal(err)
	}
	transcriptHash, err := protocol.TranscriptHash(hello, challenge, response.EvidenceJson)
	if err != nil {
		t.Fatal(err)
	}
	if !ed25519.Verify(publicKey, transcriptHash[:], response.TranscriptSignature) {
		t.Fatal("EvidenceResponse transcript signature is invalid")
	}
}

func TestAidAttestationRejectsMismatchedKeyBeforeEvidenceRequest(t *testing.T) {
	privateKey := ed25519.NewKeyFromSeed(bytes.Repeat([]byte{0x41}, ed25519.SeedSize))
	challengeNonce := bytes.Repeat([]byte{0x22}, protocol.NonceSize)
	evidenceRequest := validEvidenceRequest(t, challengeNonce, "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff")
	issuedAt := time.Now().Add(-time.Second).Unix()
	challengeBytes, err := proto.Marshal(&protocolv1.ServerChallenge{
		ProtocolVersion:     protocol.Version,
		SessionId:           bytes.Repeat([]byte{0x33}, protocol.SessionIDSize),
		Nonce:               challengeNonce,
		IssuedAtUnix:        issuedAt,
		ExpiresAtUnix:       issuedAt + 30,
		PolicyId:            "openviking-prod-v1",
		EvidenceRequestJson: evidenceRequest,
	})
	if err != nil {
		t.Fatal(err)
	}
	provider := &fakeProvider{result: []byte(`{}`)}
	plugin := New()
	plugin.config = &Config{AttestationKeyPath: "/unused", EvidenceTimeout: time.Second, MaxEvidenceBytes: protocol.MaxEvidenceSize}
	plugin.keyLoader = func(string) (ed25519.PrivateKey, error) { return privateKey, nil }
	plugin.random = bytes.NewReader(bytes.Repeat([]byte{0x11}, protocol.NonceSize))
	plugin.providerFactory = func(*Config) (EvidenceProvider, error) { return provider, nil }
	stream := &fakeAidStream{context: context.Background(), challenge: &nodeattestorv1.Challenge{Challenge: challengeBytes}}

	if err := plugin.AidAttestation(stream); err == nil {
		t.Fatal("mismatched EvidenceRequest target was accepted")
	}
	if provider.request != nil {
		t.Fatal("Evidence Provider was called before key binding was validated")
	}
}

func validEvidenceRequest(t *testing.T, nonce []byte, keyID string) []byte {
	t.Helper()
	request := map[string]any{
		"version":   "v1",
		"nonce":     base64.RawURLEncoding.EncodeToString(nonce),
		"caller_id": "spiffe://argus.local/spire/server",
		"target": map[string]any{
			"service_name": "argus-tdx-node",
			"target_uri":   "argus-node:" + keyID,
		},
		"requested_claims": []string{"TeeQuote", "IdentityClaims"},
		"profile_digest":   "sha256:" + string(bytes.Repeat([]byte{'a'}, 64)),
	}
	contents, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	return contents
}

var _ nodeattestorv1.NodeAttestor_AidAttestationServer = (*fakeAidStream)(nil)
var _ io.Reader = (*bytes.Reader)(nil)
