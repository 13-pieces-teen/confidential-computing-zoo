// Copyright (c) 2026 Intel Corporation
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//    http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

//! argus-docker-gate — minimal Docker socket proxy for the OpenClaw gateway.
//!
//! The OpenClaw sandbox runtime talks to the Docker daemon over a Unix socket
//! (DooD). WP2 isolates that control plane by replacing the raw daemon socket
//! with this proxy, which enforces:
//!   * a strict endpoint allowlist (only sandbox-lifecycle and read-only ops);
//!   * safety validation on `POST /containers/create` (no privileged, no host
//!     networking, no capability additions, no arbitrary host mounts, no
//!     device mounts, only the configured sandbox image).
//! Anything outside the allowlist or that violates the create policy is
//! rejected with HTTP 403 and an audit line. Everything else is streamed
//! through to the real daemon socket.

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"regexp"
	"strings"
	"time"
)

type config struct {
	listen          string // unix:///path/to/proxy.sock
	upstream        string // unix:///path/to/docker.sock
	allowedImage    string // only this image may be used in containers/create
	ownerLabelKey   string // label injected into and required on managed sandboxes
	ownerLabelValue string // run-scoped owner value for managed sandboxes
	socketGID       int    // group owner of the proxy socket
	logPath         string // optional audit log path
}

var cfg config
var audit *log.Logger

// containerCreateBody captures only the fields the proxy must validate.
type containerCreateBody struct {
	Image      string `json:"Image"`
	HostConfig struct {
		Privileged  bool     `json:"Privileged"`
		NetworkMode string   `json:"NetworkMode"`
		CapAdd      []string `json:"CapAdd"`
		SecurityOpt []string `json:"SecurityOpt"`
		Binds       []string `json:"Binds"`
		Devices     []struct {
			PathOnHost string `json:"PathOnHost"`
		} `json:"Devices"`
	} `json:"HostConfig"`
}

func parseFlags() {
	flag.StringVar(&cfg.listen, "listen", "unix:///var/run/argus/docker-proxy.sock",
		"Unix socket the proxy listens on")
	flag.StringVar(&cfg.upstream, "upstream", "unix:///var/run/docker.sock",
		"Real Docker daemon Unix socket")
	flag.StringVar(&cfg.allowedImage, "allowed-image", "openclaw-sandbox:bookworm-slim",
		"Only image permitted in containers/create")
	flag.StringVar(&cfg.ownerLabelKey, "owner-label-key", "argus.openclaw.sandbox.owner",
		"Docker label key injected into and required on managed sandbox containers")
	flag.StringVar(&cfg.ownerLabelValue, "owner-label-value", "",
		"Run-scoped Docker label value injected into and required on managed sandbox containers")
	flag.IntVar(&cfg.socketGID, "socket-gid", 0,
		"Group ID applied to the proxy socket (0 = do not chown)")
	flag.StringVar(&cfg.logPath, "log", "",
		"Optional audit log path (default: stdout)")
	flag.Parse()
}

// apiVersionRe matches the Docker CLI API-version prefix, e.g. /v1.54/... .
var apiVersionRe = regexp.MustCompile(`^/v[0-9]+\.[0-9]+(/.*)?$`)

// stripAPIVersion removes a leading Docker API version segment for matching.
func stripAPIVersion(path string) string {
	if m := apiVersionRe.FindStringSubmatch(path); m != nil {
		if m[1] == "" {
			return "/"
		}
		return m[1]
	}
	return path
}

