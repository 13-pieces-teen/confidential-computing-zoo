package clientcredentials

import (
	"context"
	"errors"
	"net"
	"os"
	"strings"
	"testing"
	"time"

	api "github.com/spiffe/go-spiffe/v2/exp/proto/spiffe/broker"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/test/bufconn"
	"google.golang.org/protobuf/types/known/anypb"
)

type freshnessAPI struct {
	api.UnimplementedAPIServer
	response *api.SubscribeToX509SVIDResponse
}

func (s *freshnessAPI) SubscribeToX509SVID(req *api.SubscribeToX509SVIDRequest, stream grpc.ServerStreamingServer[api.SubscribeToX509SVIDResponse]) error {
	md, _ := metadata.FromIncomingContext(stream.Context())
	var pid api.WorkloadPIDReference
	if len(md.Get("broker.spiffe.io")) != 1 || md.Get("broker.spiffe.io")[0] != "true" || req.Reference.Reference.UnmarshalTo(&pid) != nil || pid.Pid != 1234 {
		return errors.New("invalid Broker request metadata/reference")
	}
	if s.response != nil {
		if err := stream.Send(s.response); err != nil {
			return err
		}
	}
	<-stream.Context().Done()
	return stream.Context().Err()
}

func TestFreshnessUsesBoundedRealGRPCSubscription(t *testing.T) {
	for _, mode := range []string{"healthy", "removed", "stalled"} {
		t.Run(mode, func(t *testing.T) {
			listener := bufconn.Listen(1 << 20)
			server := grpc.NewServer()
			service := &freshnessAPI{}
			if mode == "healthy" {
				service.response = snapshot(t)
			}
			if mode == "removed" {
				service.response = &api.SubscribeToX509SVIDResponse{}
			}
			api.RegisterAPIServer(server, service)
			go func() { _ = server.Serve(listener) }()
			defer server.Stop()
			connection, err := grpc.NewClient("passthrough:///test", grpc.WithContextDialer(func(context.Context, string) (net.Conn, error) {
				return listener.Dial()
			}), grpc.WithTransportCredentials(insecure.NewCredentials()))
			if err != nil {
				t.Fatal(err)
			}
			defer connection.Close()
			ctx, cancel := context.WithTimeout(context.Background(), 250*time.Millisecond)
			defer cancel()
			ref, _ := anypb.New(&api.WorkloadPIDReference{Pid: 1234})
			err = probeBroker(ctx, api.NewAPIClient(connection), ref, clientID)
			if (err == nil) != (mode == "healthy") {
				t.Fatalf("%s: %v", mode, err)
			}
			if mode == "stalled" && !strings.Contains(err.Error(), "DeadlineExceeded") {
				t.Fatalf("not deadline bounded: %v", err)
			}
		})
	}
}

func TestStalledBrokerCannotRenewCachedCredentialsForever(t *testing.T) {
	ctx, cancel := context.WithCancelCause(context.Background())
	defer cancel(nil)
	watchdog := time.AfterFunc(3*time.Second, func() { cancel(errors.New("test watchdog")) })
	defer watchdog.Stop()
	p := &publisher{dir: t.TempDir(), gid: -1}
	first := snapshot(t)
	calls := 0
	recv := func() (*api.SubscribeToX509SVIDResponse, error) {
		calls++
		if calls == 1 {
			return first, nil
		}
		<-ctx.Done()
		return nil, context.Cause(ctx)
	}
	go superviseBroker(ctx, cancel, 50*time.Millisecond, 30*time.Millisecond, func(probeCtx context.Context) error {
		// A blackholed Broker never delivers the first snapshot of a new RPC.
		<-probeCtx.Done()
		return probeCtx.Err()
	})
	err := consume(ctx, recv, nil, p, clientID, func() error { return nil })
	if err == nil || !strings.Contains(err.Error(), "Broker freshness check failed") {
		t.Fatalf("unexpected error: %v", err)
	}
	entries, err := os.ReadDir(p.dir)
	if err != nil || len(entries) != 0 {
		t.Fatalf("stale credentials survived: %v %v", entries, err)
	}
}

func TestHealthyIdleBrokerDoesNotTriggerFailure(t *testing.T) {
	ctx, cancel := context.WithCancelCause(context.Background())
	defer cancel(nil)
	done := make(chan struct{})
	calls := 0
	go func() {
		superviseBroker(ctx, cancel, time.Millisecond, 10*time.Millisecond, func(context.Context) error {
			calls++
			if calls == 3 {
				cancel(errors.New("test complete"))
			}
			return nil
		})
		close(done)
	}()
	select {
	case <-done:
		if calls != 3 || context.Cause(ctx).Error() != "test complete" {
			t.Fatal("healthy probe caused disconnect")
		}
	case <-time.After(time.Second):
		t.Fatal("supervision did not terminate")
	}
}
