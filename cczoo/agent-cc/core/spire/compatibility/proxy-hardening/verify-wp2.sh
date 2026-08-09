#!/usr/bin/env bash
# WP2 data-plane bypass tightening verification.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENCLAW_CONTAINER="${V2_REAL_OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
OPENCLAW_USER="${V2_REAL_OPENCLAW_USER:-node}"
EGRESS_NETWORK="${V2_OPENCLAW_EGRESS_NETWORK:-argus-openclaw-egress}"
EGRESS_IP="${V2_OPENCLAW_EGRESS_IP:-172.31.44.2}"
PROXY_BIND="${V2_OPENCLAW_PROXY_BIND:-172.31.44.1}"
PROXY_PORT="${V2_OPENCLAW_PROXY_PORT:-1934}"
PROXY_SOCKET="${V2_DOCKER_GATE_SOCKET:-/var/run/argus/docker-proxy.sock}"
MTLS_CONTAINER="${V2_OPENCLAW_MTLS_CONTAINER:-argus-v2-openclaw-mtls}"
SERVER_CONTAINER="${V2_OPENCLAW_SERVER_CONTAINER:-argus-v2-spire-server}"
GUARD_CONTAINER="${V2_GUARD_CONTAINER:-argus-v2-guard}"
SIBLING_IMAGE="${V2_WP2_SIBLING_IMAGE:-openclaw-sandbox:bookworm-slim}"
RUNTIME_DIR="${V2_RUNTIME_DIR:-$SCRIPT_DIR/runtime}"
OWNER_LABEL_KEY="${V2_DOCKER_GATE_OWNER_LABEL_KEY:-argus.openclaw.sandbox.owner}"
OWNER_ID="${V2_DOCKER_GATE_OWNER_ID:-}"

if [[ -z "$OWNER_ID" ]]; then
    OWNER_ID="$(printf '%s' "$RUNTIME_DIR" | sha256sum | awk '{print substr($1, 1, 24)}')"
fi

fail() {
    printf 'WP2 verification: FAIL: %s\n' "$1" >&2
    exit 1
}

echo "[1/7] Gateway Docker socket is the argus-docker-gate proxy"
actual_socket="$(
    docker inspect "$OPENCLAW_CONTAINER" \
        --format '{{range .Mounts}}{{if eq .Destination "/var/run/docker.sock"}}{{.Source}}{{end}}{{end}}'
)"
[[ "$actual_socket" == "$PROXY_SOCKET" ]] \
    || fail "gateway docker.sock mount is $actual_socket, expected proxy $PROXY_SOCKET"
docker exec -u "$OPENCLAW_USER" "$OPENCLAW_CONTAINER" docker ps >/dev/null 2>&1 \
    || fail "docker ps through the proxy failed"
if docker exec -u "$OPENCLAW_USER" "$OPENCLAW_CONTAINER" \
    docker run --rm --privileged "$SIBLING_IMAGE" echo nope >/dev/null 2>&1; then
    fail "privileged container creation was NOT blocked by the proxy"
fi
echo "OK: proxy in place; docker ps works; privileged create denied"

echo "[2/7] Infrastructure and unmanaged containers are outside the gateway authority"
PROTECTED="argus-wp2-protected-$$"
MANAGED="argus-wp2-managed-$$"
docker run -d --name "$PROTECTED" "$SIBLING_IMAGE" sleep 300 >/dev/null
cleanup_control_plane_probes() {
    docker rm -f "$PROTECTED" "$MANAGED" >/dev/null 2>&1 || true
    docker exec -u "$OPENCLAW_USER" "$OPENCLAW_CONTAINER" \
        rm -f /tmp/argus-wp2-protected-copy >/dev/null 2>&1 || true
}
trap cleanup_control_plane_probes EXIT

for target in "$PROTECTED" "$SERVER_CONTAINER" "$GUARD_CONTAINER" "$MTLS_CONTAINER"; do
    if docker exec -u "$OPENCLAW_USER" "$OPENCLAW_CONTAINER" \
        docker exec "$target" true >/dev/null 2>&1; then
        fail "gateway obtained exec access to unmanaged container $target"
    fi
