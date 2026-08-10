#!/usr/bin/env bash
# Build and start argus-docker-gate, the minimal Docker socket proxy that
# isolates the OpenClaw gateway's Docker control plane (WP2).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SPIRE_ROOT="$(cd "$PROFILE_DIR/../.." && pwd)"
RUNTIME_DIR="${V2_RUNTIME_DIR:-$PROFILE_DIR/runtime}"
GO_CACHE_DIR="${ARGUS_GO_CACHE_DIR:-/tmp/argus-spire-v2-go-cache}"
DOCKER_GATE_BIN="$RUNTIME_DIR/plugins/argus-docker-gate"
PROXY_SOCKET="${V2_DOCKER_GATE_SOCKET:-/var/run/argus/docker-proxy.sock}"
PROXY_PID_FILE="${V2_DOCKER_GATE_PID_FILE:-/var/run/argus/argus-docker-gate.pid}"
PROXY_LOG="${V2_DOCKER_GATE_LOG:-/var/log/argus-docker-gate.log}"
ALLOWED_IMAGE="${V2_SANDBOX_IMAGE:-openclaw-sandbox:bookworm-slim}"
DOCKER_SOCKET="${DOCKER_SOCKET:-/var/run/docker.sock}"
OWNER_LABEL_KEY="${V2_DOCKER_GATE_OWNER_LABEL_KEY:-argus.openclaw.sandbox.owner}"
OWNER_ID="${V2_DOCKER_GATE_OWNER_ID:-}"

fail() {
    printf 'docker-gate: FAIL: %s\n' "$1" >&2
    exit 1
}

[[ "$RUNTIME_DIR" == /* ]] \
    || fail "V2_RUNTIME_DIR must be an absolute host path: $RUNTIME_DIR"
[[ -S "$DOCKER_SOCKET" ]] || fail "Docker daemon socket not found at $DOCKER_SOCKET"
command -v docker >/dev/null 2>&1 || fail "docker not found"
command -v stat >/dev/null 2>&1 || fail "stat not found"
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum not found"

if [[ -z "$OWNER_ID" ]]; then
    OWNER_ID="$(printf '%s' "$RUNTIME_DIR" | sha256sum | awk '{print substr($1, 1, 24)}')"
fi
[[ "$OWNER_ID" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$ ]] \
    || fail "V2_DOCKER_GATE_OWNER_ID must be a safe 1-64 character identifier"

# Always rebuild before restart so a source update cannot silently reuse the
# previous gate binary. Build to a sibling path and publish atomically.
echo "Building argus-docker-gate..."
install -d -m 0755 "$GO_CACHE_DIR" "$RUNTIME_DIR/plugins"
docker run --rm \
    -e "HTTPS_PROXY=${HTTPS_PROXY:-}" \
    -e "HTTP_PROXY=${HTTP_PROXY:-}" \
    -e "NO_PROXY=${NO_PROXY:-}" \
    -e GOMODCACHE=/gomodcache \
    -e "GOPROXY=${ARGUS_GO_PROXY:-https://proxy.golang.org,direct}" \
    -v "$SPIRE_ROOT/components/docker-gate:/source:ro" \
    -v "$GO_CACHE_DIR:/gomodcache" \
    -v "$RUNTIME_DIR/plugins:/out" \
    golang:1.24-bookworm sh -ceu '
        mkdir -p /workspace
        cp -a /source/. /workspace/
        cd /workspace
        go build -mod=readonly -trimpath -ldflags="-s -w" -o /out/argus-docker-gate.new .
    ' >/dev/null
chmod 0755 "$DOCKER_GATE_BIN.new"

DOCKER_GID="$(stat -c '%g' "$DOCKER_SOCKET" 2>/dev/null || \
              stat -f '%g' "$DOCKER_SOCKET" 2>/dev/null || echo "")"
[[ -n "$DOCKER_GID" ]] || fail "cannot determine GID of $DOCKER_SOCKET"

SOCKET_DIR="$(dirname "$PROXY_SOCKET")"
install -d -m 0755 "$SOCKET_DIR"

if [[ -f "$PROXY_PID_FILE" ]]; then
    old_pid="$(cat "$PROXY_PID_FILE")"
    if [[ "$old_pid" =~ ^[1-9][0-9]*$ ]] && kill -0 "$old_pid" 2>/dev/null; then
        old_exe="$(readlink -f "/proc/$old_pid/exe" 2>/dev/null || true)"
        [[ "$old_exe" == "$DOCKER_GATE_BIN" ]] \
            || fail "PID file points to an unexpected process: pid=$old_pid exe=$old_exe"
        kill "$old_pid"
        for _ in $(seq 1 30); do
            kill -0 "$old_pid" 2>/dev/null || break
            sleep 1
        done
        kill -0 "$old_pid" 2>/dev/null \
            && fail "previous argus-docker-gate did not stop: pid=$old_pid"
    fi
fi
mv -f "$DOCKER_GATE_BIN.new" "$DOCKER_GATE_BIN"
rm -f "$PROXY_SOCKET" "$PROXY_PID_FILE"
nohup "$DOCKER_GATE_BIN" \
    -listen "unix://$PROXY_SOCKET" \
    -upstream "unix://$DOCKER_SOCKET" \
    -allowed-image "$ALLOWED_IMAGE" \
    -owner-label-key "$OWNER_LABEL_KEY" \
    -owner-label-value "$OWNER_ID" \
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

echo "argus-docker-gate ready: $PROXY_SOCKET -> $DOCKER_SOCKET (socket-gid $DOCKER_GID, allowed-image $ALLOWED_IMAGE, owner-label $OWNER_LABEL_KEY=$OWNER_ID)"
echo "audit log: $PROXY_LOG"
