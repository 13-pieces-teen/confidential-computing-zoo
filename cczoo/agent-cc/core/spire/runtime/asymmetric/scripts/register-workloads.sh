#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROFILE_DIR/compose.yaml"
RUNTIME_DIR="${V2_RUNTIME_DIR:-$PROFILE_DIR/runtime}"
export V2_RUNTIME_DIR="$RUNTIME_DIR"
SERVER_SOCKET="/opt/spire/run/server/api.sock"
OPENCLAW_ID="spiffe://argus.local/agent/openclaw"
OPENVIKING_ID="spiffe://argus.local/service/openviking-cmem"
TDVM_SSH_TARGET="${TDVM_SSH_TARGET:-tdx@127.0.0.1}"
TDVM_SSH_PORT="${TDVM_SSH_PORT:-2222}"
TDVM_SSH_IDENTITY="${TDVM_SSH_IDENTITY:-}"
TDVM_KNOWN_HOSTS="${TDVM_KNOWN_HOSTS:-/tmp/argus-openviking-v2-known-hosts}"
OPENCLAW_WORKLOAD_IMAGE="${V2_OPENCLAW_WORKLOAD_IMAGE:-openclaw-sbx:latest}"
OPENVIKING_WORKLOAD_IMAGE="${V2_OPENVIKING_WORKLOAD_IMAGE:-localhost:5000/openviking:v0.4.8}"

ssh_options=(
    -o BatchMode=yes
    -o ConnectTimeout=10
    -o StrictHostKeyChecking=accept-new
    -o "UserKnownHostsFile=$TDVM_KNOWN_HOSTS"
    -p "$TDVM_SSH_PORT"
)
if [[ -n "$TDVM_SSH_IDENTITY" ]]; then
    ssh_options+=(-i "$TDVM_SSH_IDENTITY")
fi

fail() {
    printf 'v2 registration: FAIL: %s\n' "$1" >&2
    exit 1
}

[[ "$RUNTIME_DIR" == /* ]] \
    || fail "V2_RUNTIME_DIR must be an absolute host path: $RUNTIME_DIR"

spire_server() {
    docker compose -f "$COMPOSE_FILE" exec -T spire-server \
        /opt/spire/bin/spire-server "$@" -socketPath "$SERVER_SOCKET"
}

resolve_agent_parent() {
    local configured_id="$1"
    local attestor_path="$2"

    spire_server agent list -output json | python3 -c '
import json
import sys
import time
from urllib.parse import urlparse

prefix = sys.argv[1]
configured = sys.argv[2]
matches = []
for agent in json.load(sys.stdin).get("agents", []):
    if int(agent.get("x509svid_expires_at", 0)) <= time.time():
        continue
    identity = agent.get("id")
    if isinstance(identity, dict):
        value = "spiffe://{}{}".format(identity["trust_domain"], identity["path"])
        path = identity["path"]
    else:
        value = str(identity)
        path = urlparse(value).path
    if path.startswith(prefix):
        matches.append(value)
if configured:
    if configured not in matches:
        raise SystemExit(
            "configured parent {} is not a live Agent under {}".format(
                configured, prefix
            )
        )
    print(configured)
    raise SystemExit(0)
if len(matches) != 1:
    raise SystemExit(
        "expected exactly one non-expired Agent under {}; found {}".format(
            prefix, len(matches)
        )
    )
print(matches[0])
' "$attestor_path" "$configured_id"
}

require_digest() {
    [[ "$2" =~ ^sha256:[0-9a-f]{64}$ ]] \
        || fail "$1 is not an immutable Docker image config digest: $2"
}

openclaw_parent="$(
    resolve_agent_parent \
        "${OPENCLAW_PARENT_ID:-}" \
        /spire/agent/x509pop/
)"
openviking_parent="$(
    resolve_agent_parent \
        "${OPENVIKING_PARENT_ID:-}" \
        /spire/agent/argus_tdx/
)"
[[ "$openclaw_parent" != "$openviking_parent" ]] \
    || fail 'OpenClaw and OpenViking resolved to the same Agent parent'

openclaw_digest="${OPENCLAW_IMAGE_CONFIG_DIGEST:-$(
    docker image inspect "$OPENCLAW_WORKLOAD_IMAGE" --format '{{.Id}}'
)}"
openviking_digest="${OPENVIKING_IMAGE_CONFIG_DIGEST:-$(
    ssh "${ssh_options[@]}" "$TDVM_SSH_TARGET" \
        sudo -n /usr/local/bin/docker image inspect "$OPENVIKING_WORKLOAD_IMAGE" \
        --format '{{.Id}}'
)}"
require_digest OPENCLAW_IMAGE_CONFIG_DIGEST "$openclaw_digest"
require_digest OPENVIKING_IMAGE_CONFIG_DIGEST "$openviking_digest"

spire_server entry delete -entryID v2-openclaw-workload >/dev/null 2>&1 || true
spire_server entry create \
    -entryID v2-openclaw-workload \
    -parentID "$openclaw_parent" \
    -spiffeID "$OPENCLAW_ID" \
    -selector docker:label:argus.workload:openclaw \
    -selector docker:image_id:"$openclaw_digest" \
    -selector docker:image_config_digest:"$openclaw_digest" \
    -x509SVIDTTL 600 >/dev/null

spire_server entry delete -entryID v2-openviking-workload >/dev/null 2>&1 || true
spire_server entry create \
    -entryID v2-openviking-workload \
    -parentID "$openviking_parent" \
    -spiffeID "$OPENVIKING_ID" \
    -selector docker:label:argus.workload:openviking-cmem \
    -selector docker:image_id:"$openviking_digest" \
    -selector docker:image_config_digest:"$openviking_digest" \
    -x509SVIDTTL 600 >/dev/null

printf '%s\n' \
    "OpenClaw Agent parent: $openclaw_parent" \
    "OpenViking Agent parent: $openviking_parent" \
    "OpenClaw image config digest: $openclaw_digest" \
    "OpenViking image config digest: $openviking_digest"
spire_server entry show -entryID v2-openclaw-workload
spire_server entry show -entryID v2-openviking-workload
