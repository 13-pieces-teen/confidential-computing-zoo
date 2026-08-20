#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SPIRE_ROOT="$(cd "$PROFILE_DIR/../.." && pwd)"
AGENT_CC_DIR="$(cd "$SPIRE_ROOT/../.." && pwd)"
CONNECT_SCRIPT="$AGENT_CC_DIR/adapters/OpenClaw/scripts/connect_openclaw_openviking.sh"
OPENCLAW_CONTAINER="${V2_REAL_OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
OPENCLAW_USER="${V2_REAL_OPENCLAW_USER:-node}"
OPENCLAW_CONFIG="${V2_REAL_OPENCLAW_CONFIG:-/home/node/.openclaw/openclaw.json}"
TARGET_URI="${V2_OPENVIKING_ORIGIN:-https://openviking.argus.local:1943}"

fail() {
    printf 'OpenClaw asymmetric plugin connection: FAIL: %s\n' "$1" >&2
    exit 1
}

[[ -f "$CONNECT_SCRIPT" ]] || fail "missing adapter: $CONNECT_SCRIPT"
[[ -n "${OPENVIKING_API_KEY:-}" ]] \
    || fail 'OPENVIKING_API_KEY must contain a non-root OpenViking user key'
docker inspect "$OPENCLAW_CONTAINER" >/dev/null 2>&1 \
    || fail "OpenClaw container does not exist: $OPENCLAW_CONTAINER"
[[ "$(docker inspect "$OPENCLAW_CONTAINER" --format '{{.State.Running}}')" == true ]] \
    || fail 'OpenClaw container is not running'
container_environment="$(docker inspect "$OPENCLAW_CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}')"
[[ "$container_environment" == *$'ARGUS_SPIFFE_ENABLED=1\n'* || "$container_environment" == ARGUS_SPIFFE_ENABLED=1* ]] \
    || fail 'OpenClaw container is not running the SPIFFE transport profile'

TARGET_URI="$TARGET_URI" \
OPENCLAW_CONTAINER="$OPENCLAW_CONTAINER" \
OPENCLAW_USER="$OPENCLAW_USER" \
OPENCLAW_CONFIG_PATH="$OPENCLAW_CONFIG" \
    bash "$CONNECT_SCRIPT"

docker exec -i -u "$OPENCLAW_USER" "$OPENCLAW_CONTAINER" \
    node - "$OPENCLAW_CONFIG" "$TARGET_URI" <<'NODE'
const { execFileSync } = require("node:child_process");
const configPath = process.argv[2];
const target = process.argv[3].replace(/\/+$/, "");
function get(path) {
  return JSON.parse(execFileSync("openclaw", ["config", "get", path, "--json"], {
    encoding: "utf8",
    env: { ...process.env, OPENCLAW_CONFIG_PATH: configPath },
  }).trim());
}
const slot = get("plugins.slots.contextEngine");
const plugin = get("plugins.entries.openviking.config");
if (slot !== "openviking") throw new Error(`unexpected context engine ${slot}`);
if (plugin?.mode !== "remote" || plugin?.baseUrl?.replace(/\/+$/, "") !== target) {
  throw new Error(`OpenViking plugin does not target ${target}`);
}
if (typeof plugin.apiKey !== "string" || !plugin.apiKey) throw new Error("plugin API key is missing");
console.log(`OpenViking context engine uses Broker Sidecar SPIFFE origin ${target}`);
NODE

printf '%s\n' \
    'Real OpenClaw OpenViking plugin connection is configured.' \
    "OpenClaw container: $OPENCLAW_CONTAINER" \
    "Native SPIFFE mTLS origin: $TARGET_URI" \
    'API key: configured but not printed'
