package sidecar

import (
	"context"
	"testing"

	"github.com/spiffe/go-spiffe/v2/exp/proto/spiffe/broker"
	"google.golang.org/grpc/metadata"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/anypb"
)

func TestBrokerRequestCarriesPIDReferenceAndRequiredMetadata(t *testing.T) {
	ctx, request, err := brokerRequest(context.Background(), 4321)
	if err != nil {
		t.Fatal(err)
	}
	outgoing, ok := metadata.FromOutgoingContext(ctx)
	if !ok {
		t.Fatal("broker metadata is missing")
	}
	values := outgoing.Get("broker.spiffe.io")
	if len(values) != 1 || values[0] != "true" {
		t.Fatalf("broker metadata = %v", values)
	}
	packed := request.GetReference().GetReference()
	if packed.GetTypeUrl() != "type.googleapis.com/spiffe.broker.WorkloadPIDReference" {
		t.Fatalf("reference type = %q", packed.GetTypeUrl())
	}
	var pidReference broker.WorkloadPIDReference
	if err := anypb.UnmarshalTo(packed, &pidReference, proto.UnmarshalOptions{}); err != nil {
		t.Fatal(err)
	}
	if pidReference.Pid != 4321 {
		t.Fatalf("PID = %d", pidReference.Pid)
	}
}
