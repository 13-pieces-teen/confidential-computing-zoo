package server

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	nodeattestorv1 "github.com/spiffe/spire-plugin-sdk/proto/spire/plugin/server/nodeattestor/v1"
	"google.golang.org/grpc/metadata"
	"google.golang.org/protobuf/proto"

	protocolv1 "github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/gen/argus/spire/nodeattestor/v1"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/internal/policy"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/internal/protocol"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/internal/trustee"
)

type fakeVerifier struct {
	called bool
	input  trustee.VerifyInput
	claims trustee.VerifiedNodeClaims
	err    error
}

func (verifier *fakeVerifier) VerifyNode(_ context.Context, input trustee.VerifyInput) (trustee.VerifiedNodeClaims, error) {
	verifier.called = true
	verifier.input = input
	return verifier.claims, verifier.err
}

type fakeAttestStream struct {
	context       context.Context
	helloPayload  []byte
	privateKey    ed25519.PrivateKey
	evidenceJSON  []byte
	badSignature  bool
	receiveCount  int
	sentResponses []*nodeattestorv1.AttestResponse
}

func (stream *fakeAttestStream) Recv() (*nodeattestorv1.AttestRequest, error) {
	stream.receiveCount++
	if stream.receiveCount == 1 {
		return &nodeattestorv1.AttestRequest{Request: &nodeattestorv1.AttestRequest_Payload{Payload: stream.helloPayload}}, nil
	}
	if stream.receiveCount != 2 || len(stream.sentResponses) != 1 {
		return nil, fmt.Errorf("unexpected receive sequence")
	}
	challenge := new(protocolv1.ServerChallenge)
	if err := proto.Unmarshal(stream.sentResponses[0].GetChallenge(), challenge); err != nil {
		return nil, err
	}
	hello := new(protocolv1.AgentHello)
	if err := proto.Unmarshal(stream.helloPayload, hello); err != nil {
		return nil, err
	}
	hash, err := protocol.TranscriptHash(hello, challenge, stream.evidenceJSON)
	if err != nil {
		return nil, err
	}
	signature := ed25519.Sign(stream.privateKey, hash[:])
	if stream.badSignature {
		signature[0] ^= 0xff
	}
	responseBytes, err := proto.Marshal(&protocolv1.EvidenceResponse{
		ProtocolVersion: protocol.Version, SessionId: challenge.SessionId,
		EvidenceJson: stream.evidenceJSON, TranscriptSignature: signature,
	})
	if err != nil {
		return nil, err
	}
	return &nodeattestorv1.AttestRequest{Request: &nodeattestorv1.AttestRequest_ChallengeResponse{ChallengeResponse: responseBytes}}, nil
}

func (stream *fakeAttestStream) Send(response *nodeattestorv1.AttestResponse) error {
	stream.sentResponses = append(stream.sentResponses, response)
	return nil
}

func (stream *fakeAttestStream) SetHeader(metadata.MD) error  { return nil }
func (stream *fakeAttestStream) SendHeader(metadata.MD) error { return nil }
func (stream *fakeAttestStream) SetTrailer(metadata.MD)       {}
func (stream *fakeAttestStream) Context() context.Context     { return stream.context }
func (stream *fakeAttestStream) SendMsg(any) error            { return nil }
func (stream *fakeAttestStream) RecvMsg(any) error            { return nil }

func TestAttestVerifiesTranscriptBeforeTrusteeAndReturnsAttributes(t *testing.T) {
	plugin, stream, verifier := configuredAttestation(t, false)
	if err := plugin.Attest(stream); err != nil {
		t.Fatal(err)
	}
	if !verifier.called {
		t.Fatal("Trustee verifier was not called")
	}
	if len(stream.sentResponses) != 2 {
		t.Fatalf("response count = %d, want 2", len(stream.sentResponses))
	}
	challenge := new(protocolv1.ServerChallenge)
	if err := proto.Unmarshal(stream.sentResponses[0].GetChallenge(), challenge); err != nil {
		t.Fatal(err)
	}
	var request protocol.EvidenceRequest
	if err := json.Unmarshal(challenge.EvidenceRequestJson, &request); err != nil {
		t.Fatal(err)
	}
	keyID, _ := protocol.KeyID(stream.privateKey.Public().(ed25519.PublicKey))
	if request.Target.TargetURI != "argus-node:"+keyID || request.ProfileDigest != plugin.state.config.Policy.Digest {
		t.Fatal("challenge did not bind key ID and policy digest")
	}
	if !bytes.Equal(verifier.input.SessionID, challenge.SessionId) || !bytes.Equal(verifier.input.EvidenceRequestJSON, challenge.EvidenceRequestJson) {
		t.Fatal("Trustee did not receive the exact challenge context")
	}
	attributes := stream.sentResponses[1].GetAgentAttributes()
	if attributes == nil || attributes.CanReattest {
		t.Fatalf("attributes = %#v", attributes)
	}
	expectedID, _ := protocol.AgentSPIFFEID("argus.local", stream.privateKey.Public().(ed25519.PublicKey))
	if attributes.SpiffeId != expectedID {
		t.Fatalf("Agent ID = %q, want %q", attributes.SpiffeId, expectedID)
	}
	expectedSelectors := []string{
		"policy:openviking-prod-v1", "policy_digest:" + plugin.state.config.Policy.Digest,
		"mrtd:aabb", "tcb_status:up_to_date", "debug:false", "instance_id:tdvm-0001",
	}
	if !equalStringSlices(attributes.SelectorValues, expectedSelectors) {
		t.Fatalf("selectors = %v", attributes.SelectorValues)
	}
}

