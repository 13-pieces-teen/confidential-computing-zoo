package workloadattestor

import (
	"context"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"sync"

	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-workloadattestor/internal/evidence"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-workloadattestor/internal/protocol"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-workloadattestor/internal/trustee"
	"github.com/spiffe/go-spiffe/v2/exp/proto/spiffe/broker"
	workloadattestorv1 "github.com/spiffe/spire-plugin-sdk/proto/spire/plugin/agent/workloadattestor/v1"
	configv1 "github.com/spiffe/spire-plugin-sdk/proto/spire/service/common/config/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/anypb"
)

const workloadPIDReferenceTypeURL = "type.googleapis.com/spiffe.broker.WorkloadPIDReference"

// EvidenceCollector obtains evidence for a target PID without deciding whether
// that workload is trusted.
type EvidenceCollector interface {
	Collect(context.Context, protocol.EvidenceRequest) (json.RawMessage, error)
}

// TrusteeVerifier appraises evidence and returns an allow/deny verdict. It does
// not issue an SVID or choose the workload's SPIFFE ID.
type TrusteeVerifier interface {
	Verify(context.Context, protocol.VerifyRequest) (protocol.Verdict, error)
}

// Plugin implements SPIRE Agent workload attestation and configuration. It
// orchestrates evidence collection and remote appraisal, then returns trusted
// selectors for SPIRE registration-entry matching; SPIRE remains the SVID
// issuer.
type Plugin struct {
	workloadattestorv1.UnimplementedWorkloadAttestorServer
	configv1.UnimplementedConfigServer

	clientsMu sync.RWMutex
	evidence  EvidenceCollector
	trustee   TrusteeVerifier
	nonce     func() (string, error)
}

// New creates a plugin with optional injected clients. SPIRE supplies the real
// clients through Configure; injection keeps protocol tests independent of the
// network.
func New(evidence EvidenceCollector, trustee TrusteeVerifier) *Plugin {
	return &Plugin{evidence: evidence, trustee: trustee, nonce: newNonce}
}

// Attest deliberately returns no selectors for the ordinary PID-only Workload
// API path. Remote-attestation selectors are available only through the typed
// SPIFFE Broker reference handled by AttestReference.
func (plugin *Plugin) Attest(context.Context, *workloadattestorv1.AttestRequest) (*workloadattestorv1.AttestResponse, error) {
	return &workloadattestorv1.AttestResponse{}, nil
}

// AttestReference attests the PID named by a Broker reference. Every failure is
// fail-closed: only an allow verdict bound to the same protocol, nonce, and PID
// can produce selectors. Those selectors are claims for SPIRE to match, not an
// SVID issued by this plugin.
func (plugin *Plugin) AttestReference(ctx context.Context, request *workloadattestorv1.AttestReferenceRequest) (*workloadattestorv1.AttestReferenceResponse, error) {
	// Snapshot both clients under one lock so a concurrent Configure call cannot
	// mix Evidence Provider and Trustee instances from different configurations.
	evidenceClient, trusteeClient, err := plugin.clients()
	if err != nil {
		return nil, err
	}
	reference := request.GetReference()
	if reference.GetTypeUrl() != workloadPIDReferenceTypeURL {
		return nil, status.Errorf(codes.InvalidArgument, "unsupported workload reference type %q", reference.GetTypeUrl())
	}
	var pidReference broker.WorkloadPIDReference
	if err := anypb.UnmarshalTo(reference, &pidReference, proto.UnmarshalOptions{}); err != nil {
		return nil, status.Errorf(codes.InvalidArgument, "decode workload PID reference: %v", err)
	}
	if pidReference.Pid <= 0 {
		return nil, status.Error(codes.InvalidArgument, "workload PID must be positive")
	}
	// A numeric PID is only a lookup key and can be reused. If process-incarnation
	// binding is required, the Evidence Provider and Trustee must cover stable
	// process facts in the evidence; the checks below bind only the echoed PID.
	nonce, err := plugin.nonce()
	if err != nil {
		return nil, status.Errorf(codes.Internal, "generate attestation nonce: %v", err)
	}
	evidenceRequest := protocol.EvidenceRequest{
		ProtocolVersion: protocol.Version,
		Nonce:           nonce,
		PID:             pidReference.Pid,
	}
	evidence, err := evidenceClient.Collect(ctx, evidenceRequest)
	if err != nil {
		return nil, status.Errorf(codes.Unavailable, "collect workload evidence: %v", err)
	}
	verdict, err := trusteeClient.Verify(ctx, protocol.VerifyRequest{
		ProtocolVersion: protocol.Version,
		Nonce:           nonce,
		PID:             pidReference.Pid,
		Evidence:        evidence,
	})
	if err != nil {
		return nil, status.Errorf(codes.Unavailable, "verify workload evidence: %v", err)
	}
	// Reject replayed, cross-PID, or cross-version verdicts even if they say
	// "allow".
	if verdict.ProtocolVersion != protocol.Version || verdict.Nonce != nonce || verdict.PID != pidReference.Pid {
		return nil, status.Error(codes.Unavailable, "Trustee verdict is not bound to the workload request")
	}
	if verdict.Decision != "allow" {
		return nil, status.Errorf(codes.PermissionDenied, "Trustee denied workload attestation with code %q", verdict.StableErrorCode)
	}
	if verdict.StableErrorCode != "OK" || !validSelectorComponent(verdict.WorkloadID) || !validSelectorComponent(verdict.PolicyID) {
		return nil, status.Error(codes.Unavailable, "Trustee returned an invalid allow verdict")
	}
	// SPIRE maps these selector values through registration entries and retains
	// authority over the resulting SPIFFE ID and SVID lifecycle.
	return &workloadattestorv1.AttestReferenceResponse{SelectorValues: []string{
		"verified:true",
		"workload_id:" + verdict.WorkloadID,
		"policy:" + verdict.PolicyID,
	}}, nil
}