// allowDecision reports whether a request passes the allowlist, plus an
// operation label for audit and, on denial, the reason.
func allowDecision(method, path string) (string, bool, string) {
	// The Docker CLI negotiates an API version and prefixes paths (e.g.
	// /v1.54/containers/json). Classify on the version-stripped path while
	// forwarding the original request unchanged.
	path = stripAPIVersion(path)
	// Read-only control-plane endpoints.
	switch {
	case method == "GET" && path == "/_ping":
		return "ping", true, ""
	case method == "GET" && path == "/version":
		return "version", true, ""
	case method == "GET" && path == "/info":
		return "info", true, ""
	case method == "GET" && path == "/events":
		return "events", true, ""
	}
	// Read-only image inspection (harmless metadata reads).
	if method == "GET" && strings.HasPrefix(path, "/images/") && strings.HasSuffix(path, "/json") {
		return "image_inspect", true, ""
	}
	if method == "GET" && path == "/images/json" {
		return "images_list", true, ""
	}
	// Container operations.
	if method == "POST" && path == "/containers/create" {
		return "containers_create", true, ""
	}
	if method == "GET" && path == "/containers/json" {
		return "containers_list", true, ""
	}
	if sub, ok := containerSubPath(path); ok {
		switch {
		case method == "DELETE" && sub == "":
			return "containers_rm", true, ""
		case method == "POST" && (sub == "start" || sub == "stop" || sub == "restart" || sub == "kill"):
			return "containers_" + sub, true, ""
		case method == "POST" && sub == "wait":
			return "containers_wait", true, ""
		case method == "POST" && sub == "exec":
			return "containers_exec", true, ""
		case method == "POST" && sub == "rename":
			return "containers_rename", true, ""
		case method == "GET" && (sub == "json" || sub == "logs" || sub == "attach" || sub == "attach/ws"):
			return "containers_" + strings.ReplaceAll(sub, "/", "_"), true, ""
		case method == "POST" && sub == "attach":
			return "containers_attach", true, ""
		case (method == "GET" || method == "PUT") && sub == "archive":
			return "containers_archive", true, ""
		}
		return "containers_" + strings.ReplaceAll(sub, "/", "_"), false,
			fmt.Sprintf("container sub-operation %q is not allowed", sub)
	}
	// Exec operations.
	if sub, ok := execSubPath(path); ok {
		switch {
		case method == "POST" && sub == "start":
			return "exec_start", true, ""
		case method == "GET" && sub == "json":
			return "exec_inspect", true, ""
		}
		return "exec_" + strings.ReplaceAll(sub, "/", "_"), false,
			fmt.Sprintf("exec sub-operation %q is not allowed", sub)
	}
	return "other", false, fmt.Sprintf("operation %s %s is not allowed", method, path)
}

// containerSubPath returns the part of a /containers/{id}/... path after the
// container id. A plain /containers/{id} returns "".
func containerSubPath(path string) (string, bool) {
	rest, ok := strings.CutPrefix(path, "/containers/")
	if !ok {
		return "", false
	}
	id, sub, found := strings.Cut(rest, "/")
	if id == "" {
		return "", false
	}
	if !found {
		return "", true
	}
	return sub, true
}

// execSubPath returns the part of an /exec/{id}/... path after the exec id.
func execSubPath(path string) (string, bool) {
	rest, ok := strings.CutPrefix(path, "/exec/")
	if !ok {
		return "", false
	}
	id, sub, found := strings.Cut(rest, "/")
	if id == "" {
		return "", false
	}
	if !found {
		return "", true
	}
	return sub, true
}

// validateCreate enforces the containers/create safety policy.
func validateCreate(body []byte) (string, error) {
	var req containerCreateBody
	if err := json.Unmarshal(body, &req); err != nil {
		return "", fmt.Errorf("malformed create request: %w", err)
	}
	if req.Image != cfg.allowedImage {
		return "", fmt.Errorf("image %q is not allowed (expected %q)", req.Image, cfg.allowedImage)
	}
	if req.HostConfig.Privileged {
		return "", fmt.Errorf("privileged containers are forbidden")
	}
	mode := strings.ToLower(strings.TrimSpace(req.HostConfig.NetworkMode))
	if mode != "" && mode != "none" && mode != "default" {
		return "", fmt.Errorf("network mode %q is forbidden (only none/default)", req.HostConfig.NetworkMode)
	}
	if len(req.HostConfig.CapAdd) > 0 {
		return "", fmt.Errorf("cap_add is forbidden: %v", req.HostConfig.CapAdd)
	}
	for _, opt := range req.HostConfig.SecurityOpt {
		if strings.Contains(strings.ToLower(opt), "unconfined") {
			return "", fmt.Errorf("security_opt %q is forbidden", opt)
		}
	}
	for _, bind := range req.HostConfig.Binds {
		source := bind
		if i := strings.IndexByte(bind, ':'); i >= 0 {
			source = bind[:i]
		}
		if strings.HasPrefix(source, "/") && !strings.HasPrefix(source, "/home/node/.openclaw/") {
			return "", fmt.Errorf("bind source %q is outside the gateway workspace", source)
		}
	}
	if len(req.HostConfig.Devices) > 0 {
		return "", fmt.Errorf("device mounts are forbidden")
	}
	return "allowed", nil
}

