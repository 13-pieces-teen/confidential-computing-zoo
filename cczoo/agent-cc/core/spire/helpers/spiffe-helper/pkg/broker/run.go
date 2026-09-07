package broker

import (
	"context"
	"errors"
	"fmt"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/workload/target"
	api "github.com/spiffe/go-spiffe/v2/exp/proto/spiffe/broker"
	"github.com/spiffe/go-spiffe/v2/spiffeid"
	"github.com/spiffe/go-spiffe/v2/spiffetls/tlsconfig"
	"github.com/spiffe/go-spiffe/v2/svid/x509svid"
	"github.com/spiffe/go-spiffe/v2/workloadapi"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/keepalive"
	"google.golang.org/grpc/metadata"
	"google.golang.org/protobuf/types/known/anypb"
	"log"
	"net"
	"net/url"
	"strconv"
	"time"
)

func Run(ctx context.Context, agentAddress, certDir string, c Config) (result error) {
	if err := c.Validate(certDir); err != nil {
		return err
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	p := NewPublisher(certDir, c.PublishHook)
	if err := p.Prepare(); err != nil {
		return err
	}
	defer func() {
		clearErr := p.Clear()
		stopCtx, stop := context.WithTimeout(context.Background(), 5*time.Second)
		defer stop()
		result = errors.Join(result, clearErr, p.Hook(stopCtx, "stop"))
	}()
	t, err := target.Load(c.TargetRegistrationPath)
	if err != nil {
		return err
	}
	if t.AgentID != c.AgentSPIFFEID || c.TargetSPIFFEID != "spiffe://argus.local/service/"+t.WorkloadID {
		return fmt.Errorf("registered target identity mismatch")
	}
	watchErr, err := target.StartWatch(ctx, t)
	if err != nil {
		return err
	}
	helperID, _ := spiffeid.FromString(c.HelperSPIFFEID)
	agentID, _ := spiffeid.FromString(c.AgentSPIFFEID)
	targetID, _ := spiffeid.FromString(c.TargetSPIFFEID)
	source, err := workloadapi.NewX509Source(ctx, workloadapi.WithClientOptions(workloadapi.WithAddr(agentAddress)), workloadapi.WithDefaultX509SVIDPicker(func(svids []*x509svid.SVID) *x509svid.SVID {
		for _, s := range svids {
			if s.ID == helperID {
				return s
			}
		}
		return nil
	}))
	if err != nil {
		return err
	}
	defer source.Close()
	self, err := source.GetX509SVID()
	if err != nil || self.ID != helperID {
		return fmt.Errorf("Helper identity unavailable")
	}
	ep, _ := url.Parse(c.Endpoint)
	conn, err := grpc.NewClient("passthrough:///argus-broker",
		grpc.WithTransportCredentials(credentials.NewTLS(tlsconfig.MTLSClientConfig(source, source, tlsconfig.AuthorizeID(agentID)))),
		grpc.WithContextDialer(func(ctx context.Context, _ string) (net.Conn, error) {
			return (&net.Dialer{}).DialContext(ctx, "unix", ep.Path)
		}),
		grpc.WithKeepaliveParams(keepalive.ClientParameters{Time: 10 * time.Second, Timeout: 5 * time.Second, PermitWithoutStream: true}),
		grpc.WithDefaultCallOptions(grpc.MaxCallRecvMsgSize(4<<20)))
	if err != nil {
		return err
	}
	defer conn.Close()
	pid, _ := strconv.ParseInt(t.PID, 10, 32)
	ref, err := anypb.New(&api.WorkloadPIDReference{Pid: int32(pid)})
	if err != nil {
		return err
	}
	stream, err := api.NewAPIClient(conn).SubscribeToX509SVID(metadata.AppendToOutgoingContext(ctx, "broker.spiffe.io", "true"), &api.SubscribeToX509SVIDRequest{Reference: &api.WorkloadReference{Reference: ref}})
	if err != nil {
		return err
	}
	log.Printf("workload subscription launch_id=%s container_id=%s pid=%s policy=%s", t.LaunchID, t.ContainerID, t.PID, t.PolicyID)
	return consume(ctx, stream.Recv, watchErr, p, targetID, func() error {
		s, err := source.GetX509SVID()
		if err != nil {
			return err
		}
		if s.ID != helperID || !time.Now().Before(s.Certificates[0].NotAfter) {
			return fmt.Errorf("Helper identity removed or expired")
		}
		return nil
	})
}

// A disconnect exits and clears credentials. systemd may start a new process,
// which performs a new Broker subscription and therefore fresh attestation.
func consume(ctx context.Context, recv func() (*api.SubscribeToX509SVIDResponse, error), targetErrors <-chan error, p *Publisher, id spiffeid.ID, checkSelf func() error) error {
	type message struct {
		snapshot *api.SubscribeToX509SVIDResponse
		err      error
	}
	updates := make(chan message)
	go func() {
		for {
			r, err := recv()
			select {
			case updates <- message{r, err}:
			case <-ctx.Done():
				return
			}
			if err != nil {
				return
			}
		}
	}()
	tick := time.NewTicker(250 * time.Millisecond)
	defer tick.Stop()
	deadline := time.Now().Add(60 * time.Second)
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case err := <-targetErrors:
			return fmt.Errorf("target instance ended: %w", err)
		case <-tick.C:
			if !time.Now().Before(deadline) {
				return fmt.Errorf("target credentials unavailable or expired")
			}
			if err := checkSelf(); err != nil {
				return err
			}
		case m := <-updates:
			if m.err != nil {
				return fmt.Errorf("Broker subscription ended: %w", m.err)
			}
			c, err := Snapshot(m.snapshot, id, time.Now())
			if err != nil {
				return err
			}
			if err = p.Publish(ctx, c); err != nil {
				return err
			}
			deadline = c.Expires
			log.Printf("target SVID published serial=%s expires=%s; certificate update is not a new Quote", c.Serial, c.Expires.UTC().Format(time.RFC3339))
		}
	}
}
