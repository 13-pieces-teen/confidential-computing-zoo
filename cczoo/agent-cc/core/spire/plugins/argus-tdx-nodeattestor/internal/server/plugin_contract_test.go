package server

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"sync"
	"testing"
	"time"

	"github.com/spiffe/spire-plugin-sdk/pluginsdk"
	"github.com/spiffe/spire-plugin-sdk/plugintest"
	metricsv1 "github.com/spiffe/spire-plugin-sdk/proto/spire/hostservice/common/metrics/v1"
	nodeattestorv1 "github.com/spiffe/spire-plugin-sdk/proto/spire/plugin/server/nodeattestor/v1"
	configv1 "github.com/spiffe/spire-plugin-sdk/proto/spire/service/common/config/v1"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/emptypb"

	protocolv1 "github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/gen/argus/spire/nodeattestor/v1"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/internal/protocol"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/internal/trustee"
)

type contractMetrics struct {
	metricsv1.UnimplementedMetricsServer
	mu        sync.Mutex
	counters  []*metricsv1.IncrCounterRequest
	samples   []*metricsv1.AddSampleRequest
	durations []*metricsv1.MeasureSinceRequest
}

func (metrics *contractMetrics) IncrCounter(_ context.Context, request *metricsv1.IncrCounterRequest) (*emptypb.Empty, error) {
	metrics.mu.Lock()
	defer metrics.mu.Unlock()
	metrics.counters = append(metrics.counters, request)
	return &emptypb.Empty{}, nil
}

func (metrics *contractMetrics) AddSample(_ context.Context, request *metricsv1.AddSampleRequest) (*emptypb.Empty, error) {
	metrics.mu.Lock()
	defer metrics.mu.Unlock()
	metrics.samples = append(metrics.samples, request)
	return &emptypb.Empty{}, nil
}

func (metrics *contractMetrics) MeasureSince(_ context.Context, request *metricsv1.MeasureSinceRequest) (*emptypb.Empty, error) {
	metrics.mu.Lock()
	defer metrics.mu.Unlock()
	metrics.durations = append(metrics.durations, request)
	return &emptypb.Empty{}, nil
}

