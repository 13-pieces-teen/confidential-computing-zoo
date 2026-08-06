package main

import (
	"os"
	"strings"
	"testing"
)

func TestMain(m *testing.M) {
	// Tests exercise validateCreate directly; set the default policy config.
	cfg.allowedImage = "openclaw-sandbox:bookworm-slim"
	os.Exit(m.Run())
}

func TestAllowDecisionAllowed(t *testing.T) {
	cases := [][2]string{
		{"GET", "/_ping"},
		{"GET", "/version"},
		{"GET", "/info"},
		{"GET", "/events"},
		{"GET", "/images/json"},
		{"GET", "/images/openclaw-sandbox:bookworm-slim/json"},
		{"GET", "/containers/json"},
		{"POST", "/containers/create"},
		{"GET", "/containers/abc123/json"},
		{"GET", "/containers/abc123/logs"},
		{"POST", "/containers/abc123/start"},
		{"POST", "/containers/abc123/stop"},
		{"POST", "/containers/abc123/restart"},
		{"POST", "/containers/abc123/kill"},
		{"POST", "/containers/abc123/wait"},
		{"POST", "/containers/abc123/exec"},
		{"GET", "/containers/abc123/attach"},
		{"POST", "/containers/abc123/attach"},
		{"GET", "/containers/abc123/attach/ws"},
		{"GET", "/containers/abc123/archive"},
		{"PUT", "/containers/abc123/archive"},
		{"DELETE", "/containers/abc123"},
		{"POST", "/exec/xyz/start"},
		{"GET", "/exec/xyz/json"},
		// Docker CLI version-prefixed paths must classify identically.
		{"GET", "/v1.54/version"},
		{"GET", "/v1.54/containers/json"},
		{"POST", "/v1.54/containers/create"},
		{"GET", "/v1.54/containers/abc123/logs"},
		{"POST", "/v1.54/containers/abc123/exec"},
	}
	for _, c := range cases {
		op, allowed, reason := allowDecision(c[0], c[1])
		if !allowed {
			t.Errorf("expected %s %s to be allowed, got denied: %s (op=%s)", c[0], c[1], reason, op)
		}
	}
}

func TestAllowDecisionDenied(t *testing.T) {
	cases := [][2]string{
		{"POST", "/images/create"}, // image pull
		{"POST", "/images/openclaw-sandbox:bookworm-slim/push"},
		{"POST", "/build"},           // docker build
		{"POST", "/networks/create"}, // network management
		{"POST", "/volumes/create"},  // volume management
		{"POST", "/containers/abc123/prune"},
		{"POST", "/auth"},                    // docker login
		{"GET", "/containers/abc123/stats"},  // not in minimal allowlist
		{"GET", "/containers/abc123/rename"}, // rename is POST only
		{"GET", "/containers/abc123"},        // inspect is /json
		{"POST", "/containers/abc123/copy"},  // legacy copy endpoint
		{"GET", "/system/df"},                // system usage
		{"DELETE", "/networks/foo"},
	}
	for _, c := range cases {
		op, allowed, _ := allowDecision(c[0], c[1])
		if allowed {
			t.Errorf("expected %s %s to be denied, got allowed (op=%s)", c[0], c[1], op)
		}
	}
}

func TestValidateCreateAllowed(t *testing.T) {
	body := `{
		"Image": "openclaw-sandbox:bookworm-slim",
		"HostConfig": {
			"NetworkMode": "none",
			"SecurityOpt": ["no-new-privileges"],
			"Binds": ["/home/node/.openclaw/workspace:/workspace:z"]
		}
	}`
	if _, err := validateCreate([]byte(body)); err != nil {
		t.Errorf("expected safe create to be allowed, got: %v", err)
	}
}

func TestValidateCreateDenied(t *testing.T) {
	cases := []struct {
		name string
		body string
		part string
	}{
		{"privileged", `{"Image":"openclaw-sandbox:bookworm-slim","HostConfig":{"Privileged":true}}`, "privileged"},
		{"wrong image", `{"Image":"alpine"}`, "not allowed"},
		{"host network", `{"Image":"openclaw-sandbox:bookworm-slim","HostConfig":{"NetworkMode":"host"}}`, "network mode"},
		{"identity network", `{"Image":"openclaw-sandbox:bookworm-slim","HostConfig":{"NetworkMode":"argus-openclaw-egress"}}`, "network mode"},
		{"cap add", `{"Image":"openclaw-sandbox:bookworm-slim","HostConfig":{"CapAdd":["SYS_ADMIN"]}}`, "cap_add"},
		{"unconfined seccomp", `{"Image":"openclaw-sandbox:bookworm-slim","HostConfig":{"SecurityOpt":["seccomp:unconfined"]}}`, "unconfined"},
		{"host bind", `{"Image":"openclaw-sandbox:bookworm-slim","HostConfig":{"Binds":["/etc:/etc"]}}`, "outside the gateway workspace"},
		{"device", `{"Image":"openclaw-sandbox:bookworm-slim","HostConfig":{"Devices":[{"PathOnHost":"/dev/sda"}]}}`, "device mounts"},
	}
	for _, c := range cases {
		_, err := validateCreate([]byte(c.body))
		if err == nil {
			t.Errorf("%s: expected denial, got allowed", c.name)
			continue
		}
		if !strings.Contains(err.Error(), c.part) {
			t.Errorf("%s: denial reason %q does not mention %q", c.name, err.Error(), c.part)
		}
	}
}
