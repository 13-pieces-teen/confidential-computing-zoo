#!/usr/bin/env bash
# Build and start argus-docker-gate, the minimal Docker socket proxy that
# isolates the OpenClaw gateway's Docker control plane (WP2).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="${V2_RUNTIME_DIR:-$SCRIPT_DIR/runtime}"
GO_CACHE_DIR="${ARGUS_GO_CACHE_DIR:-/tmp/argus-spire-v2-go-cache}"
DOCKER_GATE_BIN="$RUNTIME_DIR/plugins/argus-docker-gate"
PROXY_SOCKET="${V2_DOCKER_GATE_SOCKET:-/var/run/argus/docker-proxy.sock}"
PROXY_PID_FILE="${V2_DOCKER_GATE_PID_FILE:-/var/run/argus/argus-docker-gate.pid}"
PROXY_LOG="${V2_DOCKER_GATE_LOG:-/var/log/argus-docker-gate.log}"
ALLOWED_IMAGE="${V2_SANDBOX_IMAGE:-openclaw-sandbox:bookworm-slim}"
DOCKER_SOCKET="${DOCKER_SOCKET:-/var/run/docker.sock}"

fail() {
    printf 'docker-gate: FAIL: %s\n' "$1" >&2
    exit 1
}

[[ "$RUNTIME_DIR" == /* ]] \
    || fail "V2_RUNTIME_DIR must be an absolute host path: $RUNTIME_DIR"
[[ -S "$DOCKER_SOCKET" ]] || fail "Docker daemon socket not found at $DOCKER_SOCKET"
command -v docker >/dev/null 2>&1 || fail "docker not found"
command -v stat >/dev/null 2>&1 || fail "stat not found"

# Build the proxy binary if it is missing.
if [[ ! -x "$DOCKER_GATE_BIN" ]]; then
    echo "Building argus-docker-gate..."
    install -d -m 0755 "$GO_CACHE_DIR" "$RUNTIME_DIR/plugins"
    docker run --rm \
        -e "HTTPS_PROXY=${HTTPS_PROXY:-}" \
        -e "HTTP_PROXY=${HTTP_PROXY:-}" \
        -e "NO_PROXY=${NO_PROXY:-}" \
        -e GOMODCACHE=/gomodcache \
        -e "GOPROXY=${ARGUS_GO_PROXY:-https://proxy.golang.org,direct}" \
        -v "$SCRIPT_DIR/docker-gate:/source:ro" \
        -v "$GO_CACHE_DIR:/gomodcache" \
        -v "$RUNTIME_DIR/plugins:/out" \
        golang:1.24-bookworm sh -ceu '
            mkdir -p /workspace
            cp -a /source/. /workspace/
            cd /workspace
            go build -mod=readonly -trimpath -ldflags="-s -w" -o /out/argus-docker-gate .
        ' >/dev/null
    chmod 0755 "$DOCKER_GATE_BIN"
fi

DOCKER_GID="$(stat -c '%g' "$DOCKER_SOCKET" 2>/dev/null || \
              stat -f '%g' "$DOCKER_SOCKET" 2>/dev/null || echo "")"
[[ -n "$DOCKER_GID" ]] || fail "cannot determine GID of $DOCKER_SOCKET"

SOCKET_DIR="$(dirname "$PROXY_SOCKET")"
install -d -m 0755 "$SOCKET_DIR"

if [[ -f "$PROXY_PID_FILE" ]] && kill -0 "$(cat "$PROXY_PID_FILE")" 2>/dev/null; then
    echo "argus-docker-gate already running (pid $(cat "$PROXY_PID_FILE"))"
else
    rm -f "$PROXY_SOCKET" "$PROXY_PID_FILE"
    nohup "$DOCKER_GATE_BIN" \
        -listen "unix://$PROXY_SOCKET" \
        -upstream "unix://$DOCKER_SOCKET" \
        -allowed-image "$ALLOWED_IMAGE" \
        -socket-gid "$DOCKER_GID" \
        -log "$PROXY_LOG" \
        >/var/log/argus-docker-gate.stdout.log 2>&1 &
    echo $! > "$PROXY_PID_FILE"
    for _ in $(seq 1 30); do
        [[ -S "$PROXY_SOCKET" ]] && break
        sleep 1
    done
    [[ -S "$PROXY_SOCKET" ]] \
        || fail "proxy socket not created at $PROXY_SOCKET"
fi

echo "argus-docker-gate ready: $PROXY_SOCKET -> $DOCKER_SOCKET (socket-gid $DOCKER_GID, allowed-image $ALLOWED_IMAGE)"
echo "audit log: $PROXY_LOG"