func TestPluginSDKContractChallengesThenReturnsAttributes(t *testing.T) {
	plugin := New()
	metrics := new(contractMetrics)
	plugin.random = bytes.NewReader(append(bytes.Repeat([]byte{0x61}, 32), bytes.Repeat([]byte{0x62}, 32)...))
	var verifier *fakeVerifier
	plugin.verifierFactory = func(config *Config) (TrusteeVerifier, error) {
		verifier = &fakeVerifier{claims: trustee.VerifiedNodeClaims{
			QuoteVerified: true, ReportDataVerified: true, TCBStatus: "up_to_date", MRTD: "aabb",
			InstanceID: "tdvm-0001", PolicyID: config.Policy.Model.PolicyID, PolicyDigest: config.Policy.Digest,
		}}
		return verifier, nil
	}
	nodeAttestorClient := new(nodeattestorv1.NodeAttestorPluginClient)
	configClient := new(configv1.ConfigServiceClient)
	plugintest.ServeInBackground(t, plugintest.Config{
		PluginServer: nodeattestorv1.NodeAttestorPluginServer(plugin),
		PluginClient: nodeAttestorClient,
		ServiceServers: []pluginsdk.ServiceServer{
			configv1.ConfigServiceServer(plugin),
		},
		ServiceClients:     []pluginsdk.ServiceClient{configClient},
		HostServiceServers: []pluginsdk.ServiceServer{metricsv1.MetricsServiceServer(metrics)},
	})

	paths := writeConfigFixtures(t, t.TempDir())
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	validation, err := configClient.Validate(ctx, &configv1.ValidateRequest{
		CoreConfiguration: &configv1.CoreConfiguration{TrustDomain: "argus.local"},
		HclConfiguration:  validServerHCL(paths),
	})
	if err != nil {
		t.Fatal(err)
	}
	if !validation.Valid || len(validation.Notes) != 0 {
		t.Fatalf("validation = %#v", validation)
	}
	if _, err := configClient.Configure(ctx, &configv1.ConfigureRequest{
		CoreConfiguration: &configv1.CoreConfiguration{TrustDomain: "argus.local"},
		HclConfiguration:  validServerHCL(paths),
	}); err != nil {
		t.Fatal(err)
	}

	privateKey := ed25519.NewKeyFromSeed(bytes.Repeat([]byte{0x71}, ed25519.SeedSize))
	hello := &protocolv1.AgentHello{
		ProtocolVersion: protocol.Version, AttestationPublicKey: privateKey.Public().(ed25519.PublicKey),
		AgentNonce: bytes.Repeat([]byte{0x72}, protocol.NonceSize), InstanceHint: "tdvm-0001",
		Capabilities: []string{"report_data_v1", "tdx"},
	}
	helloBytes, err := proto.Marshal(hello)
	if err != nil {
		t.Fatal(err)
	}
	stream, err := nodeAttestorClient.Attest(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if err := stream.Send(&nodeattestorv1.AttestRequest{Request: &nodeattestorv1.AttestRequest_Payload{Payload: helloBytes}}); err != nil {
		t.Fatal(err)
	}
	challengeEnvelope, err := stream.Recv()
	if err != nil {
		t.Fatal(err)
	}
	if len(challengeEnvelope.GetChallenge()) == 0 || challengeEnvelope.GetAgentAttributes() != nil {
		t.Fatal("first Server response was not a challenge")
	}
	challenge := new(protocolv1.ServerChallenge)
	if err := proto.Unmarshal(challengeEnvelope.GetChallenge(), challenge); err != nil {
		t.Fatal(err)
	}
	evidenceJSON := []byte(`{"quote":"contract"}`)
	transcriptHash, err := protocol.TranscriptHash(hello, challenge, evidenceJSON)
	if err != nil {
		t.Fatal(err)
	}
	responseBytes, err := proto.Marshal(&protocolv1.EvidenceResponse{
		ProtocolVersion: protocol.Version, SessionId: challenge.SessionId, EvidenceJson: evidenceJSON,
		TranscriptSignature: ed25519.Sign(privateKey, transcriptHash[:]),
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := stream.Send(&nodeattestorv1.AttestRequest{Request: &nodeattestorv1.AttestRequest_ChallengeResponse{ChallengeResponse: responseBytes}}); err != nil {
		t.Fatal(err)
	}
	final, err := stream.Recv()
	if err != nil {
		t.Fatal(err)
	}
	if final.GetAgentAttributes() == nil || len(final.GetChallenge()) != 0 {
		t.Fatal("final Server response was not AgentAttributes")
	}
	if !verifier.called {
		t.Fatal("configured Trustee verifier was not called")
	}
	if _, err := stream.Recv(); err == nil {
		t.Fatal("Server returned more than one AgentAttributes response")
	}
	metrics.mu.Lock()
	defer metrics.mu.Unlock()
	if len(metrics.counters) != 2 || len(metrics.samples) != 1 || len(metrics.durations) != 1 {
		t.Fatalf("metrics calls = counters:%d samples:%d durations:%d", len(metrics.counters), len(metrics.samples), len(metrics.durations))
	}
	counterNames := map[string]bool{}
	for _, request := range metrics.counters {
		if len(request.Key) == 2 {
			counterNames[request.Key[1]] = true
		}
	}
	if !counterNames["attempts"] || !counterNames["trustee_requests"] {
		t.Fatalf("counter names = %v", counterNames)
	}
	if metrics.samples[0].Val != float32(len(evidenceJSON)) {
		t.Fatalf("evidence bytes = %v", metrics.samples[0].Val)
	}
}