func prepareCreate(body []byte) ([]byte, error) {
	if _, err := validateCreate(body); err != nil {
		return nil, err
	}
	var request map[string]json.RawMessage
	if err := json.Unmarshal(body, &request); err != nil {
		return nil, fmt.Errorf("malformed create request: %w", err)
	}
	labels := make(map[string]string)
	if rawLabels, ok := request["Labels"]; ok && string(rawLabels) != "null" {
		if err := json.Unmarshal(rawLabels, &labels); err != nil {
			return nil, fmt.Errorf("malformed create labels: %w", err)
		}
	}
	labels[cfg.ownerLabelKey] = cfg.ownerLabelValue
	encodedLabels, err := json.Marshal(labels)
	if err != nil {
		return nil, fmt.Errorf("encode managed sandbox labels: %w", err)
	}
	request["Labels"] = encodedLabels
	prepared, err := json.Marshal(request)
	if err != nil {
		return nil, fmt.Errorf("encode managed sandbox request: %w", err)
	}
	return prepared, nil
}

type dockerTargetAuthorizer struct {
	client *http.Client
}

type dockerContainerInspect struct {
	ID     string `json:"Id"`
	Config struct {
		Labels map[string]string `json:"Labels"`
	} `json:"Config"`
}

type dockerExecInspect struct {
	ContainerID string `json:"ContainerID"`
}

func newDockerTransport() *http.Transport {
	return &http.Transport{
		DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			return (&net.Dialer{}).DialContext(ctx, "unix", strings.TrimPrefix(cfg.upstream, "unix://"))
		},
	}
}

func newDockerTargetAuthorizer() *dockerTargetAuthorizer {
	return &dockerTargetAuthorizer{
		client: &http.Client{Transport: newDockerTransport(), Timeout: 5 * time.Second},
	}
}

func (authorizer *dockerTargetAuthorizer) authorizeContainer(ctx context.Context, target string) error {
	var inspect dockerContainerInspect
	if err := authorizer.getJSON(ctx, "/containers/"+url.PathEscape(target)+"/json", &inspect); err != nil {
		return fmt.Errorf("inspect container target: %w", err)
	}
	if inspect.Config.Labels[cfg.ownerLabelKey] != cfg.ownerLabelValue {
		return fmt.Errorf("container target is not managed by this gate")
	}
	return nil
}

func (authorizer *dockerTargetAuthorizer) authorizeExec(ctx context.Context, target string) error {
	var inspect dockerExecInspect
	if err := authorizer.getJSON(ctx, "/exec/"+url.PathEscape(target)+"/json", &inspect); err != nil {
		return fmt.Errorf("inspect exec target: %w", err)
	}
	if inspect.ContainerID == "" {
		return fmt.Errorf("exec target has no parent container")
	}
	return authorizer.authorizeContainer(ctx, inspect.ContainerID)
}

func (authorizer *dockerTargetAuthorizer) getJSON(ctx context.Context, path string, output any) error {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, "http://docker"+path, nil)
	if err != nil {
		return err
	}
	response, err := authorizer.client.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
		return fmt.Errorf("Docker returned HTTP %d", response.StatusCode)
	}
	decoder := json.NewDecoder(io.LimitReader(response.Body, 1<<20))
	if err := decoder.Decode(output); err != nil {
		return err
	}
	return nil
}

func containerTarget(path string) (string, bool) {
	rest, ok := strings.CutPrefix(stripAPIVersion(path), "/containers/")
	if !ok {
		return "", false
	}
	target, _, _ := strings.Cut(rest, "/")
	if target == "" {
		return "", false
	}
	decoded, err := url.PathUnescape(target)
	return decoded, err == nil
}

func execTarget(path string) (string, bool) {
	rest, ok := strings.CutPrefix(stripAPIVersion(path), "/exec/")
	if !ok {
		return "", false
	}
	target, _, _ := strings.Cut(rest, "/")
	if target == "" {
		return "", false
	}
	decoded, err := url.PathUnescape(target)
	return decoded, err == nil
}

func auditLog(op string, r *http.Request, decision, reason string) {
	audit.Printf("operation=%s method=%s path=%s decision=%s reason=%s",
		op, r.Method, r.URL.RequestURI(), decision, reason)
}

