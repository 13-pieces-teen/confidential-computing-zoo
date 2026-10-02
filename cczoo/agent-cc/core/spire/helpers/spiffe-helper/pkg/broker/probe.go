package broker

// Probe is an explicit experiment mode of the already approved Helper binary.
// It obtains one Broker snapshot without publishing credentials, notifying
// readiness, installing a listener, or invoking a reload/stop hook.
import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"net"
	"net/url"
	"regexp"
	"strconv"
	"time"

	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/workload/protocol"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/workload/target"
	api "github.com/spiffe/go-spiffe/v2/exp/proto/spiffe/broker"
	"github.com/spiffe/go-spiffe/v2/spiffeid"
	"github.com/spiffe/go-spiffe/v2/spiffetls/tlsconfig"
	"github.com/spiffe/go-spiffe/v2/svid/x509svid"
	"github.com/spiffe/go-spiffe/v2/workloadapi"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/anypb"
)

type ProbeResult struct {
	Schema                  string           `json:"schema"`
	RunID                   string           `json:"run_id"`
	SubscriptionID          string           `json:"subscription_id"`
	Result                  string           `json:"result"`
	Stage                   string           `json:"stage"`
	StartedAtMS             int64            `json:"started_at_ms"`
	CompletedAtMS           int64            `json:"completed_at_ms"`
	SubscriptionStartedAtMS int64            `json:"subscription_started_at_ms,omitempty"`
	Target                  *protocol.Target `json:"target,omitempty"`
	Serial                  string           `json:"serial,omitempty"`
	ErrorCode               string           `json:"error_code,omitempty"`
	Scope                   string           `json:"scope"`
}

func Probe(parent context.Context, agentAddress, certDir string, c Config, runID string, timeout time.Duration) (r ProbeResult) {
	r = ProbeResult{Schema: "argus.subscription-probe.v1", RunID: runID, Result: "UNKNOWN", Stage: "configuration", StartedAtMS: time.Now().UnixMilli(),
		Scope: "one separate Broker subscription; no publication/hooks; identity receipt is not independent policy admission"}
	defer func() { r.CompletedAtMS = time.Now().UnixMilli() }()
	if !regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$`).MatchString(runID) || timeout <= 0 || timeout > 5*time.Minute {
		r.ErrorCode = "invalid_probe_configuration"
		return
	}
	if err := c.Validate(certDir); err != nil {
		r.ErrorCode = "invalid_broker_configuration"
		return
	}
	id := make([]byte, 16)
	if _, err := rand.Read(id); err != nil {
		r.ErrorCode = "random_source_unavailable"
		return
	}
	r.SubscriptionID = hex.EncodeToString(id)
	ctx, cancel := context.WithTimeout(parent, timeout)
	defer cancel()
	r.Stage = "target_check"
	t, err := target.Load(c.TargetRegistrationPath)
	if err != nil || t.AgentID != c.AgentSPIFFEID || t.WorkloadID != c.WorkloadID {
		r.ErrorCode = "target_registration_invalid"
		return
	}
	if err = target.Check(t); err != nil {
		r.ErrorCode = "target_check_failed"
		return
	}
	r.Target = &t
	r.Stage = "helper_identity"
	helperID := spiffeid.RequireFromString(c.HelperSPIFFEID)
	source, err := workloadapi.NewX509Source(ctx, workloadapi.WithClientOptions(workloadapi.WithAddr(agentAddress)),
		workloadapi.WithDefaultX509SVIDPicker(func(svids []*x509svid.SVID) *x509svid.SVID {
			for _, s := range svids {
				if s.ID == helperID {
					return s
				}
			}
			return nil
		}))
	if err != nil {
		r.ErrorCode = status.Code(err).String()
		return
	}
	defer source.Close()
	self, err := source.GetX509SVID()
	if err != nil || self.ID != helperID {
		r.ErrorCode = "helper_identity_unavailable"
		return
	}
	r.Stage = "broker_connect"
	ep, _ := url.Parse(c.Endpoint)
	conn, err := grpc.NewClient("passthrough:///argus-broker", grpc.WithTransportCredentials(credentials.NewTLS(
		tlsconfig.MTLSClientConfig(source, source, tlsconfig.AuthorizeID(spiffeid.RequireFromString(c.AgentSPIFFEID))))),
		grpc.WithContextDialer(func(ctx context.Context, _ string) (net.Conn, error) {
			return (&net.Dialer{}).DialContext(ctx, "unix", ep.Path)
		}),
		grpc.WithDefaultCallOptions(grpc.MaxCallRecvMsgSize(4<<20)))
	if err != nil {
		r.ErrorCode = status.Code(err).String()
		return
	}
	defer conn.Close()
	pid, _ := strconv.ParseInt(t.PID, 10, 32)
	ref, err := anypb.New(&api.WorkloadPIDReference{Pid: int32(pid)})
	if err != nil {
		r.ErrorCode = "invalid_pid_reference"
		return
	}
	r.Stage = "subscription"
	r.SubscriptionStartedAtMS = time.Now().UnixMilli()
	stream, err := api.NewAPIClient(conn).SubscribeToX509SVID(metadata.AppendToOutgoingContext(ctx, "broker.spiffe.io", "true"), &api.SubscribeToX509SVIDRequest{Reference: &api.WorkloadReference{Reference: ref}})
	if err != nil {
		r.ErrorCode = status.Code(err).String()
		return
	}
	response, err := stream.Recv()
	if err != nil {
		r.ErrorCode = status.Code(err).String()
		return
	}
	r.Stage = "snapshot_and_target_recheck"
	serial, err := probeSnapshot(response, spiffeid.RequireFromString(c.TargetSPIFFEID), t, func() (protocol.Target, error) {
		current, e := target.Load(c.TargetRegistrationPath)
		if e != nil {
			return current, e
		}
		return current, target.Check(current)
	})
	if err != nil {
		r.ErrorCode = "snapshot_or_target_changed"
		return
	}
	r.Serial = serial
	r.Stage = "complete"
	r.Result = "OBSERVED"
	return
}

func probeSnapshot(response *api.SubscribeToX509SVIDResponse, id spiffeid.ID, before protocol.Target, check func() (protocol.Target, error)) (string, error) {
	c, err := Snapshot(response, id, time.Now())
	if err != nil {
		return "", err
	}
	after, err := check()
	if err != nil {
		return "", err
	}
	if before != after {
		return "", fmt.Errorf("target changed during subscription")
	}
	return c.Serial, nil
}
