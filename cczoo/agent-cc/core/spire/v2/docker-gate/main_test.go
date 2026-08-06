package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"net/http/httputil"
	"net/url"
	"os"
	"strings"
	"sync/atomic"
	"testing"
)

func TestMain(m *testing.M) {
	// Tests exercise validateCreate directly; set the default policy config.
	cfg.allowedImage = "openclaw-sandbox:bookworm-slim"
	cfg.ownerLabelKey = "argus.openclaw.sandbox.owner"
	cfg.ownerLabelValue = "test-runtime"
	audit = log.New(io.Discard, "", 0)
	os.Exit(m.Run())
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

func jsonResponse(status int, body string) *http.Response {
	return &http.Response{
		StatusCode: status,
		Header:     make(http.Header),
		Body:       io.NopCloser(strings.NewReader(body)),
	}
}

func testGateHandler(t *testing.T, dockerAPI http.RoundTripper, downstream http.Handler) http.Handler {
	t.Helper()
	server := httptest.NewServer(downstream)
	t.Cleanup(server.Close)
	target, err := url.Parse(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	proxy := httputil.NewSingleHostReverseProxy(target)
	authorizer := &dockerTargetAuthorizer{client: &http.Client{Transport: dockerAPI}}
	return handler(proxy, authorizer)
}

func TestGateDeniesOperationsAgainstUnmanagedContainers(t *testing.T) {
	var forwarded atomic.Int32
	gate := testGateHandler(t, roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if request.URL.Path != "/containers/argus-v2-spire-server/json" {
			t.Fatalf("unexpected Docker inspection path: %s", request.URL.Path)
		}
		return jsonResponse(http.StatusOK, `{"Id":"infra","Config":{"Labels":{"com.docker.compose.service":"spire-server"}}}`), nil
	}), http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		forwarded.Add(1)
	}))

	request := httptest.NewRequest(http.MethodPost, "/containers/argus-v2-spire-server/exec", strings.NewReader(`{"Cmd":["sh"]}`))
	response := httptest.NewRecorder()
	gate.ServeHTTP(response, request)

	if response.Code != http.StatusForbidden {
		t.Fatalf("unmanaged infrastructure container returned HTTP %d, expected 403", response.Code)
	}
	if forwarded.Load() != 0 {
		t.Fatal("unmanaged infrastructure operation reached the Docker daemon")
	}
}

func TestGateAllowsOperationsOnlyAgainstManagedSandboxes(t *testing.T) {
	var forwarded atomic.Int32
	gate := testGateHandler(t, roundTripFunc(func(request *http.Request) (*http.Response, error) {
		return jsonResponse(http.StatusOK, `{"Id":"sandbox","Config":{"Labels":{"argus.openclaw.sandbox.owner":"test-runtime"}}}`), nil
	}), http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		forwarded.Add(1)
		response.WriteHeader(http.StatusNoContent)
	}))

	request := httptest.NewRequest(http.MethodPost, "/containers/openclaw-sandbox-123/restart", nil)
	response := httptest.NewRecorder()
	gate.ServeHTTP(response, request)

	if response.Code != http.StatusNoContent {
		t.Fatalf("managed sandbox returned HTTP %d, expected 204", response.Code)
	}
	if forwarded.Load() != 1 {
		t.Fatalf("managed sandbox operation was forwarded %d times, expected once", forwarded.Load())
	}
}

func TestGateInjectsUnspoofableOwnerLabelOnCreate(t *testing.T) {
	var forwardedBody []byte
	gate := testGateHandler(t, roundTripFunc(func(request *http.Request) (*http.Response, error) {
		t.Fatalf("create must not perform target inspection: %s", request.URL.Path)
		return nil, nil
	}), http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		var err error
		forwardedBody, err = io.ReadAll(request.Body)
		if err != nil {
			response.WriteHeader(http.StatusInternalServerError)
			return
		}
		response.WriteHeader(http.StatusCreated)
	}))

	body := `{"Image":"openclaw-sandbox:bookworm-slim","Labels":{"caller":"kept","argus.openclaw.sandbox.owner":"spoofed"},"HostConfig":{"NetworkMode":"none"}}`
	request := httptest.NewRequest(http.MethodPost, "/containers/create", strings.NewReader(body))
	response := httptest.NewRecorder()
	gate.ServeHTTP(response, request)

	if response.Code != http.StatusCreated {
		t.Fatalf("safe create returned HTTP %d, expected 201", response.Code)
	}
	var forwarded struct {
		Labels map[string]string `json:"Labels"`
	}
	if err := json.NewDecoder(bytes.NewReader(forwardedBody)).Decode(&forwarded); err != nil {
		t.Fatal(err)
	}
	if forwarded.Labels[cfg.ownerLabelKey] != cfg.ownerLabelValue {
		t.Fatalf("owner label = %q, expected %q", forwarded.Labels[cfg.ownerLabelKey], cfg.ownerLabelValue)
	}
	if forwarded.Labels["caller"] != "kept" {
		t.Fatal("existing caller label was not preserved")
	}
}

func TestGateBindsExecSessionsToManagedParentContainer(t *testing.T) {
	var forwarded atomic.Int32
	gate := testGateHandler(t, roundTripFunc(func(request *http.Request) (*http.Response, error) {
		switch request.URL.Path {
		case "/exec/exec-from-infra/json":
			return jsonResponse(http.StatusOK, `{"ContainerID":"spire-server-id"}`), nil
		case "/containers/spire-server-id/json":
			return jsonResponse(http.StatusOK, `{"Id":"spire-server-id","Config":{"Labels":{}}}`), nil
		default:
			t.Fatalf("unexpected Docker inspection path: %s", request.URL.Path)
			return nil, nil
		}
	}), http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		forwarded.Add(1)
	}))

	request := httptest.NewRequest(http.MethodPost, "/exec/exec-from-infra/start", strings.NewReader(`{}`))
	response := httptest.NewRecorder()
	gate.ServeHTTP(response, request)

	if response.Code != http.StatusForbidden {
		t.Fatalf("exec session from unmanaged parent returned HTTP %d, expected 403", response.Code)
	}
	if forwarded.Load() != 0 {
		t.Fatal("exec session from unmanaged parent reached the Docker daemon")
	}
}

func TestAuthorizerFailsClosedWhenDockerInspectionIsUnavailable(t *testing.T) {
	authorizer := &dockerTargetAuthorizer{client: &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
		return jsonResponse(http.StatusServiceUnavailable, `{}`), nil
	})}}
	if err := authorizer.authorizeContainer(context.Background(), "sandbox"); err == nil {
		t.Fatal("Docker inspection failure was accepted")
	}
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
