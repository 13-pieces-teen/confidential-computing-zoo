package workloadattestor

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-workloadattestor/internal/protocol"
	"github.com/spiffe/go-spiffe/v2/exp/proto/spiffe/broker"
	workloadattestorv1 "github.com/spiffe/spire-plugin-sdk/proto/spire/plugin/agent/workloadattestor/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/anypb"
	"google.golang.org/protobuf/types/known/emptypb"
)

func TestAttestReferenceAllowReturnsVerifiedSelectors(t *testing.T) {
	evidence := &stubEvidenceCollector{
		document: json.RawMessage(`{"protocol_version":1,"nonce":"nonce-from-request","pid":4321,"workload_id":"openviking-cmem","policy_id":"openviking-cmem-v1"}`),
	}
	trustee := &stubTrusteeVerifier{
		verdict: protocol.Verdict{
			ProtocolVersion: protocol.Version,
			PID:             4321,
			Decision:        "allow",
			StableErrorCode: "OK",
			WorkloadID:      "openviking-cmem",
			PolicyID:        "openviking-cmem-v1",
		},
	}
	plugin := New(evidence, trustee)
	plugin.nonce = func() (string, error) { return "nonce-from-request", nil }

	response, err := plugin.AttestReference(context.Background(), pidReference(t, 4321))
	if err != nil {
		t.Fatalf("AttestReference() error = %v", err)
	}
	want := []string{"verified:true", "workload_id:openviking-cmem", "policy:openviking-cmem-v1"}
	assertStrings(t, response.SelectorValues, want)
	if evidence.request.PID != 4321 || evidence.request.Nonce != "nonce-from-request" {
		t.Fatalf("Evidence Provider request = %#v", evidence.request)
	}
	if trustee.request.PID != 4321 || trustee.request.Nonce != "nonce-from-request" {
		t.Fatalf("Trustee request = %#v", trustee.request)
	}
}

func TestAttestReferenceDenyReturnsNoTrustedSelectors(t *testing.T) {
	evidence := &stubEvidenceCollector{document: json.RawMessage(`{"protocol_version":1}`)}
	trustee := &stubTrusteeVerifier{verdict: protocol.Verdict{
		ProtocolVersion: protocol.Version,
		PID:             4321,
		Decision:        "deny",
		StableErrorCode: "POLICY_MISMATCH",
	}}
	plugin := New(evidence, trustee)
	plugin.nonce = func() (string, error) { return "nonce-from-request", nil }

	response, err := plugin.AttestReference(context.Background(), pidReference(t, 4321))
	if status.Code(err) != codes.PermissionDenied {
		t.Fatalf("AttestReference() code = %v, error = %v", status.Code(err), err)
	}
	if response != nil {
		t.Fatalf("AttestReference() response = %#v, want nil", response)
	}
}

func TestAttestReferenceRejectsVerdictBoundToAnotherPID(t *testing.T) {
	evidence := &stubEvidenceCollector{document: json.RawMessage(`{"protocol_version":1}`)}
	trustee := &stubTrusteeVerifier{verdict: protocol.Verdict{
		ProtocolVersion: protocol.Version,
		PID:             9999,
		Decision:        "allow",
		StableErrorCode: "OK",
		WorkloadID:      "openviking-cmem",
		PolicyID:        "openviking-cmem-v1",
	}}
	plugin := New(evidence, trustee)
	plugin.nonce = func() (string, error) { return "nonce-from-request", nil }

	response, err := plugin.AttestReference(context.Background(), pidReference(t, 4321))
	if status.Code(err) != codes.Unavailable {
		t.Fatalf("AttestReference() code = %v, error = %v", status.Code(err), err)
	}
	if response != nil {
		t.Fatalf("AttestReference() response = %#v, want nil", response)
	}
}

func TestAttestReferenceRejectsUnsupportedReferenceType(t *testing.T) {
	plugin := New(&stubEvidenceCollector{}, &stubTrusteeVerifier{})
	reference, err := anypb.New(&emptypb.Empty{})
	if err != nil {
		t.Fatal(err)
	}

	response, err := plugin.AttestReference(context.Background(), &workloadattestorv1.AttestReferenceRequest{Reference: reference})
	if status.Code(err) != codes.InvalidArgument {
		t.Fatalf("AttestReference() code = %v, error = %v", status.Code(err), err)
	}
	if response != nil {
		t.Fatalf("AttestReference() response = %#v, want nil", response)
	}
}

func TestAttestDoesNotGrantTrustedSelectorsToOrdinaryWorkloadAPI(t *testing.T) {
	plugin := New(&stubEvidenceCollector{}, &stubTrusteeVerifier{})

	response, err := plugin.Attest(context.Background(), &workloadattestorv1.AttestRequest{Pid: 4321})
	if err != nil {
		t.Fatalf("Attest() error = %v", err)
	}
	if len(response.SelectorValues) != 0 {
		t.Fatalf("Attest() selectors = %v, want none", response.SelectorValues)
	}
}

type stubEvidenceCollector struct {
	document json.RawMessage
	request  protocol.EvidenceRequest
}

func (stub *stubEvidenceCollector) Collect(_ context.Context, request protocol.EvidenceRequest) (json.RawMessage, error) {
	stub.request = request
	return stub.document, nil
}

type stubTrusteeVerifier struct {
	verdict protocol.Verdict
	request protocol.VerifyRequest
}

func (stub *stubTrusteeVerifier) Verify(_ context.Context, request protocol.VerifyRequest) (protocol.Verdict, error) {
	stub.request = request
	stub.verdict.Nonce = request.Nonce
	return stub.verdict, nil
}

func pidReference(t *testing.T, pid int32) *workloadattestorv1.AttestReferenceRequest {
	t.Helper()
	reference, err := anypb.New(&broker.WorkloadPIDReference{Pid: pid})
	if err != nil {
		t.Fatal(err)
	}
	return &workloadattestorv1.AttestReferenceRequest{Reference: reference}
}

func assertStrings(t *testing.T, got, want []string) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("selectors = %v, want %v", got, want)
	}
	for index := range want {
		if got[index] != want[index] {
			t.Fatalf("selectors = %v, want %v", got, want)
		}
	}
}
