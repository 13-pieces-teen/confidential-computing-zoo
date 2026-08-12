#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SPIRE_ROOT="$(cd "$PROFILE_DIR/../.." && pwd)"
AGENT_CC_DIR="$(cd "$SPIRE_ROOT/../.." && pwd)"
RUNTIME_DIR="${V2_RUNTIME_DIR:-$PROFILE_DIR/runtime}"
OPENCLAW_CONTAINER="${V2_REAL_OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
OPENCLAW_RUN_SCRIPT="$AGENT_CC_DIR/adapters/OpenClaw/scripts/run-sbx.sh"
CONTROL_NETWORK="${V2_OPENCLAW_CONTROL_NETWORK:-argus-spire-v2-center_center}"
OPENVIKING_ORIGIN="${V2_OPENVIKING_ORIGIN:-https://openviking.argus.local:1943}"
OPENVIKING_HOST_ADDRESS="${V2_OPENVIKING_HOST_ADDRESS:-host-gateway}"
GUARD_URL="${V2_GUARD_INTERNAL_URL:-http://guard:8007/guard/v1/authorize}"

fail() {
    printf 'OpenClaw asymmetric workload: FAIL: %s\n' "$1" >&2
    exit 1
}

[[ "$RUNTIME_DIR" == /* ]] || fail "V2_RUNTIME_DIR must be absolute: $RUNTIME_DIR"
[[ "$OPENCLAW_CONTAINER" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] \
    || fail "invalid OpenClaw container name: $OPENCLAW_CONTAINER"
[[ -f "$OPENCLAW_RUN_SCRIPT" ]] || fail "missing OpenClaw launcher: $OPENCLAW_RUN_SCRIPT"
[[ -f "$RUNTIME_DIR/secrets/openclaw-guard-api-token" ]] \
    || fail 'Guard API token is missing; run prepare.sh first'
[[ -S "$RUNTIME_DIR/openclaw-agent-run/agent.sock" ]] \
    || fail 'OpenClaw Workload API socket is missing; start the OpenClaw SPIRE Agent first'
docker network inspect "$CONTROL_NETWORK" >/dev/null 2>&1 \
    || fail "Guard control network is missing: $CONTROL_NETWORK"

run_arguments=(--name "$OPENCLAW_CONTAINER")
# The image digest was pinned into the workload registration entry. Rebuilding
# after registration can change that digest and must therefore be explicit.
[[ "${V2_REBUILD_OPENCLAW_IMAGE:-0}" == "1" ]] && run_arguments=(--build "${run_arguments[@]}")

ARGUS_SPIFFE_ENABLED=1 \
ARGUS_SPIFFE_WORKLOAD_API_DIR="$RUNTIME_DIR/openclaw-agent-run" \
ARGUS_SPIFFE_DOCKER_NETWORK="$CONTROL_NETWORK" \
ARGUS_OPENVIKING_ORIGIN="$OPENVIKING_ORIGIN" \
ARGUS_OPENVIKING_HOST_ADDRESS="$OPENVIKING_HOST_ADDRESS" \
ARGUS_GUARD_URL="$GUARD_URL" \
ARGUS_GUARD_API_TOKEN_FILE="$RUNTIME_DIR/secrets/openclaw-guard-api-token" \
ARGUS_CALLER_SPIFFE_ID=spiffe://argus.local/agent/openclaw \
ARGUS_TARGET_SPIFFE_ID=spiffe://argus.local/service/openviking-cmem \
ARGUS_TARGET_SERVICE=openviking-cmem \
ARGUS_GUARD_DATA_CLASS="${V2_GUARD_DATA_CLASS:-sensitive}" \
ARGUS_GUARD_TIMEOUT_MS="${V2_GUARD_TIMEOUT_MS:-2000}" \
ARGUS_SPIFFE_KEEPALIVE_TIMEOUT_MS="${V2_SPIFFE_KEEPALIVE_TIMEOUT_MS:-10000}" \
ARGUS_SPIFFE_KEEPALIVE_MAX_TIMEOUT_MS="${V2_SPIFFE_KEEPALIVE_MAX_TIMEOUT_MS:-30000}" \
ARGUS_MODEL_CA_BUNDLE="${V2_MODEL_CA_BUNDLE:-}" \
NODE_USE_ENV_PROXY="${NODE_USE_ENV_PROXY:-}" \
    bash "$OPENCLAW_RUN_SCRIPT" "${run_arguments[@]}"

for _ in $(seq 1 30); do
    if docker exec "$OPENCLAW_CONTAINER" \
        grep -q '"spiffe_id": "spiffe://argus.local/agent/openclaw"' \
        /run/argus-svid/status.json >/dev/null 2>&1; then
        break
    fi
    read -r -t 1 _ || true
done
docker exec "$OPENCLAW_CONTAINER" \
    grep -q '"spiffe_id": "spiffe://argus.local/agent/openclaw"' \
    /run/argus-svid/status.json \
    || { docker logs --tail 120 "$OPENCLAW_CONTAINER" >&2; fail 'OpenClaw did not materialize its X509-SVID'; }

docker exec "$OPENCLAW_CONTAINER" node - "$GUARD_URL" <<'NODE'
const url = process.argv[2].replace(/\/guard\/v1\/authorize$/, "/health");
const response = await fetch(url, { signal: AbortSignal.timeout(3000) });
if (!response.ok) throw new Error(`Guard health returned HTTP ${response.status}`);
const health = await response.json();
if (health.status !== "OK" || health.mode !== "spiffe_identity") {
  throw new Error(`unexpected Guard health: ${JSON.stringify(health)}`);
}
NODE

printf '%s\n' \
    'OpenClaw native Guard + SPIFFE HTTP workload is ready.' \
    "Container: $OPENCLAW_CONTAINER" \
    "Guard: $GUARD_URL" \
    "OpenViking origin: $OPENVIKING_ORIGIN" \
    'No standalone OpenClaw mTLS transport proxy is in the business path.'
