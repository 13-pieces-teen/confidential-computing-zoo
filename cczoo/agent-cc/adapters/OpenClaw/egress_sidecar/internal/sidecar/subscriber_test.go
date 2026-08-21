package sidecar

import (
	"context"
	"errors"
	"io"
	"testing"

	"github.com/spiffe/go-spiffe/v2/exp/proto/spiffe/broker"
	"google.golang.org/grpc"
	"google.golang.org/grpc/metadata"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/anypb"
)

func TestBrokerRequestReferencesOpenClawPID(t *testing.T) {
	ctx, request, err := brokerRequest(context.Background(), 4321)
	if err != nil {
		t.Fatal(err)
	}
	outgoing, ok := metadata.FromOutgoingContext(ctx)
	if !ok || len(outgoing.Get("broker.spiffe.io")) != 1 || outgoing.Get("broker.spiffe.io")[0] != "true" {
		t.Fatalf("broker metadata = %v", outgoing)
	}
	packed := request.GetReference().GetReference()
	var pidReference broker.WorkloadPIDReference
	if err := anypb.UnmarshalTo(packed, &pidReference, proto.UnmarshalOptions{}); err != nil {
		t.Fatal(err)
	}
	if pidReference.Pid != 4321 {
		t.Fatalf("PID = %d", pidReference.Pid)
	}
}

func TestBrokerSubscriptionTerminationClearsIdentityAndReturns(t *testing.T) {
	const targetID = "spiffe://argus.local/agent/openclaw"
	store := NewIdentityStore(targetID)
	stream := &terminatingSVIDStream{responses: []*broker.SubscribeToX509SVIDResponse{{
		Svids: []*broker.X509SVID{testBrokerSVID(t, targetID)},
	}}}
	subscriber := NewBrokerSubscriber(&terminatingBrokerClient{stream: stream}, 4321, store)
	err := subscriber.Run(context.Background())
	if !errors.Is(err, io.EOF) {
		t.Fatalf("subscription error = %v, want EOF", err)
	}
	if store.Current() != nil {
		t.Fatal("identity remained installed after Broker subscription termination")
	}
}

type terminatingBrokerClient struct {
	broker.APIClient
	stream grpc.ServerStreamingClient[broker.SubscribeToX509SVIDResponse]
}

func (client *terminatingBrokerClient) SubscribeToX509SVID(
	context.Context,
	*broker.SubscribeToX509SVIDRequest,
	...grpc.CallOption,
) (grpc.ServerStreamingClient[broker.SubscribeToX509SVIDResponse], error) {
	return client.stream, nil
}

type terminatingSVIDStream struct {
	grpc.ServerStreamingClient[broker.SubscribeToX509SVIDResponse]
	responses []*broker.SubscribeToX509SVIDResponse
}

func (stream *terminatingSVIDStream) Recv() (*broker.SubscribeToX509SVIDResponse, error) {
	if len(stream.responses) == 0 {
		return nil, io.EOF
	}
	response := stream.responses[0]
	stream.responses = stream.responses[1:]
	return response, nil
}
