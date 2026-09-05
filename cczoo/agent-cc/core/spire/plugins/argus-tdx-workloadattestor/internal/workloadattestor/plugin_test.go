package workloadattestor

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/confidential-containers/agent-cc-argus-spiffe/core/spire/plugins/argus-tdx-workloadattestor/internal/protocol"
	"github.com/spiffe/go-spiffe/v2/exp/proto/spiffe/broker"
	v1 "github.com/spiffe/spire-plugin-sdk/proto/spire/plugin/agent/workloadattestor/v1"
	"google.golang.org/protobuf/types/known/anypb"
	"os"
	"slices"
	"testing"
)

type collector func(context.Context, protocol.EvidenceRequest) (protocol.Evidence, error)

func (f collector) Collect(c context.Context, r protocol.EvidenceRequest) (protocol.Evidence, error) {
	return f(c, r)
}

type verifier func(context.Context, protocol.Evidence) error

func (f verifier) Verify(c context.Context, e protocol.Evidence) error { return f(c, e) }
func fixture(t *testing.T) protocol.RuntimeData {
	b, err := os.ReadFile("../../../../workload/testdata/runtime-data.json")
	if err != nil {
		t.Fatal(err)
	}
	var v struct {
		RuntimeData protocol.RuntimeData `json:"runtime_data"`
	}
	if err = json.Unmarshal(b, &v); err != nil {
		t.Fatal(err)
	}
	return v.RuntimeData
}
func configured(t *testing.T) *Plugin {
	d := fixture(t)
	p := New(collector(func(_ context.Context, r protocol.EvidenceRequest) (protocol.Evidence, error) {
		d.Nonce = r.Nonce
		return protocol.Evidence{EvidenceType: "tdx_quote", Quote: "AQID", RuntimeData: d}, nil
	}), verifier(func(context.Context, protocol.Evidence) error { return nil }))
	p.config = &Config{TargetRegistrationPath: "/target.json", WorkloadID: d.WorkloadID, PolicyID: d.PolicyID, ImageConfigDigest: d.ImageConfigDigest, ConfigDigest: d.ConfigDigest}
	p.load = func(string) (protocol.Target, error) { return d.Target, nil }
	p.check = func(protocol.Target) error { return nil }
	return p
}
func request(t *testing.T) *v1.AttestReferenceRequest {
	ref, err := anypb.New(&broker.WorkloadPIDReference{Pid: 1234})
	if err != nil {
		t.Fatal(err)
	}
	return &v1.AttestReferenceRequest{Reference: ref}
}
func TestOnlyVerifiedReferenceProducesTrustedSelectors(t *testing.T) {
	p := configured(t)
	r, err := p.AttestReference(context.Background(), request(t))
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Contains(r.SelectorValues, "verified:true") || !slices.Contains(r.SelectorValues, "image_config_digest:"+fixture(t).ImageConfigDigest) {
		t.Fatal(r)
	}
	ordinary, err := p.Attest(context.Background(), &v1.AttestRequest{Pid: 1234})
	if err != nil || len(ordinary.SelectorValues) != 0 {
		t.Fatal("ordinary Workload API received trusted selectors")
	}
}
func TestEvidenceAndInstanceFailuresNeverYieldSelectors(t *testing.T) {
	cases := map[string]func(*Plugin){
		"trustee denial": func(p *Plugin) {
			p.trustee = verifier(func(context.Context, protocol.Evidence) error { return fmt.Errorf("EAR denied") })
		},
		"provider unavailable": func(p *Plugin) {
			p.evidence = collector(func(context.Context, protocol.EvidenceRequest) (protocol.Evidence, error) {
				return protocol.Evidence{}, fmt.Errorf("TSM failed")
			})
		},
		"baseline image":  func(p *Plugin) { p.config.ImageConfigDigest = "sha256:wrong" },
		"baseline config": func(p *Plugin) { p.config.ConfigDigest = "sha256:wrong" },
		"baseline policy": func(p *Plugin) { p.config.PolicyID = "wrong" },
		"target exited":   func(p *Plugin) { p.check = func(protocol.Target) error { return fmt.Errorf("pidfd exited") } },
		"target changed after quote": func(p *Plugin) {
			n := 0
			p.check = func(protocol.Target) error {
				n++
				if n == 2 {
					return fmt.Errorf("instance changed")
				}
				return nil
			}
		},
	}
	for _, field := range []string{"nonce", "pid", "launch", "image", "config", "policy", "namespace"} {
		field := field
		cases[field] = func(p *Plugin) {
			previous := p.evidence
			p.evidence = collector(func(c context.Context, r protocol.EvidenceRequest) (protocol.Evidence, error) {
				e, err := previous.Collect(c, r)
				switch field {
				case "nonce":
					e.RuntimeData.Nonce = "wrong"
				case "pid":
					e.RuntimeData.PID = "999"
				case "launch":
					e.RuntimeData.LaunchID = "replacement"
				case "image":
					e.RuntimeData.ImageConfigDigest = "wrong"
				case "config":
					e.RuntimeData.ConfigDigest = "wrong"
				case "policy":
					e.RuntimeData.PolicyID = "wrong"
				case "namespace":
					e.RuntimeData.PIDNamespace = "pid:[9]"
				}
				return e, err
			})
		}
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			p := configured(t)
			mutate(p)
			r, err := p.AttestReference(context.Background(), request(t))
			if err == nil || r != nil {
				t.Fatal("untrusted evidence yielded selectors")
			}
		})
	}
}
func TestNonceIsFresh(t *testing.T) {
	a, _ := newNonce()
	b, _ := newNonce()
	if a == b || len(a) != 43 {
		t.Fatal("nonce not fresh")
	}
}
