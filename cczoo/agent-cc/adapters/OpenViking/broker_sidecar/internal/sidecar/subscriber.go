package sidecar

import (
	"context"
	"fmt"
	"log"
	"time"

	"github.com/spiffe/go-spiffe/v2/exp/proto/spiffe/broker"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/anypb"
)

const brokerRetryDelay = time.Second

type BrokerSubscriber struct {
	client broker.APIClient
	pid    int32
	store  *IdentityStore
}

func NewBrokerSubscriber(client broker.APIClient, pid int32, store *IdentityStore) *BrokerSubscriber {
	return &BrokerSubscriber{client: client, pid: pid, store: store}
}

func (subscriber *BrokerSubscriber) Run(ctx context.Context) error {
	for {
		err := subscriber.subscribe(ctx)
		subscriber.store.Clear()
		if ctx.Err() != nil {
			return nil
		}
		if status.Code(err) == codes.PermissionDenied {
			return fmt.Errorf("Broker subscription denied: %w", err)
		}
		log.Printf("Broker subscription ended; identity cleared; retrying in %s: %v", brokerRetryDelay, err)
		timer := time.NewTimer(brokerRetryDelay)
		select {
		case <-ctx.Done():
			timer.Stop()
			return nil
		case <-timer.C:
		}
	}
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
		found, err := subscriber.store.ApplySnapshot(response)
		if err != nil {
			return fmt.Errorf("apply Broker identity snapshot: %w", err)
		}
		if !found {
			log.Printf("Broker snapshot does not contain the target OpenViking identity; new mTLS handshakes are blocked")
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
