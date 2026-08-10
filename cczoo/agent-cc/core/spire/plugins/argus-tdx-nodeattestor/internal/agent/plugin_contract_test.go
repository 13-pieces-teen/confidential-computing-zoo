package agent

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
	nodeattestorv1 "github.com/spiffe/spire-plugin-sdk/proto/spire/plugin/agent/nodeattestor/v1"
	configv1 "github.com/spiffe/spire-plugin-sdk/proto/spire/service/common/config/v1"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/emptypb"

	protocolv1 "github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/gen/argus/spire/nodeattestor/v1"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-nodeattestor/internal/protocol"
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

func TestPluginSDKContractSendsInitialPayloadFirst(t *testing.T) {
	privateKey := ed25519.NewKeyFromSeed(bytes.Repeat([]byte{0x51}, ed25519.SeedSize))
	provider := &fakeProvider{result: []byte(`{"version":"v1","evidence":"contract"}`)}
	plugin := New()
	metrics := new(contractMetrics)
	plugin.keyLoader = func(string) (ed25519.PrivateKey, error) { return privateKey, nil }
	plugin.random = bytes.NewReader(bytes.Repeat([]byte{0x61}, protocol.NonceSize))
	plugin.providerFactory = func(*Config) (EvidenceProvider, error) { return provider, nil }

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

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	validation, err := configClient.Validate(ctx, &configv1.ValidateRequest{
		CoreConfiguration: &configv1.CoreConfiguration{TrustDomain: "argus.local"},
		HclConfiguration: `
evidence_endpoint = "unix:///run/argus/evidence.sock"
attestation_key_path = "/var/lib/spire/argus-tdx/attestation-key"
`,
	})
	if err != nil {
		t.Fatal(err)
	}
	if !validation.Valid || len(validation.Notes) != 0 {
		t.Fatalf("validation = %#v", validation)
	}
	if _, err := configClient.Configure(ctx, &configv1.ConfigureRequest{
		CoreConfiguration: &configv1.CoreConfiguration{TrustDomain: "argus.local"},
		HclConfiguration: `
evidence_endpoint = "unix:///run/argus/evidence.sock"
attestation_key_path = "/var/lib/spire/argus-tdx/attestation-key"
`,
	}); err != nil {
		t.Fatal(err)
	}

	stream, err := nodeAttestorClient.AidAttestation(ctx)
	if err != nil {
		t.Fatal(err)
	}
	initial, err := stream.Recv()
	if err != nil {
		t.Fatal(err)
	}
	if len(initial.GetPayload()) == 0 || len(initial.GetChallengeResponse()) != 0 {
		t.Fatal("first Agent message was not an initial payload")
	}
	hello := new(protocolv1.AgentHello)
	if err := proto.Unmarshal(initial.GetPayload(), hello); err != nil {
		t.Fatal(err)
	}
	keyID, err := protocol.KeyID(hello.AttestationPublicKey)
	if err != nil {
		t.Fatal(err)
	}
	nonce := bytes.Repeat([]byte{0x71}, protocol.NonceSize)
	issuedAt := time.Now().Add(-time.Second).Unix()
	challengeBytes, err := proto.Marshal(&protocolv1.ServerChallenge{
		ProtocolVersion:     protocol.Version,
		SessionId:           bytes.Repeat([]byte{0x72}, protocol.SessionIDSize),
		Nonce:               nonce,
		IssuedAtUnix:        issuedAt,
		ExpiresAtUnix:       issuedAt + 30,
		PolicyId:            "openviking-prod-v1",
		EvidenceRequestJson: validEvidenceRequest(t, nonce, keyID),
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := stream.Send(&nodeattestorv1.Challenge{Challenge: challengeBytes}); err != nil {
		t.Fatal(err)
	}
	final, err := stream.Recv()
	if err != nil {
		t.Fatal(err)
	}
	if len(final.GetChallengeResponse()) == 0 || len(final.GetPayload()) != 0 {
		t.Fatal("second Agent message was not a challenge response")
	}
	if _, err := stream.Recv(); err == nil {
		t.Fatal("Agent stream returned more than one challenge response")
	}
	metrics.mu.Lock()
	defer metrics.mu.Unlock()
	if len(metrics.counters) != 1 || len(metrics.samples) != 1 || len(metrics.durations) != 1 {
		t.Fatalf("metrics calls = counters:%d samples:%d durations:%d", len(metrics.counters), len(metrics.samples), len(metrics.durations))
	}
	if got := metrics.counters[0].Key; len(got) != 2 || got[1] != "attempts" {
		t.Fatalf("attempt counter key = %v", got)
	}
	if metrics.samples[0].Val != float32(len(provider.result)) {
		t.Fatalf("evidence bytes = %v", metrics.samples[0].Val)
	}
}
