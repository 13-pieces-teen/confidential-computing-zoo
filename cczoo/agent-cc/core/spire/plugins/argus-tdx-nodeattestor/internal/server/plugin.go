package server

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"strings"
	"sync"
	"time"

	"github.com/hashicorp/go-hclog"
	"github.com/spiffe/spire-plugin-sdk/pluginsdk"
	nodeattestorv1 "github.com/spiffe/spire-plugin-sdk/proto/spire/plugin/server/nodeattestor/v1"
	configv1 "github.com/spiffe/spire-plugin-sdk/proto/spire/service/common/config/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"

	protocolv1 "github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/gen/argus/spire/nodeattestor/v1"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/internal/protocol"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/internal/telemetry"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/internal/trustee"
)

var _ pluginsdk.NeedsLogger = (*Plugin)(nil)
var _ pluginsdk.NeedsHostServices = (*Plugin)(nil)

var requiredCapabilities = map[string]struct{}{
	"report_data_v1": {},
	"tdx":            {},
}

type TrusteeVerifier interface {
	VerifyNode(context.Context, trustee.VerifyInput) (trustee.VerifiedNodeClaims, error)
}

type VerifierFactory func(*Config) (TrusteeVerifier, error)

type runtimeState struct {
	config       *Config
	verifier     TrusteeVerifier
	bindingStore *bindingStore
}

type Plugin struct {
	nodeattestorv1.UnimplementedNodeAttestorServer
	configv1.UnimplementedConfigServer

	stateMu sync.RWMutex
	state   *runtimeState
	logger  hclog.Logger

	random          io.Reader
	now             func() time.Time
	verifierFactory VerifierFactory
	telemetry       telemetry.Recorder
}

func New() *Plugin {
	return &Plugin{
		random: rand.Reader,
		now:    time.Now,
		verifierFactory: func(config *Config) (TrusteeVerifier, error) {
			return trustee.NewClient(
				config.TrusteeURL,
				config.TrusteeTLSConfig,
				config.TrusteeExpectedSPIFFEID,
				config.VerifierTimeout,
				config.MaxEvidenceBytes,
			)
		},
	}
}