// Validate checks configuration syntax and security boundaries without loading
// certificate files or changing the active clients.
func (plugin *Plugin) Validate(_ context.Context, request *configv1.ValidateRequest) (*configv1.ValidateResponse, error) {
	if request == nil {
		return &configv1.ValidateResponse{Valid: false, Notes: []string{"request is required"}}, nil
	}
	_, notes := parseConfig(request.HclConfiguration)
	return &configv1.ValidateResponse{Valid: len(notes) == 0, Notes: notes}, nil
}

// Configure loads the Trustee mTLS material, builds both boundary clients, and
// swaps them into service as one consistent pair.
func (plugin *Plugin) Configure(_ context.Context, request *configv1.ConfigureRequest) (*configv1.ConfigureResponse, error) {
	if request == nil {
		return nil, status.Error(codes.InvalidArgument, "request is required")
	}
	config, notes := parseConfig(request.HclConfiguration)
	if len(notes) > 0 {
		return nil, status.Errorf(codes.InvalidArgument, "invalid configuration: %s", strings.Join(notes, "; "))
	}
	tlsConfig, err := loadTrusteeTLS(config)
	if err != nil {
		return nil, status.Errorf(codes.InvalidArgument, "load Trustee mTLS configuration: %v", err)
	}
	evidenceClient, err := evidence.NewClient(config.EvidenceEndpoint, config.RequestTimeout, config.MaxResponseBytes)
	if err != nil {
		return nil, status.Errorf(codes.InvalidArgument, "configure Evidence Provider client: %v", err)
	}
	trusteeClient, err := trustee.NewClient(config.TrusteeEndpoint, tlsConfig, config.TrusteeSPIFFEID, config.RequestTimeout, config.MaxResponseBytes)
	if err != nil {
		return nil, status.Errorf(codes.InvalidArgument, "configure Trustee client: %v", err)
	}
	plugin.clientsMu.Lock()
	plugin.evidence = evidenceClient
	plugin.trustee = trusteeClient
	plugin.clientsMu.Unlock()
	return &configv1.ConfigureResponse{}, nil
}

func (plugin *Plugin) clients() (EvidenceCollector, TrusteeVerifier, error) {
	plugin.clientsMu.RLock()
	defer plugin.clientsMu.RUnlock()
	if plugin.evidence == nil || plugin.trustee == nil {
		return nil, nil, status.Error(codes.FailedPrecondition, "plugin is not configured")
	}
	return plugin.evidence, plugin.trustee, nil
}

func loadTrusteeTLS(config *Config) (*tls.Config, error) {
	// These credentials authenticate the plugin-to-Trustee channel. They are not
	// evidence that the referenced workload itself is trusted.
	certificate, err := tls.LoadX509KeyPair(config.TrusteeClientCertPath, config.TrusteeClientKeyPath)
	if err != nil {
		return nil, fmt.Errorf("load client certificate: %w", err)
	}
	caPEM, err := os.ReadFile(config.TrusteeCAPath)
	if err != nil {
		return nil, fmt.Errorf("read Trustee CA: %w", err)
	}
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(caPEM) {
		return nil, fmt.Errorf("Trustee CA contains no certificates")
	}
	return &tls.Config{
		MinVersion:   tls.VersionTLS12,
		Certificates: []tls.Certificate{certificate},
		RootCAs:      roots,
		ServerName:   config.TrusteeServerName,
	}, nil
}

func newNonce() (string, error) {
	// A fresh 256-bit nonce lets the plugin correlate evidence and verdict with
	// exactly one attestation attempt.
	value := make([]byte, 32)
	if _, err := rand.Read(value); err != nil {
		return "", fmt.Errorf("read random bytes: %w", err)
	}
	return base64.RawURLEncoding.EncodeToString(value), nil
}

func validSelectorComponent(value string) bool {
	// Colons and whitespace would change SPIRE's selector type/value grammar or
	// make the emitted claim ambiguous.
	return value != "" && !strings.ContainsAny(value, ":\r\n\t ")
}
