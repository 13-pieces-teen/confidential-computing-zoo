package broker

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/workload/protocol"
	api "github.com/spiffe/go-spiffe/v2/exp/proto/spiffe/broker"
	"github.com/spiffe/go-spiffe/v2/spiffeid"
	"strings"
	"testing"
	"time"
)

func TestProbeSnapshotChecksOriginalTargetAndNeverReturnsMaterial(t *testing.T) {
	before := protocol.Target{PID: "42", StartTime: "100", ContainerID: strings.Repeat("a", 64)}
	response := &api.SubscribeToX509SVIDResponse{Svids: []*api.X509SVID{credentialsFor(t, targetURI, 73)}}
	serial, err := probeSnapshot(response, spiffeid.RequireFromString(targetURI), before, func() (protocol.Target, error) { return before, nil })
	if err != nil || serial != "73" {
		t.Fatalf("serial=%s err=%v", serial, err)
	}
	changed := before
	changed.StartTime = "101"
	if _, err = probeSnapshot(response, spiffeid.RequireFromString(targetURI), before, func() (protocol.Target, error) { return changed, nil }); err == nil {
		t.Fatal("target replacement accepted")
	}
	if _, err = probeSnapshot(response, spiffeid.RequireFromString(targetURI), before, func() (protocol.Target, error) { return before, errors.New("gone") }); err == nil {
		t.Fatal("failed current check accepted")
	}
	if _, err = probeSnapshot(&api.SubscribeToX509SVIDResponse{}, spiffeid.RequireFromString(targetURI), before, func() (protocol.Target, error) { return before, nil }); err == nil {
		t.Fatal("missing identity accepted")
	}
	raw, _ := json.Marshal(ProbeResult{Result: "OBSERVED", Serial: serial, Target: &before})
	if strings.Contains(string(raw), "PRIVATE KEY") || strings.Contains(string(raw), "certificate") {
		t.Fatal("credential material exposed")
	}
}

func TestInvalidProbeNeverRunsPublicationAndIsNotDenial(t *testing.T) {
	r := Probe(context.Background(), "unused", "unused", Config{}, "", time.Second)
	if r.Result != "UNKNOWN" || r.Stage != "configuration" || r.ErrorCode != "invalid_probe_configuration" || r.CompletedAtMS < r.StartedAtMS {
		t.Fatalf("%+v", r)
	}
}