func TestAttestRejectsBadSignatureBeforeTrustee(t *testing.T) {
	plugin, stream, verifier := configuredAttestation(t, true)
	if err := plugin.Attest(stream); err == nil {
		t.Fatal("bad transcript signature was accepted")
	}
	if verifier.called {
		t.Fatal("Trustee was called before transcript signature passed")
	}
	if len(stream.sentResponses) != 1 {
		t.Fatal("AgentAttributes were returned after signature failure")
	}
}

func TestRecordInstanceBindingRejectsCloneConflict(t *testing.T) {
	store, err := newBindingStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	keyID := strings.Repeat("a", 64)
	first := trustee.VerifiedNodeClaims{InstanceID: "tdvm-0001"}
	second := trustee.VerifiedNodeClaims{InstanceID: "tdvm-0002"}
	if err := recordInstanceBinding(store, keyID, first); err != nil {
		t.Fatal(err)
	}
	if err := recordInstanceBinding(store, keyID, first); err != nil {
		t.Fatal("same binding was not idempotent")
	}
	if err := recordInstanceBinding(store, keyID, second); err == nil {
		t.Fatal("same key bound to another instance was accepted")
	}
}

func configuredAttestation(t *testing.T, badSignature bool) (*Plugin, *fakeAttestStream, *fakeVerifier) {
	t.Helper()
	loadedPolicy, err := policy.Parse([]byte(validPolicyYAML))
	if err != nil {
		t.Fatal(err)
	}
	privateKey := ed25519.NewKeyFromSeed(bytes.Repeat([]byte{0x41}, ed25519.SeedSize))
	helloBytes, err := proto.Marshal(&protocolv1.AgentHello{
		ProtocolVersion: protocol.Version, AttestationPublicKey: privateKey.Public().(ed25519.PublicKey),
		AgentNonce: bytes.Repeat([]byte{0x42}, protocol.NonceSize), InstanceHint: "tdvm-0001",
		Capabilities: []string{"report_data_v1", "tdx"},
	})
	if err != nil {
		t.Fatal(err)
	}
	claims := trustee.VerifiedNodeClaims{
		QuoteVerified: true, ReportDataVerified: true, TCBStatus: "up_to_date", MRTD: "aabb",
		DebugEnabled: false, InstanceID: "tdvm-0001", PolicyID: loadedPolicy.Model.PolicyID, PolicyDigest: loadedPolicy.Digest,
	}
	verifier := &fakeVerifier{claims: claims}
	plugin := New()
	bindings, err := newBindingStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	plugin.state = &runtimeState{config: &Config{
		TrustDomain: "argus.local", Policy: loadedPolicy, ChallengeTTL: 30 * time.Second,
		VerifierTimeout: time.Second, MaxEvidenceBytes: protocol.MaxEvidenceSize,
	}, verifier: verifier, bindingStore: bindings}
	plugin.random = bytes.NewReader(append(bytes.Repeat([]byte{0x51}, 32), bytes.Repeat([]byte{0x52}, 32)...))
	plugin.now = time.Now
	stream := &fakeAttestStream{
		context: context.Background(), helloPayload: helloBytes, privateKey: privateKey,
		evidenceJSON: []byte(`{"quote":"fixture"}`), badSignature: badSignature,
	}
	return plugin, stream, verifier
}

func equalStringSlices(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	for index := range left {
		if left[index] != right[index] {
			return false
		}
	}
	return true
}

var _ nodeattestorv1.NodeAttestor_AttestServer = (*fakeAttestStream)(nil)
