package sidecar

import (
	"context"
	"fmt"

	"github.com/spiffe/go-spiffe/v2/exp/proto/spiffe/broker"
	"google.golang.org/grpc/metadata"
	"google.golang.org/protobuf/types/known/anypb"
)

type BrokerSubscriber struct {
	client broker.APIClient
	pid    int32
	store  *IdentityStore
}

func NewBrokerSubscriber(client broker.APIClient, pid int32, store *IdentityStore) *BrokerSubscriber {
	return &BrokerSubscriber{client: client, pid: pid, store: store}
}

func (subscriber *BrokerSubscriber) Run(ctx context.Context) error {
	defer subscriber.store.Clear()
	err := subscriber.subscribe(ctx)
	if ctx.Err() != nil {
		return nil
	}
	return err
}

func (subscriber *BrokerSubscriber) subscribe(ctx context.Context) error {
	requestContext, request, err := brokerRequest(ctx, subscriber.pid)
	if err != nil {
		return err
	}
	stream, err := subscriber.client.SubscribeToX509SVID(requestContext, request)
	if err != nil {
		return err
	}
	for {
		response, err := stream.Recv()
		if err != nil {
			return err
		}
		_, err = subscriber.store.ApplySnapshot(response)
		if err != nil {
			return fmt.Errorf("apply Broker identity snapshot: %w", err)
		}
	}
}

func brokerRequest(ctx context.Context, pid int32) (context.Context, *broker.SubscribeToX509SVIDRequest, error) {
	if pid <= 0 {
		return nil, nil, fmt.Errorf("target PID must be positive")
	}
	reference, err := anypb.New(&broker.WorkloadPIDReference{Pid: pid})
	if err != nil {
		return nil, nil, fmt.Errorf("pack workload PID reference: %w", err)
	}
	ctx = metadata.AppendToOutgoingContext(ctx, "broker.spiffe.io", "true")
	return ctx, &broker.SubscribeToX509SVIDRequest{
		Reference: &broker.WorkloadReference{Reference: reference},
	}, nil
}
