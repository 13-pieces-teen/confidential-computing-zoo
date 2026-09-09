package clientcredentials

import (
	"context"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"log"
	"net"
	"os"
	"time"

	api "github.com/spiffe/go-spiffe/v2/exp/proto/spiffe/broker"
	"github.com/spiffe/go-spiffe/v2/spiffeid"
	"github.com/spiffe/go-spiffe/v2/spiffetls/tlsconfig"
	"github.com/spiffe/go-spiffe/v2/svid/x509svid"
	"github.com/spiffe/go-spiffe/v2/workloadapi"
	"github.com/spiffe/spiffe-helper/pkg/broker"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/metadata"
	"google.golang.org/protobuf/types/known/anypb"
)

func certificateSerial(data []byte) (string, error) {
	block, _ := pem.Decode(data)
	if block == nil {
		return "", fmt.Errorf("missing PEM certificate")
	}
	cert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		return "", err
	}
	return cert.SerialNumber.Text(16), nil
}

func Run(ctx context.Context, c Config) (result error) {
	if err := c.validate(); err != nil {
		return err
	}
	if err := os.MkdirAll(c.Directory, 0750); err != nil {
		return err
	}
	if err := checkFilesystem(c.Directory); err != nil {
		return err
	}
	p := &publisher{dir: c.Directory, gid: c.ReaderGID}
	if err := p.access(p.dir, 0750); err != nil {
		return err
	}
	if err := p.clear(); err != nil {
		return err
	}
	defer func() { result = errors.Join(result, p.clear()) }()
	ctx, cancel := context.WithCancelCause(ctx)
	defer cancel(nil)
	if err := protected(c.Registration, false); err != nil {
		return err
	}
	data, err := os.ReadFile(c.Registration)
	if err != nil {
		return err
	}
	var target Target
	if err = json.Unmarshal(data, &target); err != nil {
		return err
	}
	ended, err := watch(ctx, target)
	if err != nil {
		return err
	}
	// Target exit must cancel startup too, including a Workload API that has
	// not delivered the publisher's own identity yet.
	go func() {
		select {
		case err := <-ended:
			cancel(err)
		case <-ctx.Done():
		}
	}()
	startup := time.AfterFunc(60*time.Second, func() { cancel(fmt.Errorf("Workload API/Broker startup timed out")) })
	defer startup.Stop()
	helperID, _ := spiffeid.FromString(c.HelperID)
	agentID, _ := spiffeid.FromString(c.AgentID)
	targetID, _ := spiffeid.FromString(c.TargetID)
	source, err := workloadapi.NewX509Source(ctx,
		workloadapi.WithClientOptions(workloadapi.WithAddr(c.WorkloadAPI)),
		workloadapi.WithDefaultX509SVIDPicker(func(svids []*x509svid.SVID) *x509svid.SVID {
			for _, svid := range svids {
				if svid.ID == helperID {
					return svid
				}
			}
			return nil
		}))
	if err != nil {
		return errors.Join(err, context.Cause(ctx))
	}
	defer source.Close()
	checkSelf := func() error {
		if ctx.Err() != nil {
			return context.Cause(ctx)
		}
		current, err := observe(target.PID)
		if err != nil || current != target {
			return fmt.Errorf("registered OpenClaw instance changed")
		}
		s, err := source.GetX509SVID()
		if err != nil {
			return err
		}
		if s.ID != helperID || !time.Now().Before(s.Certificates[0].NotAfter) {
			return fmt.Errorf("publisher identity unavailable")
		}
		return nil
	}
	if err = checkSelf(); err != nil {
		return err
	}
	connection, err := grpc.NewClient("passthrough:///openclaw-broker",
		grpc.WithContextDialer(func(ctx context.Context, _ string) (net.Conn, error) {
			return (&net.Dialer{}).DialContext(ctx, "unix", c.BrokerSocket)
		}),
		grpc.WithTransportCredentials(credentials.NewTLS(tlsconfig.MTLSClientConfig(source, source, tlsconfig.AuthorizeID(agentID)))),
		grpc.WithDefaultCallOptions(grpc.MaxCallRecvMsgSize(4<<20)))
	if err != nil {
		return err
	}
	defer connection.Close()
	ref, err := anypb.New(&api.WorkloadPIDReference{Pid: int32(target.PID)})
	if err != nil {
		return err
	}
	// SPIRE's default HTTP/2 ping enforcement rejects aggressive keepalives.
	// Fresh, bounded Broker subscriptions prove application-level progress even
	// when the original stream and its cached identity appear healthy.
	client := api.NewAPIClient(connection)
	go superviseBroker(ctx, cancel, 5*time.Second, 3*time.Second, func(probeCtx context.Context) error {
		return probeBroker(probeCtx, client, ref, targetID)
	})
	stream, err := client.SubscribeToX509SVID(metadata.AppendToOutgoingContext(ctx, "broker.spiffe.io", "true"),
		&api.SubscribeToX509SVIDRequest{Reference: &api.WorkloadReference{Reference: ref}})
	if err != nil {
		return errors.Join(err, context.Cause(ctx))
	}
	startup.Stop()
	log.Printf("OpenClaw identity subscription pid=%d start_time=%s boot_id=%s target=%s", target.PID, target.StartTime, target.BootID, c.TargetID)
	return consume(ctx, stream.Recv, nil, p, targetID, checkSelf)
}

