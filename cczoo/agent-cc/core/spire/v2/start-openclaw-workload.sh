#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="${V2_RUNTIME_DIR:-$SCRIPT_DIR/runtime}"
OPENCLAW_CONTAINER="${V2_REAL_OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
EGRESS_NETWORK="${V2_OPENCLAW_EGRESS_NETWORK:-argus-openclaw-egress}"
EGRESS_SUBNET="${V2_OPENCLAW_EGRESS_SUBNET:-172.31.44.0/28}"
PROXY_BIND="${V2_OPENCLAW_PROXY_BIND:-172.31.44.1}"
EGRESS_IP="${V2_OPENCLAW_EGRESS_IP:-172.31.44.2}"
export V2_RUNTIME_DIR="$RUNTIME_DIR"

if [[ "$RUNTIME_DIR" != /* ]]; then
    printf 'V2_RUNTIME_DIR must be an absolute host path: %s\n' "$RUNTIME_DIR" >&2
    exit 1
fi

fail() {
    printf 'OpenClaw v2 workload: FAIL: %s\n' "$1" >&2
    exit 1
}

[[ "$OPENCLAW_CONTAINER" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] \
    || fail "invalid real OpenClaw container name: $OPENCLAW_CONTAINER"
[[ "$EGRESS_NETWORK" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] \
    || fail "invalid Docker egress network name: $EGRESS_NETWORK"
docker inspect "$OPENCLAW_CONTAINER" >/dev/null 2>&1 \
    || fail "real OpenClaw container does not exist: $OPENCLAW_CONTAINER"
[[ "$(docker inspect "$OPENCLAW_CONTAINER" --format '{{.State.Running}}')" == true ]] \
    || fail "real OpenClaw container is not running: $OPENCLAW_CONTAINER"

guard_health="$(
    curl -fsS --noproxy '127.0.0.1,localhost' --max-time 3 \
        "http://127.0.0.1:${V2_GUARD_PORT:-18007}/health"
)" || fail 'Argus Guard health endpoint is unavailable'
printf '%s' "$guard_health" | python3 -c '
import json
import sys

value = json.load(sys.stdin)
if value.get("status") != "OK" or value.get("mode") != "mock_allow":
    raise SystemExit("Argus Guard is not in explicit mock_allow mode")
if value.get("authorization_context_required") is not True:
    raise SystemExit("Argus Guard does not require authorization_context")
if value.get("authorization_context_version") != "argus-authorization-v2":
    raise SystemExit("Argus Guard authorization context version is not v2")
ttl = value.get("decision_ttl_seconds")
if not isinstance(ttl, int) or not 1 <= ttl <= 300:
    raise SystemExit("Argus Guard decision TTL is invalid")
' || fail 'Argus Guard configuration is not safe for the gated workload'

python3 - "$EGRESS_SUBNET" "$PROXY_BIND" "$EGRESS_IP" <<'PY'
import ipaddress
import sys

network = ipaddress.ip_network(sys.argv[1], strict=True)
gateway = ipaddress.ip_address(sys.argv[2])
workload = ipaddress.ip_address(sys.argv[3])
if gateway not in network:
    raise SystemExit(f"proxy bind/gateway {gateway} is outside {network}")
if workload not in network:
    raise SystemExit(f"OpenClaw egress IP {workload} is outside {network}")
if gateway == workload:
    raise SystemExit("proxy bind/gateway and OpenClaw egress IP must differ")
if gateway in {network.network_address, network.broadcast_address}:
    raise SystemExit(f"proxy bind/gateway {gateway} is not a usable address")
if workload in {network.network_address, network.broadcast_address}:
    raise SystemExit(f"OpenClaw egress IP {workload} is not a usable address")
PY

if docker network inspect "$EGRESS_NETWORK" >/dev/null 2>&1; then
    network_json="$(docker network inspect "$EGRESS_NETWORK")"
    NETWORK_JSON="$network_json" python3 - \
        "$EGRESS_NETWORK" "$EGRESS_SUBNET" "$PROXY_BIND" <<'PY'
import json
import os
import sys

payload = json.loads(os.environ["NETWORK_JSON"])
if len(payload) != 1:
    raise SystemExit(f"expected one Docker network named {sys.argv[1]}")
network = payload[0]
if network.get("Driver") != "bridge":
    raise SystemExit(f"{sys.argv[1]} exists but is not a bridge network")
configs = network.get("IPAM", {}).get("Config", [])
if not any(
    config.get("Subnet") == sys.argv[2]
    and config.get("Gateway") == sys.argv[3]
    for config in configs
):
    raise SystemExit(
        f"{sys.argv[1]} exists with unexpected subnet/gateway; "
        f"expected {sys.argv[2]} gateway {sys.argv[3]}"
    )
PY
    # WP2: the egress bridge must be --internal (no path out of the bridge).
    if [[ "$(docker network inspect "$EGRESS_NETWORK" --format '{{.Internal}}')" != "true" ]]; then
        printf 'Egress network %s is not --internal; rebuilding it.\n' \
            "$EGRESS_NETWORK" >&2
        current_egress_ip="$(
            docker inspect "$OPENCLAW_CONTAINER" | python3 -c '
import json
import sys

container = json.load(sys.stdin)[0]
network = container.get("NetworkSettings", {}).get("Networks", {}).get(sys.argv[1])
print("" if network is None else network.get("IPAddress", ""))
' "$EGRESS_NETWORK"
        )"
        if [[ -n "$current_egress_ip" ]]; then
            docker network disconnect "$EGRESS_NETWORK" "$OPENCLAW_CONTAINER"
        fi
        docker network rm "$EGRESS_NETWORK" >/dev/null
        docker network create \
            --internal \
            --driver bridge \
            --subnet "$EGRESS_SUBNET" \
            --gateway "$PROXY_BIND" \
            "$EGRESS_NETWORK" >/dev/null
    fi
else
    docker network create \
        --internal \
        --driver bridge \
        --subnet "$EGRESS_SUBNET" \
        --gateway "$PROXY_BIND" \
        "$EGRESS_NETWORK" >/dev/null
fi

# WP2: start the Docker control-plane proxy and repoint the gateway socket so
# the OpenClaw gateway no longer controls the raw Docker daemon socket.
"$SCRIPT_DIR/start-docker-gate.sh"
"$SCRIPT_DIR/repoint-openclaw-socket.sh"

current_egress_ip="$(
    docker inspect "$OPENCLAW_CONTAINER" | python3 -c '
import json
import sys

container = json.load(sys.stdin)[0]
network = container.get("NetworkSettings", {}).get("Networks", {}).get(sys.argv[1])
print("" if network is None else network.get("IPAddress", ""))
' "$EGRESS_NETWORK"
)"
if [[ -z "$current_egress_ip" ]]; then
    docker network connect \
        --ip "$EGRESS_IP" \
        "$EGRESS_NETWORK" \
        "$OPENCLAW_CONTAINER"
elif [[ "$current_egress_ip" != "$EGRESS_IP" ]]; then
    fail "$OPENCLAW_CONTAINER is already attached to $EGRESS_NETWORK as $current_egress_ip, expected $EGRESS_IP"
fi

export V2_OPENCLAW_PROXY_BIND="$PROXY_BIND"
export V2_OPENCLAW_EGRESS_IP="$EGRESS_IP"
V2_MTLS_RUNTIME_IMAGE="${V2_MTLS_RUNTIME_IMAGE:-$(
    docker image inspect argus-spire-v2-mtls:local --format '{{.Id}}'
)}"
export V2_MTLS_RUNTIME_IMAGE

docker compose -f "$SCRIPT_DIR/compose.center.yaml" \
    --profile workload \
    up -d --force-recreate --no-deps openclaw-mtls-client

for _ in $(seq 1 30); do
    if docker compose -f "$SCRIPT_DIR/compose.center.yaml" \
        --profile workload \
        exec -T openclaw-mtls-client \
        /spire-mtls identity \
        -socket=unix:///opt/spire/run/openclaw/agent.sock \
        -expected-id=spiffe://argus.local/agent/openclaw \
        >/dev/null 2>&1; then
        printf '%s\n' \
            'OpenClaw SPIFFE mTLS workload is ready.' \
            "Real OpenClaw container: $OPENCLAW_CONTAINER" \
            "Restricted egress network: $EGRESS_NETWORK ($EGRESS_IP -> $PROXY_BIND:${V2_OPENCLAW_PROXY_PORT:-1934})"
        exit 0
    fi
    read -r -t 1 _ || true
done

docker compose -f "$SCRIPT_DIR/compose.center.yaml" \
    --profile workload \
    logs --tail 80 openclaw-mtls-client >&2
printf 'OpenClaw workload did not receive its X.509-SVID.\n' >&2
exit 1