done
if docker exec -u "$OPENCLAW_USER" "$OPENCLAW_CONTAINER" \
    docker stop "$PROTECTED" >/dev/null 2>&1; then
    fail "gateway stopped an unmanaged container"
fi
for operation in start restart kill rm; do
    operation_args=("$operation")
    [[ "$operation" == "rm" ]] && operation_args+=("-f")
    if docker exec -u "$OPENCLAW_USER" "$OPENCLAW_CONTAINER" \
        docker "${operation_args[@]}" "$PROTECTED" >/dev/null 2>&1; then
        fail "gateway executed docker $operation against an unmanaged container"
    fi
done
if docker exec -u "$OPENCLAW_USER" "$OPENCLAW_CONTAINER" \
    docker cp "$PROTECTED:/etc/hostname" /tmp/argus-wp2-protected-copy >/dev/null 2>&1; then
    fail "gateway read an unmanaged container archive"
fi
[[ "$(docker inspect "$PROTECTED" --format '{{.State.Running}}')" == true ]] \
    || fail "unmanaged probe container was modified by denied operations"
echo "OK: exec/stop/archive access to unmanaged and infrastructure containers denied"

echo "[3/7] Gate-created sandbox receives the run-scoped owner label"
docker exec -u "$OPENCLAW_USER" "$OPENCLAW_CONTAINER" \
    docker run -d --name "$MANAGED" "$SIBLING_IMAGE" sleep 300 >/dev/null \
    || fail "gateway could not create a managed sandbox through the gate"
actual_owner="$(docker inspect "$MANAGED" --format "{{index .Config.Labels \"$OWNER_LABEL_KEY\"}}")"
[[ "$actual_owner" == "$OWNER_ID" ]] \
    || fail "managed sandbox owner label is $actual_owner, expected $OWNER_ID"
docker exec -u "$OPENCLAW_USER" "$OPENCLAW_CONTAINER" \
    docker exec "$MANAGED" true >/dev/null 2>&1 \
    || fail "gateway could not operate its managed sandbox"
docker exec -u "$OPENCLAW_USER" "$OPENCLAW_CONTAINER" \
    docker rm -f "$MANAGED" >/dev/null 2>&1 \
    || fail "gateway could not remove its managed sandbox"
echo "OK: managed sandbox label injected; managed exec/remove allowed"

echo "[4/7] Egress bridge is --internal and carries only the gateway"
[[ "$(docker network inspect "$EGRESS_NETWORK" --format '{{.Internal}}')" == "true" ]] \
    || fail "egress network is not --internal"
members="$(
    docker network inspect "$EGRESS_NETWORK" \
        --format '{{range $k,$v := .Containers}}{{$v.Name}}={{$v.IPv4Address}} {{end}}'
)"
echo "egress members: $members"
member_count="$(printf '%s\n' "$members" | tr ' ' '\n' | sed '/^$/d' | wc -l)"
[[ "$member_count" == "1" ]] \
    || fail "expected exactly one container on the egress network, got: $members"
printf '%s\n' "$members" | tr ' ' '\n' | sed '/^$/d' | grep -q "^$OPENCLAW_CONTAINER=$EGRESS_IP/28$" \
    || fail "gateway is not attached as $EGRESS_IP/28 on the egress network"

echo "[5/7] Sibling container on the egress bridge cannot borrow the egress identity"
SIBLING="argus-wp2-sibling-$$"
docker run -d --name "$SIBLING" "$SIBLING_IMAGE" sleep 300 >/dev/null
cleanup_sibling() {
    docker rm -f "$SIBLING" >/dev/null 2>&1 || true
}
trap 'cleanup_sibling; cleanup_control_plane_probes' EXIT
docker network connect "$EGRESS_NETWORK" "$SIBLING"
sibling_ip="$(docker inspect "$SIBLING" \
    --format '{{range $k,$v := .NetworkSettings.Networks}}{{$v.IPAddress}} {{end}}' \
    | tr ' ' '\n' | sed '/^$/d' | grep '^172.31.44.' || true)"
