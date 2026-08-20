package sidecar

import (
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"time"

	"github.com/spiffe/go-spiffe/v2/exp/proto/spiffe/broker"
	"github.com/spiffe/go-spiffe/v2/spiffegrpc/grpccredentials"
	"github.com/spiffe/go-spiffe/v2/spiffeid"
	"github.com/spiffe/go-spiffe/v2/spiffetls/tlsconfig"
	"github.com/spiffe/go-spiffe/v2/workloadapi"
	"google.golang.org/grpc"
)

type Config struct {
	WorkloadAPIAddress      string
	BrokerSocketPath        string
	BrokerSPIFFEID          spiffeid.ID
	AgentSPIFFEID           spiffeid.ID
	TargetSPIFFEID          spiffeid.ID
	ExpectedClientSPIFFEID  spiffeid.ID
	TargetPID               int
	ListenAddress           string
	OpenVikingUpstream      *url.URL
	GracefulShutdownTimeout time.Duration
}

func Run(ctx context.Context, config Config) error {
	target, err := OpenTarget(config.TargetPID)
	if err != nil {
		return err
	}
	defer target.Close()

	source, err := workloadapi.NewX509Source(ctx, workloadapi.WithClientOptions(workloadapi.WithAddr(config.WorkloadAPIAddress)))
	if err != nil {
		return fmt.Errorf("obtain Broker X.509-SVID from Workload API: %w", err)
	}
	defer source.Close()
	brokerSVID, err := source.GetX509SVID()
	if err != nil {
		return fmt.Errorf("read Broker X.509-SVID: %w", err)
	}
	if brokerSVID.ID != config.BrokerSPIFFEID {
		return fmt.Errorf("Workload API returned SPIFFE ID %q, want Broker ID %q", brokerSVID.ID, config.BrokerSPIFFEID)
	}

	connection, err := grpc.NewClient(
		"passthrough:///spire-agent-broker",
		grpc.WithContextDialer(func(ctx context.Context, _ string) (net.Conn, error) {
			return (&net.Dialer{}).DialContext(ctx, "unix", config.BrokerSocketPath)
		}),
		grpc.WithTransportCredentials(grpccredentials.MTLSClientCredentials(
			source,
			source,
			tlsconfig.AuthorizeID(config.AgentSPIFFEID),
		)),
	)
	if err != nil {
		return fmt.Errorf("configure SPIFFE Broker connection: %w", err)
	}
	defer connection.Close()

	runContext, cancel := context.WithCancel(ctx)
	defer cancel()
	store := NewIdentityStore(config.TargetSPIFFEID.String())
	subscriber := NewBrokerSubscriber(broker.NewAPIClient(connection), int32(config.TargetPID), store)
	subscriptionResult := make(chan error, 1)
	go func() { subscriptionResult <- subscriber.Run(runContext) }()
	targetResult := make(chan error, 1)
	go func() { targetResult <- target.Wait(runContext) }()

	select {
	case <-store.Ready():
	case err := <-subscriptionResult:
		return fmt.Errorf("obtain OpenViking identity through Broker API: %w", err)
	case err := <-targetResult:
		return targetExitError(config.TargetPID, err)
	case <-ctx.Done():
		return nil
	}

	listener, err := net.Listen("tcp", config.ListenAddress)
	if err != nil {
		return fmt.Errorf("listen for OpenViking mTLS: %w", err)
	}
	log.Printf("OpenViking mTLS listener is ready for identity %s", config.TargetSPIFFEID)
	proxy := httputil.NewSingleHostReverseProxy(config.OpenVikingUpstream)
	server := &http.Server{
		Handler:           proxy,
		ReadHeaderTimeout: 10 * time.Second,
	}
	serverResult := make(chan error, 1)
	go func() {
		err := server.Serve(tls.NewListener(listener, store.ServerTLSConfig(config.ExpectedClientSPIFFEID.String())))
		if errors.Is(err, http.ErrServerClosed) {
			err = nil
		}
		serverResult <- err
	}()

	var result error
	select {
	case err := <-subscriptionResult:
		if err != nil {
			result = fmt.Errorf("Broker subscription stopped: %w", err)
		}
	case err := <-targetResult:
		result = targetExitError(config.TargetPID, err)
	case err := <-serverResult:
		if err != nil {
			result = fmt.Errorf("serve OpenViking mTLS proxy: %w", err)
		}
	case <-ctx.Done():
	}

	store.Clear()
	cancel()
	shutdownContext, shutdownCancel := context.WithTimeout(context.Background(), config.GracefulShutdownTimeout)
	defer shutdownCancel()
	if err := server.Shutdown(shutdownContext); err != nil && result == nil {
		result = fmt.Errorf("shut down OpenViking mTLS proxy: %w", err)
	}
	return result
}

func targetExitError(pid int, err error) error {
	if err == nil {
		return fmt.Errorf("OpenViking target PID %d exited", pid)
	}
	if errors.Is(err, context.Canceled) {
		return nil
	}
	return fmt.Errorf("watch OpenViking target PID %d: %w", pid, err)
}
