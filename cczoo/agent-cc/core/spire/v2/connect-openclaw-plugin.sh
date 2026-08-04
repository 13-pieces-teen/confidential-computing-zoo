#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_CC_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
CONNECT_SCRIPT="$AGENT_CC_DIR/adapters/OpenClaw/scripts/connect_openclaw_openviking.sh"
OPENCLAW_CONTAINER="${V2_REAL_OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
OPENCLAW_USER="${V2_REAL_OPENCLAW_USER:-node}"
OPENCLAW_CONFIG="${V2_REAL_OPENCLAW_CONFIG:-/home/node/.openclaw/openclaw.json}"
MTLS_CONTAINER="${V2_OPENCLAW_MTLS_CONTAINER:-argus-v2-openclaw-mtls}"
EGRESS_NETWORK="${V2_OPENCLAW_EGRESS_NETWORK:-argus-openclaw-egress}"
PROXY_BIND="${V2_OPENCLAW_PROXY_BIND:-172.31.44.1}"
EGRESS_IP="${V2_OPENCLAW_EGRESS_IP:-172.31.44.2}"
PROXY_PORT="${V2_OPENCLAW_PROXY_PORT:-1934}"
TARGET_URI="http://$PROXY_BIND:$PROXY_PORT"

fail() {
    printf 'OpenClaw v2 plugin connection: FAIL: %s\n' "$1" >&2
    exit 1
}

[[ "$OPENCLAW_CONTAINER" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] \
    || fail "invalid real OpenClaw container name: $OPENCLAW_CONTAINER"
[[ "$MTLS_CONTAINER" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] \
    || fail "invalid mTLS client container name: $MTLS_CONTAINER"
[[ "$EGRESS_NETWORK" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] \
    || fail "invalid Docker egress network name: $EGRESS_NETWORK"
[[ -x "$CONNECT_SCRIPT" ]] || fail "missing executable adapter: $CONNECT_SCRIPT"
[[ -n "${OPENVIKING_API_KEY:-}" ]] \
    || fail 'OPENVIKING_API_KEY must contain a non-root OpenViking user key'
docker inspect "$OPENCLAW_CONTAINER" >/dev/null 2>&1 \
    || fail "real OpenClaw container does not exist: $OPENCLAW_CONTAINER"
docker inspect "$MTLS_CONTAINER" >/dev/null 2>&1 \
    || fail "mTLS client container does not exist: $MTLS_CONTAINER"
[[ "$(docker inspect "$OPENCLAW_CONTAINER" --format '{{.State.Running}}')" == true ]] \
    || fail "real OpenClaw container is not running: $OPENCLAW_CONTAINER"
[[ "$(docker inspect "$MTLS_CONTAINER" --format '{{.State.Running}}')" == true ]] \
    || fail "mTLS client container is not running: $MTLS_CONTAINER"

actual_egress_ip="$(
    docker inspect "$OPENCLAW_CONTAINER" | python3 -c '
import json
import sys

container = json.load(sys.stdin)[0]
network = container.get("NetworkSettings", {}).get("Networks", {}).get(sys.argv[1])
print("" if network is None else network.get("IPAddress", ""))
' "$EGRESS_NETWORK"
)"
[[ "$actual_egress_ip" == "$EGRESS_IP" ]] \
    || fail "$OPENCLAW_CONTAINER is not attached to $EGRESS_NETWORK as $EGRESS_IP"

TARGET_URI="$TARGET_URI" \
OPENCLAW_CONTAINER="$OPENCLAW_CONTAINER" \
OPENCLAW_USER="$OPENCLAW_USER" \
OPENCLAW_CONFIG_PATH="$OPENCLAW_CONFIG" \
    "$CONNECT_SCRIPT"

docker exec -i -u "$OPENCLAW_USER" "$OPENCLAW_CONTAINER" \
    node - "$OPENCLAW_CONFIG" "$TARGET_URI" <<'NODE'
const { execFileSync } = require("node:child_process");

const configPath = process.argv[2];
const target = process.argv[3].replace(/\/+$/, "");

function readOpenClawConfig(path) {
  const output = execFileSync(
    "openclaw",
    ["config", "get", path, "--json"],
    {
      encoding: "utf8",
      env: { ...process.env, OPENCLAW_CONFIG_PATH: configPath },
      stdio: ["ignore", "pipe", "inherit"],
    },
  ).trim();
  if (!output) {
    throw new Error(`openclaw config get ${path} returned empty output`);
  }
  return JSON.parse(output);
}

const slot = readOpenClawConfig("plugins.slots.contextEngine");
const plugin = readOpenClawConfig("plugins.entries.openviking.config");

if (slot !== "openviking") {
  throw new Error(`plugins.slots.contextEngine is ${JSON.stringify(slot)}, expected "openviking"`);
}
if (!plugin || typeof plugin !== "object" || Array.isArray(plugin) || plugin.mode !== "remote") {
  throw new Error("OpenViking plugin is not configured in remote mode");
}
if (typeof plugin.baseUrl !== "string" || plugin.baseUrl.replace(/\/+$/, "") !== target) {
  throw new Error(`OpenViking baseUrl is ${JSON.stringify(plugin?.baseUrl)}, expected ${target}`);
}
if (typeof plugin.apiKey !== "string" || !plugin.apiKey) {
  throw new Error("OpenViking plugin API key is missing");
}
console.log(`OpenViking context engine is routed through ${target}`);
NODE

printf '%s\n' \
    'Real OpenClaw OpenViking plugin connection is configured.' \
    "OpenClaw container: $OPENCLAW_CONTAINER" \
    "SPIFFE mTLS egress: $TARGET_URI" \
    'API key: configured but not printed'