func writeDenied(w http.ResponseWriter, reason string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusForbidden)
	_ = json.NewEncoder(w).Encode(map[string]string{
		"message": "blocked by argus-docker-gate: " + reason,
	})
}

func newProxy() *httputil.ReverseProxy {
	proxy := &httputil.ReverseProxy{
		FlushInterval: -1, // flush immediately for streaming (logs/events/attach)
		Director: func(req *http.Request) {
			req.URL.Scheme = "http"
			req.URL.Host = "docker"
		},
		Transport: newDockerTransport(),
	}
	return proxy
}

func handler(proxy *httputil.ReverseProxy, authorizer *dockerTargetAuthorizer) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		op, allowed, reason := allowDecision(r.Method, r.URL.Path)
		if !allowed {
			auditLog(op, r, "deny", reason)
			writeDenied(w, reason)
			return
		}
		if target, targeted := containerTarget(r.URL.Path); targeted &&
			op != "containers_create" && op != "containers_list" {
			if err := authorizer.authorizeContainer(r.Context(), target); err != nil {
				auditLog(op, r, "deny", err.Error())
				writeDenied(w, err.Error())
				return
			}
		} else if target, targeted := execTarget(r.URL.Path); targeted {
			if err := authorizer.authorizeExec(r.Context(), target); err != nil {
				auditLog(op, r, "deny", err.Error())
				writeDenied(w, err.Error())
				return
			}
		}
		if op == "containers_create" {
			body, err := io.ReadAll(r.Body)
			if err != nil {
				auditLog(op, r, "deny", "read create body failed")
				writeDenied(w, "read create request body failed")
				return
			}
			body, err = prepareCreate(body)
			if err != nil {
				auditLog(op, r, "deny", err.Error())
				writeDenied(w, err.Error())
				return
			}
			r.Body = io.NopCloser(bytes.NewReader(body))
			r.ContentLength = int64(len(body))
		}
		auditLog(op, r, "allow", "")
		proxy.ServeHTTP(w, r)
	})
}

func main() {
	parseFlags()
	if !strings.HasPrefix(cfg.listen, "unix://") {
		log.Fatalf("listen must be a unix:// socket: %s", cfg.listen)
	}
	if !strings.HasPrefix(cfg.upstream, "unix://") {
		log.Fatalf("upstream must be a unix:// socket: %s", cfg.upstream)
	}
	if strings.TrimSpace(cfg.ownerLabelKey) == "" || strings.ContainsAny(cfg.ownerLabelKey, "=,\x00\r\n") {
		log.Fatalf("owner-label-key must be a non-empty Docker label key")
	}
	if strings.TrimSpace(cfg.ownerLabelValue) == "" || strings.ContainsAny(cfg.ownerLabelValue, "\x00\r\n") {
		log.Fatalf("owner-label-value must be explicitly set to a non-empty run-scoped value")
	}

	if cfg.logPath != "" {
		f, err := os.OpenFile(cfg.logPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o640)
		if err != nil {
			log.Fatalf("open audit log: %v", err)
		}
		defer f.Close()
		audit = log.New(f, "", log.LstdFlags)
	} else {
		audit = log.New(os.Stdout, "", log.LstdFlags)
	}

	socketPath := strings.TrimPrefix(cfg.listen, "unix://")
	_ = os.Remove(socketPath)
	listener, err := net.Listen("unix", socketPath)
	if err != nil {
		log.Fatalf("listen on %s: %v", socketPath, err)
	}
	defer listener.Close()
	if cfg.socketGID > 0 {
		if err := os.Chown(socketPath, 0, cfg.socketGID); err != nil {
			log.Fatalf("chown proxy socket: %v", err)
		}
	}
	if err := os.Chmod(socketPath, 0o660); err != nil {
		log.Fatalf("chmod proxy socket: %v", err)
	}

	server := &http.Server{
		Handler:           handler(newProxy(), newDockerTargetAuthorizer()),
		ReadHeaderTimeout: 10 * time.Second,
	}
	audit.Printf("argus-docker-gate listening on %s (upstream %s, allowed-image %q, owner-label %s=%s)",
		cfg.listen, cfg.upstream, cfg.allowedImage, cfg.ownerLabelKey, cfg.ownerLabelValue)
	if err := server.Serve(listener); err != nil && err != http.ErrServerClosed {
		log.Fatalf("serve: %v", err)
	}
}