[[ -n "$sibling_ip" && "$sibling_ip" != "$EGRESS_IP" ]] \
    || fail "sibling did not receive a distinct egress-bridge IP"
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
sibling_response="$(
    docker exec "$SIBLING" bash -c '
        exec 3<>/dev/tcp/172.31.44.1/1934
        printf "GET /health HTTP/1.1\r\nHost: 172.31.44.1\r\nConnection: close\r\n\r\n" >&3
        cat <&3
    ' 2>&1
)"
printf '%s\n' "$sibling_response" | grep -q 'HTTP/1.1 403' \
    || fail "sibling did not receive 403 from the egress"
printf '%s\n' "$sibling_response" | grep -q 'OpenClaw egress source rejected' \
    || fail "sibling 403 body did not come from the egress proxy"
sibling_request_id="$(
    printf '%s\n' "$sibling_response" \
        | grep -i '^X-Argus-Request-ID:' \
        | head -1 \
        | awk '{print $2}' \
        | tr -d '\r'
)"
[[ "$sibling_request_id" =~ ^[0-9a-f]{24}$ ]] \
    || fail "sibling rejection did not return a generated request ID"
sibling_log=""
for _ in 1 2 3 4 5; do
    sibling_log="$(
        docker logs --since "$started_at" "$MTLS_CONTAINER" 2>&1 \
            | grep -F "request_id=$sibling_request_id " \
            | grep -F "source_ip=$sibling_ip " \
            | grep -F 'method=GET path=/health status=403 ' \
            | grep -F 'decision=source_rejected' \
            | tail -n 1 || true
    )"
    [[ -n "$sibling_log" ]] && break
    read -r -t 1 _ || true
done
[[ -n "$sibling_log" ]] || fail "egress did not log source_rejected for sibling request $sibling_request_id"
echo "OK: sibling($sibling_ip) -> 403 source_rejected, request_id=$sibling_request_id"

echo "[6/7] Gateway is not host-networked and not on the identity plane"
[[ "$(docker inspect "$OPENCLAW_CONTAINER" --format '{{.HostConfig.NetworkMode}}')" != "host" ]] \
    || fail "gateway uses host networking"
if docker inspect "$OPENCLAW_CONTAINER" \
    --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' \
    | grep -q 'argus-spire-v2-center_center'; then
    fail "gateway is attached to the SPIRE identity network"
fi
if docker exec -u "$OPENCLAW_USER" "$OPENCLAW_CONTAINER" bash -lc \
    'curl -sS --max-time 3 -o /dev/null http://127.0.0.1:2933/health' >/dev/null 2>&1; then
    fail "gateway unexpectedly reached the TDVM OpenViking loopback (127.0.0.1:2933)"
fi
echo "OK: gateway not host-networked, not on identity plane, cannot reach TDVM OpenViking loopback"

echo "[7/7] Positive Guard-gated egress still works"
positive_code="$(
    docker exec -u "$OPENCLAW_USER" "$OPENCLAW_CONTAINER" bash -lc \
        'curl -sS --max-time 10 -o /dev/null -w "%{http_code}" \
           -H "X-Argus-Request-ID: wp2-positive" http://172.31.44.1:1934/health'
)"
[[ "$positive_code" == "200" ]] || fail "positive egress returned HTTP $positive_code, expected 200"
positive_headers="$(
    docker exec -u "$OPENCLAW_USER" "$OPENCLAW_CONTAINER" bash -lc \
        'curl -sS --max-time 10 -D - -o /dev/null \
           -H "X-Argus-Request-ID: wp2-positive" http://172.31.44.1:1934/health'
)"
printf '%s\n' "$positive_headers" | grep -qi '^x-argus-decision-id:' \
    || fail "positive egress response has no Guard decision ID header"
printf '%s\n' "$positive_headers" | grep -qi '^x-argus-verification-mode: mock_allow' \
    || fail "positive egress response is not mock_allow"
echo "OK: positive Guard-gated egress returned 200 with mock_allow receipt"

printf '%s\n' \
    'WP2 verification passed.' \
    'Gateway Docker socket is the restricted proxy; privileged/unsafe creates are denied.' \
    'Only gate-labeled managed sandboxes accept lifecycle/exec/archive operations; infrastructure containers are denied.' \
    'Egress bridge is --internal with only the gateway attached; sibling containers get 403 source_rejected.' \
    'Gateway is not host-networked, not on the identity plane, and cannot reach the TDVM OpenViking loopback.' \
    'Positive Guard-gated egress still returns 200 with a mock_allow decision receipt.'
