package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"math"
	"net"
	"net/url"
	"os"
	"os/signal"
	"path"
	"syscall"
	"time"

	"github.com/confidential-containers/agent-cc-argus-spiffe/adapters/OpenViking/broker_sidecar/internal/sidecar"
	"github.com/spiffe/go-spiffe/v2/spiffeid"
)

type options struct {
	workloadAPIAddress     string
	brokerSocketPath       string
	brokerSPIFFEID         string
	agentSPIFFEID          string
	targetSPIFFEID         string
	expectedClientSPIFFEID string
	targetPID              int
	listenAddress          string
	upstream               string
}

func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
}

func run() error {
	options := parseFlags()
	config, err := makeConfig(options)
	if err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	return sidecar.Run(ctx, config)
}

func parseFlags() options {
	var result options
	flag.StringVar(&result.workloadAPIAddress, "workload-api", "unix:///opt/spire/run/agent/agent.sock", "SPIRE Workload API address")
	flag.StringVar(&result.brokerSocketPath, "broker-socket", "/opt/spire/run/broker/broker.sock", "SPIRE Broker Endpoint Unix socket")
	flag.StringVar(&result.brokerSPIFFEID, "broker-spiffe-id", "spiffe://argus.local/infra/openviking-broker", "exact SPIFFE ID assigned to this Broker Sidecar")
	flag.StringVar(&result.agentSPIFFEID, "agent-spiffe-id", "", "exact SPIRE Agent SPIFFE ID served by the Broker Endpoint")
	flag.StringVar(&result.targetSPIFFEID, "target-spiffe-id", "spiffe://argus.local/service/openviking-cmem", "exact OpenViking SPIFFE ID to select from Broker snapshots")
	flag.StringVar(&result.expectedClientSPIFFEID, "client-spiffe-id", "spiffe://argus.local/agent/openclaw", "exact client SPIFFE ID allowed on the mTLS listener")
	flag.IntVar(&result.targetPID, "target-pid", 0, "OpenViking Python host PID")
	flag.StringVar(&result.listenAddress, "listen", "0.0.0.0:1943", "OpenViking mTLS listen address")
	flag.StringVar(&result.upstream, "upstream", "http://127.0.0.1:1933", "internal OpenViking HTTP upstream")
	flag.Parse()
	return result
}

func makeConfig(options options) (sidecar.Config, error) {
	brokerID, err := spiffeid.FromString(options.brokerSPIFFEID)
	if err != nil {
		return sidecar.Config{}, fmt.Errorf("broker-spiffe-id: %w", err)
	}
	agentID, err := spiffeid.FromString(options.agentSPIFFEID)
	if err != nil {
		return sidecar.Config{}, fmt.Errorf("agent-spiffe-id: %w", err)
	}
	targetID, err := spiffeid.FromString(options.targetSPIFFEID)
	if err != nil {
		return sidecar.Config{}, fmt.Errorf("target-spiffe-id: %w", err)
	}
	clientID, err := spiffeid.FromString(options.expectedClientSPIFFEID)
	if err != nil {
		return sidecar.Config{}, fmt.Errorf("client-spiffe-id: %w", err)
	}
	if options.targetPID <= 0 || options.targetPID > math.MaxInt32 {
		return sidecar.Config{}, fmt.Errorf("target-pid must fit a positive int32")
	}
	if !path.IsAbs(options.brokerSocketPath) {
		return sidecar.Config{}, fmt.Errorf("broker-socket must be absolute")
	}
	if _, _, err := net.SplitHostPort(options.listenAddress); err != nil {
		return sidecar.Config{}, fmt.Errorf("listen: %w", err)
	}
	upstream, err := url.Parse(options.upstream)
	if err != nil || upstream.Scheme != "http" || upstream.Host == "" {
		return sidecar.Config{}, fmt.Errorf("upstream must be an HTTP URL")
	}
	host := upstream.Hostname()
	if host != "localhost" {
		address := net.ParseIP(host)
		if address == nil || !address.IsLoopback() {
			return sidecar.Config{}, fmt.Errorf("upstream must use a loopback address")
		}
	}
	return sidecar.Config{
		WorkloadAPIAddress:      options.workloadAPIAddress,
		BrokerSocketPath:        options.brokerSocketPath,
		BrokerSPIFFEID:          brokerID,
		AgentSPIFFEID:           agentID,
		TargetSPIFFEID:          targetID,
		ExpectedClientSPIFFEID:  clientID,
		TargetPID:               options.targetPID,
		ListenAddress:           options.listenAddress,
		OpenVikingUpstream:      upstream,
		GracefulShutdownTimeout: 5 * time.Second,
	}, nil
}
