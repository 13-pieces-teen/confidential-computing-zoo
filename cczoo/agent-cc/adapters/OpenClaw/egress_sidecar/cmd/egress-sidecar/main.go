package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net/url"
	"os"
	"os/signal"
	"path"
	"strings"
	"syscall"
	"time"
	"unicode"

	"github.com/confidential-containers/agent-cc-argus-spiffe/adapters/OpenClaw/egress_sidecar/internal/sidecar"
	"github.com/spiffe/go-spiffe/v2/spiffeid"
)

type options struct {
	workloadAPIAddress string
	brokerSocketPath   string
	brokerSPIFFEID     string
	agentSPIFFEID      string
	targetSPIFFEID     string
	serverSPIFFEID     string
	targetPID          int
	listenAddress      string
	target             string
	guardURL           string
	guardTokenFile     string
	targetService      string
	dataClass          string
	guardTimeout       time.Duration
	gracefulShutdown   time.Duration
}

func main() {
	config, err := parseConfig()
	if err != nil {
		log.Fatal(err)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := sidecar.Run(ctx, config); err != nil {
		log.Fatal(err)
	}
}

func parseConfig() (sidecar.Config, error) {
	var value options
	flag.StringVar(&value.workloadAPIAddress, "workload-api", "unix:///opt/spire/run/agent/agent.sock", "SPIRE Workload API address")
	flag.StringVar(&value.brokerSocketPath, "broker-socket", "/opt/spire/run/broker/broker.sock", "SPIRE Broker Endpoint Unix socket")
	flag.StringVar(&value.brokerSPIFFEID, "broker-spiffe-id", "spiffe://argus.local/infra/openclaw-broker", "exact SPIFFE ID assigned to this Egress Broker")
	flag.StringVar(&value.agentSPIFFEID, "agent-spiffe-id", "", "exact SPIRE Agent SPIFFE ID served by the Broker Endpoint")
	flag.StringVar(&value.targetSPIFFEID, "target-spiffe-id", "spiffe://argus.local/agent/openclaw", "exact OpenClaw SPIFFE ID selected from Broker snapshots")
	flag.StringVar(&value.serverSPIFFEID, "server-spiffe-id", "spiffe://argus.local/service/openviking-cmem", "exact OpenViking server SPIFFE ID")
	flag.IntVar(&value.targetPID, "target-pid", 0, "OpenClaw host PID")
	flag.StringVar(&value.listenAddress, "listen", "0.0.0.0:1934", "OpenClaw plugin HTTP listen address")
	flag.StringVar(&value.target, "target", "https://openviking.argus.local:1943", "OpenViking mTLS origin")
	flag.StringVar(&value.guardURL, "guard-url", "http://argus-dual-openclaw-guard:8007/guard/v1/authorize", "caller-local Guard authorization URL")
	flag.StringVar(&value.guardTokenFile, "guard-token-file", "/run/secrets/argus_guard_api_token", "Guard bearer token file")
	flag.StringVar(&value.targetService, "target-service", "openviking-cmem", "Guard target service")
	flag.StringVar(&value.dataClass, "data-class", "sensitive", "Guard data class")
	flag.DurationVar(&value.guardTimeout, "guard-timeout", 2*time.Second, "Guard request timeout")
	flag.DurationVar(&value.gracefulShutdown, "graceful-shutdown", 5*time.Second, "HTTP graceful shutdown timeout")
	flag.Parse()

	if !path.IsAbs(value.brokerSocketPath) || !path.IsAbs(value.guardTokenFile) {
		return sidecar.Config{}, fmt.Errorf("broker-socket and guard-token-file must be absolute")
	}
	if value.agentSPIFFEID == "" || value.targetPID <= 0 {
		return sidecar.Config{}, fmt.Errorf("agent-spiffe-id and a positive target-pid are required")
	}
	brokerID, err := spiffeid.FromString(value.brokerSPIFFEID)
	if err != nil {
		return sidecar.Config{}, fmt.Errorf("broker-spiffe-id: %w", err)
	}
	agentID, err := spiffeid.FromString(value.agentSPIFFEID)
	if err != nil {
		return sidecar.Config{}, fmt.Errorf("agent-spiffe-id: %w", err)
	}
	targetID, err := spiffeid.FromString(value.targetSPIFFEID)
	if err != nil {
		return sidecar.Config{}, fmt.Errorf("target-spiffe-id: %w", err)
	}
	serverID, err := spiffeid.FromString(value.serverSPIFFEID)
	if err != nil {
		return sidecar.Config{}, fmt.Errorf("server-spiffe-id: %w", err)
	}
	target, err := url.Parse(value.target)
	if err != nil || target.Scheme != "https" || target.Host == "" || target.Path != "" || target.RawQuery != "" || target.Fragment != "" {
		return sidecar.Config{}, fmt.Errorf("target must be a canonical HTTPS origin")
	}
	guardEndpoint, err := url.Parse(value.guardURL)
	if err != nil || (guardEndpoint.Scheme != "http" && guardEndpoint.Scheme != "https") || guardEndpoint.Host == "" {
		return sidecar.Config{}, fmt.Errorf("guard-url must be an HTTP or HTTPS URL")
	}
	tokenBytes, err := os.ReadFile(value.guardTokenFile)
	if err != nil {
		return sidecar.Config{}, fmt.Errorf("read Guard token: %w", err)
	}
	token := strings.TrimRight(string(tokenBytes), "\r\n")
	if token == "" || strings.IndexFunc(token, unicode.IsSpace) >= 0 {
		return sidecar.Config{}, fmt.Errorf("Guard token must be non-empty and contain no whitespace")
	}
	if value.guardTimeout <= 0 || value.gracefulShutdown <= 0 {
		return sidecar.Config{}, fmt.Errorf("timeouts must be positive")
	}
	return sidecar.Config{
		WorkloadAPIAddress: value.workloadAPIAddress, BrokerSocketPath: value.brokerSocketPath,
		BrokerSPIFFEID: brokerID, AgentSPIFFEID: agentID, TargetSPIFFEID: targetID,
		ExpectedServerSPIFFEID: serverID, TargetPID: value.targetPID, ListenAddress: value.listenAddress,
		OpenVikingTarget: target, GuardURL: value.guardURL, GuardToken: token,
		TargetService: value.targetService, DataClass: value.dataClass, GuardTimeout: value.guardTimeout,
		GracefulShutdownTimeout: value.gracefulShutdown,
	}, nil
}