func (plugin *Plugin) Attest(stream nodeattestorv1.NodeAttestor_AttestServer) (err error) {
	started := time.Now()
	defer func() { plugin.telemetry.Attestation("server", started, err) }()
	state, err := plugin.getState()
	if err != nil {
		return err
	}
	initial, err := stream.Recv()
	if err != nil {
		return status.Errorf(codes.Unavailable, "receive AgentHello: %v", err)
	}
	if len(initial.GetPayload()) == 0 || len(initial.GetChallengeResponse()) != 0 || len(initial.GetPayload()) > protocol.MaxAgentHelloSize {
		return status.Error(codes.InvalidArgument, "first attestation request must contain only AgentHello payload")
	}
	hello := new(protocolv1.AgentHello)
	if err := proto.Unmarshal(initial.GetPayload(), hello); err != nil {
		return status.Errorf(codes.InvalidArgument, "unmarshal AgentHello: %v", err)
	}
	if len(hello.ProtoReflect().GetUnknown()) != 0 {
		return status.Error(codes.InvalidArgument, "AgentHello contains unknown fields")
	}
	if err := protocol.ValidateAgentHello(hello); err != nil {
		return status.Errorf(codes.InvalidArgument, "validate AgentHello: %v", err)
	}
	if err := validateCapabilities(hello.Capabilities); err != nil {
		return status.Errorf(codes.InvalidArgument, "validate AgentHello capabilities: %v", err)
	}
	keyID, err := protocol.KeyID(hello.AttestationPublicKey)
	if err != nil {
		return status.Errorf(codes.InvalidArgument, "derive key ID: %v", err)
	}

	sessionID := make([]byte, protocol.SessionIDSize)
	nonce := make([]byte, protocol.NonceSize)
	if _, err := io.ReadFull(plugin.random, sessionID); err != nil {
		return status.Errorf(codes.Internal, "generate session ID: %v", err)
	}
	if _, err := io.ReadFull(plugin.random, nonce); err != nil {
		return status.Errorf(codes.Internal, "generate challenge nonce: %v", err)
	}
	evidenceRequestJSON, err := buildEvidenceRequest(state.config, nonce, keyID)
	if err != nil {
		return status.Errorf(codes.Internal, "build EvidenceRequest: %v", err)
	}
	now := plugin.now().UTC().Truncate(time.Second)
	challenge := &protocolv1.ServerChallenge{
		ProtocolVersion:     protocol.Version,
		SessionId:           sessionID,
		Nonce:               nonce,
		IssuedAtUnix:        now.Unix(),
		ExpiresAtUnix:       now.Add(state.config.ChallengeTTL).Unix(),
		PolicyId:            state.config.Policy.Model.PolicyID,
		EvidenceRequestJson: evidenceRequestJSON,
	}
	challengeBytes, err := (proto.MarshalOptions{Deterministic: true}).Marshal(challenge)
	if err != nil {
		return status.Errorf(codes.Internal, "marshal ServerChallenge: %v", err)
	}
	if err := stream.Send(&nodeattestorv1.AttestResponse{
		Response: &nodeattestorv1.AttestResponse_Challenge{Challenge: challengeBytes},
	}); err != nil {
		return status.Errorf(codes.Unavailable, "send ServerChallenge: %v", err)
	}

	request, err := stream.Recv()
	if err != nil {
		return status.Errorf(codes.Unavailable, "receive EvidenceResponse: %v", err)
	}
	if len(request.GetChallengeResponse()) == 0 || len(request.GetPayload()) != 0 || len(request.GetChallengeResponse()) > protocol.MaxEvidenceSize {
		return status.Error(codes.InvalidArgument, "second attestation request must contain only EvidenceResponse")
	}
	response := new(protocolv1.EvidenceResponse)
	if err := proto.Unmarshal(request.GetChallengeResponse(), response); err != nil {
		return status.Errorf(codes.InvalidArgument, "unmarshal EvidenceResponse: %v", err)
	}
	if len(response.ProtoReflect().GetUnknown()) != 0 {
		return status.Error(codes.InvalidArgument, "EvidenceResponse contains unknown fields")
	}
	plugin.telemetry.EvidenceBytes("server", len(response.EvidenceJson))
	if int64(len(response.EvidenceJson)) > state.config.MaxEvidenceBytes {
		return status.Error(codes.ResourceExhausted, "evidence exceeds configured size limit")
	}
	if err := protocol.ValidateEvidenceResponse(response, sessionID); err != nil {
		return status.Errorf(codes.InvalidArgument, "validate EvidenceResponse: %v", err)
	}
	transcriptHash, err := protocol.TranscriptHash(hello, challenge, response.EvidenceJson)
	if err != nil {
		return status.Errorf(codes.InvalidArgument, "construct transcript: %v", err)
	}
	if !ed25519.Verify(ed25519.PublicKey(hello.AttestationPublicKey), transcriptHash[:], response.TranscriptSignature) {
		return status.Error(codes.PermissionDenied, "transcript signature verification failed")
	}

	verifyContext, cancel := context.WithTimeout(stream.Context(), state.config.VerifierTimeout)
	defer cancel()
	claims, err := state.verifier.VerifyNode(verifyContext, trustee.VerifyInput{
		SessionID:           sessionID,
		EvidenceJSON:        response.EvidenceJson,
		EvidenceRequestJSON: evidenceRequestJSON,
		AttestationKey:      hello.AttestationPublicKey,
		Policy:              state.config.Policy,
	})
	plugin.telemetry.Trustee(err)
	if err != nil {
		return status.Errorf(codes.PermissionDenied, "Trustee verification failed: %v", err)
	}
	if hello.InstanceHint != "" && hello.InstanceHint != claims.InstanceID {
		return status.Error(codes.PermissionDenied, "instance hint does not match verified instance ID")
	}
	if err := recordInstanceBinding(state.bindingStore, keyID, claims); err != nil {
		return status.Errorf(codes.PermissionDenied, "attestation key conflict: %v", err)
	}
	attributes, err := deriveAgentAttributes(state.config, hello.AttestationPublicKey, claims)
	if err != nil {
		return status.Errorf(codes.Internal, "derive AgentAttributes: %v", err)
	}
	if err := stream.Send(&nodeattestorv1.AttestResponse{
		Response: &nodeattestorv1.AttestResponse_AgentAttributes{AgentAttributes: attributes},
	}); err != nil {
		return status.Errorf(codes.Unavailable, "send AgentAttributes: %v", err)
	}
	return nil
}

func (plugin *Plugin) Validate(_ context.Context, request *configv1.ValidateRequest) (*configv1.ValidateResponse, error) {
	if request == nil {
		return &configv1.ValidateResponse{Valid: false, Notes: []string{"request is required"}}, nil
	}
	_, notes := parseConfig(request.CoreConfiguration, request.HclConfiguration)
	return &configv1.ValidateResponse{Valid: len(notes) == 0, Notes: notes}, nil
}

