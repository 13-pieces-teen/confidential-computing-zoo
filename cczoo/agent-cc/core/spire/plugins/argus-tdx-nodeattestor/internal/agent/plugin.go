package agent

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"fmt"
	"io"
	"strings"
	"sync"
	"time"

	"github.com/hashicorp/go-hclog"
	"github.com/spiffe/spire-plugin-sdk/pluginsdk"
	nodeattestorv1 "github.com/spiffe/spire-plugin-sdk/proto/spire/plugin/agent/nodeattestor/v1"
	configv1 "github.com/spiffe/spire-plugin-sdk/proto/spire/service/common/config/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"

	protocolv1 "github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/gen/argus/spire/nodeattestor/v1"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/internal/evidence"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/internal/protocol"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/internal/telemetry"
)

var _ pluginsdk.NeedsLogger = (*Plugin)(nil)
var _ pluginsdk.NeedsHostServices = (*Plugin)(nil)

type EvidenceProvider interface {
	GetEvidence(context.Context, []byte) ([]byte, error)
}

type ProviderFactory func(*Config) (EvidenceProvider, error)

type Plugin struct {
	nodeattestorv1.UnimplementedNodeAttestorServer
	configv1.UnimplementedConfigServer

	configMu sync.RWMutex
	config   *Config
	logger   hclog.Logger

	random          io.Reader
	keyLoader       func(string) (ed25519.PrivateKey, error)
	providerFactory ProviderFactory
	telemetry       telemetry.Recorder
}

func New() *Plugin {
	return &Plugin{
		random:    rand.Reader,
		keyLoader: loadOrCreateAttestationKey,
		providerFactory: func(config *Config) (EvidenceProvider, error) {
			return evidence.NewClient(config.EvidenceEndpoint, config.EvidenceTimeout, config.MaxEvidenceBytes)
		},
	}
}