func probeBroker(ctx context.Context, client api.APIClient, ref *anypb.Any, targetID spiffeid.ID) error {
	stream, err := client.SubscribeToX509SVID(metadata.AppendToOutgoingContext(ctx, "broker.spiffe.io", "true"),
		&api.SubscribeToX509SVIDRequest{Reference: &api.WorkloadReference{Reference: ref}})
	if err != nil {
		return err
	}
	snapshot, err := stream.Recv()
	if err != nil {
		return err
	}
	_, err = broker.Snapshot(snapshot, targetID, time.Now())
	return err
}

func superviseBroker(ctx context.Context, cancel context.CancelCauseFunc, interval, timeout time.Duration, probe func(context.Context) error) {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			probeCtx, stop := context.WithTimeout(ctx, timeout)
			err := probe(probeCtx)
			stop()
			if err != nil {
				cancel(fmt.Errorf("Broker freshness check failed: %w", err))
				return
			}
		}
	}
}

func consume(ctx context.Context, recv func() (*api.SubscribeToX509SVIDResponse, error), ended <-chan error, p *publisher, targetID spiffeid.ID, self func() error) (result error) {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	defer func() { result = errors.Join(result, p.clear()) }()
	type update struct {
		snapshot *api.SubscribeToX509SVIDResponse
		err      error
	}
	updates := make(chan update)
	go func() {
		for {
			snapshot, err := recv()
			select {
			case updates <- update{snapshot, err}:
			case <-ctx.Done():
				return
			}
			if err != nil {
				return
			}
		}
	}()
	tick := time.NewTicker(500 * time.Millisecond)
	defer tick.Stop()
	firstDeadline := time.Now().Add(60 * time.Second)
	for {
		select {
		case <-ctx.Done():
			return context.Cause(ctx)
		case err := <-ended:
			return fmt.Errorf("target instance ended: %w", err)
		case <-tick.C:
			if p.current == nil && time.Now().After(firstDeadline) {
				return fmt.Errorf("no target identity received")
			}
			if err := self(); err != nil {
				return err
			}
			if err := p.renew(time.Now()); err != nil {
				return err
			}
		case next := <-updates:
			if next.err != nil {
				return fmt.Errorf("Broker subscription ended: %w", next.err)
			}
			material, err := broker.Snapshot(next.snapshot, targetID, time.Now())
			if err != nil {
				return err
			}
			if err = self(); err != nil {
				return err
			}
			if err = p.publish(material, time.Now()); err != nil {
				return err
			}
			log.Printf("OpenClaw SVID published serial=%s expires=%s; rotation is not new attestation", material.Serial, material.Expires.UTC().Format(time.RFC3339))
		}
	}
}

// Clear is also used by systemd ExecStopPost after an uncatchable process exit.
func Clear(c Config) error {
	if err := c.validate(); err != nil {
		return err
	}
	if err := checkFilesystem(c.Directory); err != nil {
		return err
	}
	return (&publisher{dir: c.Directory, gid: c.ReaderGID}).clear()
}