func (plugin *Plugin) Configure(_ context.Context, request *configv1.ConfigureRequest) (*configv1.ConfigureResponse, error) {
	if request == nil {
		return nil, status.Error(codes.InvalidArgument, "request is required")
	}
	config, notes := parseConfig(request.CoreConfiguration, request.HclConfiguration)
	if len(notes) > 0 {
		return nil, status.Errorf(codes.InvalidArgument, "invalid configuration: %s", strings.Join(notes, "; "))
	}
	verifier, err := plugin.verifierFactory(config)
	if err != nil {
		return nil, status.Errorf(codes.InvalidArgument, "configure Trustee verifier: %v", err)
	}
	bindingStore, err := newBindingStore(config.BindingStateDir)
	if err != nil {
		return nil, status.Errorf(codes.InvalidArgument, "configure binding store: %v", err)
	}
	plugin.stateMu.Lock()
	plugin.state = &runtimeState{config: config, verifier: verifier, bindingStore: bindingStore}
	plugin.stateMu.Unlock()
	return &configv1.ConfigureResponse{}, nil
}

func (plugin *Plugin) BrokerHostServices(broker pluginsdk.ServiceBroker) error {
	plugin.telemetry.Broker(broker)
	return nil
}

func (plugin *Plugin) SetLogger(logger hclog.Logger) {
	plugin.logger = logger
}

func (plugin *Plugin) getState() (*runtimeState, error) {
	plugin.stateMu.RLock()
	defer plugin.stateMu.RUnlock()
	if plugin.state == nil {
		return nil, status.Error(codes.FailedPrecondition, "plugin is not configured")
	}
	return plugin.state, nil
}

func recordInstanceBinding(store *bindingStore, keyID string, claims trustee.VerifiedNodeClaims) error {
	if store == nil {
		return fmt.Errorf("binding store is not configured")
	}
	launchID := ""
	if claims.LaunchID != nil {
		launchID = *claims.LaunchID
	}
	return store.Bind(keyID, instanceBinding{InstanceID: claims.InstanceID, LaunchID: launchID})
}

func buildEvidenceRequest(config *Config, nonce []byte, keyID string) ([]byte, error) {
	request := protocol.EvidenceRequest{
		Version:  "v1",
		Nonce:    base64.RawURLEncoding.EncodeToString(nonce),
		CallerID: "spiffe://" + config.TrustDomain + "/spire/server",
		Target: protocol.TargetService{
			ServiceName: "argus-tdx-node",
			TargetURI:   "argus-node:" + keyID,
		},
		RequestedClaims: []string{"TeeQuote", "IdentityClaims"},
		ProfileDigest:   config.Policy.Digest,
	}
	encoded, err := json.Marshal(request)
	if err != nil {
		return nil, err
	}
	canonical, _, err := protocol.CanonicalEvidenceRequest(encoded)
	return canonical, err
}

func validateCapabilities(capabilities []string) error {
	if len(capabilities) != len(requiredCapabilities) {
		return fmt.Errorf("capabilities must be exactly report_data_v1 and tdx")
	}
	for _, capability := range capabilities {
		if _, ok := requiredCapabilities[capability]; !ok {
			return fmt.Errorf("unsupported capability %q", capability)
		}
	}
	return nil
}

func deriveAgentAttributes(config *Config, publicKey []byte, claims trustee.VerifiedNodeClaims) (*nodeattestorv1.AgentAttributes, error) {
	spiffeID, err := protocol.AgentSPIFFEID(config.TrustDomain, publicKey)
	if err != nil {
		return nil, err
	}
	selectors := []string{
		"policy:" + claims.PolicyID,
		"policy_digest:" + claims.PolicyDigest,
		"mrtd:" + claims.MRTD,
		"tcb_status:" + claims.TCBStatus,
		fmt.Sprintf("debug:%t", claims.DebugEnabled),
		"instance_id:" + claims.InstanceID,
	}
	if err := validateSelectors(selectors); err != nil {
		return nil, err
	}
	return &nodeattestorv1.AgentAttributes{
		SpiffeId:       spiffeID,
		SelectorValues: selectors,
		CanReattest:    false,
	}, nil
}

func validateSelectors(selectors []string) error {
	if len(selectors) == 0 || len(selectors) > protocol.MaxSelectorValues {
		return fmt.Errorf("selector count is outside the allowed range")
	}
	for _, selector := range selectors {
		if len(selector) == 0 || len(selector) > protocol.MaxSelectorSize || strings.IndexFunc(selector, func(character rune) bool {
			return character < 0x20 || character == 0x7f
		}) >= 0 {
			return fmt.Errorf("selector value is invalid")
		}
	}
	return nil
}