func (plugin *Plugin) AidAttestation(stream nodeattestorv1.NodeAttestor_AidAttestationServer) (err error) {
	started := time.Now()
	defer func() { plugin.telemetry.Attestation("agent", started, err) }()
	config, err := plugin.getConfig()
	if err != nil {
		return err
	}
	privateKey, err := plugin.keyLoader(config.AttestationKeyPath)
	if err != nil {
		return status.Errorf(codes.Internal, "load attestation key: %v", err)
	}
	publicKey := privateKey.Public().(ed25519.PublicKey)
	agentNonce := make([]byte, protocol.NonceSize)
	if _, err := io.ReadFull(plugin.random, agentNonce); err != nil {
		return status.Errorf(codes.Internal, "generate agent nonce: %v", err)
	}

	hello := &protocolv1.AgentHello{
		ProtocolVersion:      protocol.Version,
		AttestationPublicKey: publicKey,
		AgentNonce:           agentNonce,
		InstanceHint:         config.InstanceHint,
		Capabilities:         []string{"report_data_v1", "tdx"},
	}
	if err := protocol.ValidateAgentHello(hello); err != nil {
		return status.Errorf(codes.Internal, "construct AgentHello: %v", err)
	}
	helloBytes, err := (proto.MarshalOptions{Deterministic: true}).Marshal(hello)
	if err != nil {
		return status.Errorf(codes.Internal, "marshal AgentHello: %v", err)
	}
	if err := stream.Send(&nodeattestorv1.PayloadOrChallengeResponse{
		Data: &nodeattestorv1.PayloadOrChallengeResponse_Payload{Payload: helloBytes},
	}); err != nil {
		return status.Errorf(codes.Unavailable, "send AgentHello: %v", err)
	}

	spireChallenge, err := stream.Recv()
	if err != nil {
		return status.Errorf(codes.Unavailable, "receive ServerChallenge: %v", err)
	}
	if len(spireChallenge.Challenge) == 0 || len(spireChallenge.Challenge) > protocol.MaxChallengeSize {
		return status.Error(codes.InvalidArgument, "ServerChallenge size is outside the allowed range")
	}
	challenge := new(protocolv1.ServerChallenge)
	if err := proto.Unmarshal(spireChallenge.Challenge, challenge); err != nil {
		return status.Errorf(codes.InvalidArgument, "unmarshal ServerChallenge: %v", err)
	}
	if err := protocol.ValidateServerChallenge(challenge); err != nil {
		return status.Errorf(codes.InvalidArgument, "validate ServerChallenge: %v", err)
	}
	_, evidenceRequest, err := protocol.CanonicalEvidenceRequest(challenge.EvidenceRequestJson)
	if err != nil {
		return status.Errorf(codes.InvalidArgument, "validate EvidenceRequest: %v", err)
	}
	keyID, err := protocol.KeyID(publicKey)
	if err != nil {
		return status.Errorf(codes.Internal, "derive key ID: %v", err)
	}
	if evidenceRequest.Target.TargetURI != "argus-node:"+keyID {
		return status.Error(codes.PermissionDenied, "EvidenceRequest target does not match the attestation key")
	}

	provider, err := plugin.providerFactory(config)
	if err != nil {
		return status.Errorf(codes.Internal, "configure Evidence Provider client: %v", err)
	}
	evidenceContext, cancel := context.WithTimeout(stream.Context(), config.EvidenceTimeout)
	defer cancel()
	evidenceJSON, err := provider.GetEvidence(evidenceContext, challenge.EvidenceRequestJson)
	if err != nil {
		return status.Errorf(codes.Unavailable, "obtain evidence: %v", err)
	}
	plugin.telemetry.EvidenceBytes("agent", len(evidenceJSON))
	if int64(len(evidenceJSON)) > config.MaxEvidenceBytes {
		return status.Error(codes.ResourceExhausted, "evidence exceeds configured size limit")
	}
	transcriptHash, err := protocol.TranscriptHash(hello, challenge, evidenceJSON)
	if err != nil {
		return status.Errorf(codes.InvalidArgument, "construct transcript: %v", err)
	}
	response := &protocolv1.EvidenceResponse{
		ProtocolVersion:     protocol.Version,
		SessionId:           append([]byte(nil), challenge.SessionId...),
		EvidenceJson:        evidenceJSON,
		TranscriptSignature: ed25519.Sign(privateKey, transcriptHash[:]),
	}
	if err := protocol.ValidateEvidenceResponse(response, challenge.SessionId); err != nil {
		return status.Errorf(codes.InvalidArgument, "construct EvidenceResponse: %v", err)
	}
	responseBytes, err := (proto.MarshalOptions{Deterministic: true}).Marshal(response)
	if err != nil {
		return status.Errorf(codes.Internal, "marshal EvidenceResponse: %v", err)
	}
	if err := stream.Send(&nodeattestorv1.PayloadOrChallengeResponse{
		Data: &nodeattestorv1.PayloadOrChallengeResponse_ChallengeResponse{ChallengeResponse: responseBytes},
	}); err != nil {
		return status.Errorf(codes.Unavailable, "send EvidenceResponse: %v", err)
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
	plugin.configMu.Lock()
	plugin.config = config
	plugin.configMu.Unlock()
	return &configv1.ConfigureResponse{}, nil
}

func (plugin *Plugin) BrokerHostServices(broker pluginsdk.ServiceBroker) error {
	plugin.telemetry.Broker(broker)
	return nil
}

func (plugin *Plugin) SetLogger(logger hclog.Logger) {
	plugin.logger = logger
}

func (plugin *Plugin) getConfig() (*Config, error) {
	plugin.configMu.RLock()
	defer plugin.configMu.RUnlock()
	if plugin.config == nil {
		return nil, status.Error(codes.FailedPrecondition, "plugin is not configured")
	}
	return plugin.config, nil
}

func (plugin *Plugin) String() string {
	return fmt.Sprintf("argus_tdx Agent NodeAttestor")
}
