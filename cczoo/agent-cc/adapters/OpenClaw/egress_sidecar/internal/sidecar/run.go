package sidecar

import (
	"context"
	"errors"
	"fmt"
	"log"
	"net"
	"net/http"
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
	ExpectedServerSPIFFEID  spiffeid.ID
	TargetPID               int
	ListenAddress           string
	OpenVikingTarget        *url.URL
	GuardURL                string
	GuardToken              string
	TargetService           string
	DataClass               string
	GuardTimeout            time.Duration
	GracefulShutdownTimeout time.Duration
}

func Run(ctx context.Context, config Config) error {
	target, err := OpenTarget(config.TargetPID)
	if err != nil {
		return err
	}
	defer target.Close()

	brokerSource, err := workloadapi.NewX509Source(ctx, workloadapi.WithClientOptions(workloadapi.WithAddr(config.WorkloadAPIAddress)))
	if err != nil {
		return fmt.Errorf("obtain Egress Broker X.509-SVID from Workload API: %w", err)
	}
	defer brokerSource.Close()
	brokerSVID, err := brokerSource.GetX509SVID()
	if err != nil {
		return fmt.Errorf("read Egress Broker X.509-SVID: %w", err)
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
			brokerSource,
			brokerSource,
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
		if err == nil {
			return nil
		}
		return fmt.Errorf("obtain OpenClaw identity through Broker API: %w", err)
	case err := <-targetResult:
		return targetExitError(config.TargetPID, err)
	case <-ctx.Done():
		return nil
	}

	transport := &http.Transport{TLSClientConfig: store.ClientTLSConfig(config.ExpectedServerSPIFFEID.String())}
	defer transport.CloseIdleConnections()
	handler := NewProxyHandler(ProxyConfig{
		Target:         config.OpenVikingTarget,
		Transport:      transport,
		GuardURL:       config.GuardURL,
		GuardToken:     config.GuardToken,
		CallerSPIFFEID: config.TargetSPIFFEID.String(),
		TargetSPIFFEID: config.ExpectedServerSPIFFEID.String(),
		TargetService:  config.TargetService,
		TargetOrigin:   config.OpenVikingTarget.String(),
		DataClass:      config.DataClass,
		GuardTimeout:   config.GuardTimeout,
	})
	listener, err := net.Listen("tcp", config.ListenAddress)
	if err != nil {
		return fmt.Errorf("listen for OpenClaw egress HTTP: %w", err)
	}
	server := &http.Server{Handler: handler, ReadHeaderTimeout: 10 * time.Second}
	serverResult := make(chan error, 1)
	go func() {
		err := server.Serve(listener)
		if errors.Is(err, http.ErrServerClosed) {
			err = nil
		}
		serverResult <- err
	}()
	log.Printf("OpenClaw Egress Broker is ready for identity %s on %s", config.TargetSPIFFEID, config.ListenAddress)

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
			result = fmt.Errorf("serve OpenClaw egress HTTP: %w", err)
		}
	case <-ctx.Done():
	}

	store.Clear()
	cancel()
	shutdownContext, shutdownCancel := context.WithTimeout(context.Background(), config.GracefulShutdownTimeout)
	defer shutdownCancel()
	if err := server.Shutdown(shutdownContext); err != nil && result == nil {
		result = fmt.Errorf("shut down OpenClaw Egress Broker: %w", err)
	}
	return result
}

func targetExitError(pid int, err error) error {
	if err == nil {
		return fmt.Errorf("OpenClaw target PID %d exited", pid)
	}
	if errors.Is(err, context.Canceled) {
		return nil
	}
	return fmt.Errorf("watch OpenClaw target PID %d: %w", pid, err)
}
