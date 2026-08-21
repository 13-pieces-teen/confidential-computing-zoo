#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROFILE_DIR/compose.yaml"
RUNTIME_DIR="${DUAL_RUNTIME_DIR:-$PROFILE_DIR/runtime}"
export DUAL_RUNTIME_DIR="$RUNTIME_DIR"
SERVER_SOCKET="/opt/spire/run/server/api.sock"
OPENCLAW_ID="spiffe://argus.local/agent/openclaw"
OPENCLAW_BROKER_ID="spiffe://argus.local/infra/openclaw-broker"
OPENVIKING_ID="spiffe://argus.local/service/openviking-cmem"
OPENVIKING_BROKER_ID="spiffe://argus.local/infra/openviking-broker"
OPENCLAW_IMAGE="${DUAL_OPENCLAW_WORKLOAD_IMAGE:-argus-dual-openclaw:local}"
OPENCLAW_BROKER_IMAGE="${DUAL_OPENCLAW_BROKER_IMAGE:-argus-openclaw-egress-sidecar:local}"
OPENVIKING_RUNTIME_IMAGE_ID="${DUAL_OPENVIKING_RUNTIME_IMAGE_ID:-openviking-cmem:latest}"
OPENVIKING_BROKER_IMAGE="${DUAL_OPENVIKING_BROKER_IMAGE:-argus-openviking-broker-sidecar:local}"
OPENCLAW_PARENT_ID="${DUAL_OPENCLAW_PARENT_ID:-}"
OPENVIKING_PARENT_ID="${DUAL_OPENVIKING_PARENT_ID:-}"

fail() {
    printf 'dual TDVM registration: FAIL: %s\n' "$1" >&2
    exit 1
}

[[ "$RUNTIME_DIR" == /* ]] \
    || fail "DUAL_RUNTIME_DIR must be an absolute host path: $RUNTIME_DIR"
[[ -n "$OPENCLAW_PARENT_ID" ]] \
    || fail 'DUAL_OPENCLAW_PARENT_ID is required'
[[ -n "$OPENVIKING_PARENT_ID" ]] \
    || fail 'DUAL_OPENVIKING_PARENT_ID is required'
[[ "$OPENCLAW_PARENT_ID" != "$OPENVIKING_PARENT_ID" ]] \
    || fail 'OpenClaw and OpenViking must use different SPIRE Agent parents'

spire_server() {
    docker compose -f "$COMPOSE_FILE" exec -T spire-server \
        /opt/spire/bin/spire-server "$@" -socketPath "$SERVER_SOCKET"
}

make_ssh_options() {
    local role="$1"
    local -n destination="$2"
    local port identity known_hosts
    if [[ "$role" == openclaw ]]; then
        port="${DUAL_OPENCLAW_TDVM_SSH_PORT:-22}"
        identity="${DUAL_OPENCLAW_TDVM_SSH_IDENTITY:-}"
        known_hosts="${DUAL_OPENCLAW_TDVM_KNOWN_HOSTS:-/tmp/argus-dual-openclaw-known-hosts}"
    else
        port="${DUAL_OPENVIKING_TDVM_SSH_PORT:-22}"
        identity="${DUAL_OPENVIKING_TDVM_SSH_IDENTITY:-}"
        known_hosts="${DUAL_OPENVIKING_TDVM_KNOWN_HOSTS:-/tmp/argus-dual-openviking-known-hosts}"
    fi
    destination=(
        -o BatchMode=yes
        -o ConnectTimeout=10
        -o StrictHostKeyChecking=accept-new
        -o "UserKnownHostsFile=$known_hosts"
        -p "$port"
    )
    # Use an if so the function returns 0 when no identity is configured
    # (a bare "[[ ]] &&" here exits 1 and trips set -e in callers).
    if [[ -n "$identity" ]]; then
        destination+=(-i "$identity")
    fi
}

remote_image_digest() {
    local role="$1"
    local target="$2"
    local image="$3"
    local -a options
    make_ssh_options "$role" options
    ssh "${options[@]}" "$target" \
        sudo -n /usr/local/bin/docker image inspect "$image" --format '{{.Id}}'
}

require_digest() {
    [[ "$2" =~ ^sha256:[0-9a-f]{64}$ ]] \
        || fail "$1 is not an immutable Docker image config digest: $2"
}

OPENCLAW_SSH_TARGET="${DUAL_OPENCLAW_TDVM_SSH_TARGET:-}"
OPENVIKING_SSH_TARGET="${DUAL_OPENVIKING_TDVM_SSH_TARGET:-}"
[[ -n "$OPENCLAW_SSH_TARGET" ]] \
    || fail 'DUAL_OPENCLAW_TDVM_SSH_TARGET is required'
[[ -n "$OPENVIKING_SSH_TARGET" ]] \
    || fail 'DUAL_OPENVIKING_TDVM_SSH_TARGET is required'

spire_server agent list -output json | python3 -c '
import json
import sys
import time
from urllib.parse import urlparse

expected = set(sys.argv[1:])
live_argus_agents = set()
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
    if path.startswith("/spire/agent/argus_tdx/"):
        live_argus_agents.add(value)
missing = expected - live_argus_agents
if missing:
    raise SystemExit("configured parent IDs are not live argus_tdx Agents: {}".format(", ".join(sorted(missing))))
' "$OPENCLAW_PARENT_ID" "$OPENVIKING_PARENT_ID"

openclaw_digest="${DUAL_OPENCLAW_IMAGE_CONFIG_DIGEST:-$(
    remote_image_digest openclaw "$OPENCLAW_SSH_TARGET" "$OPENCLAW_IMAGE"
)}"
openclaw_broker_digest="${DUAL_OPENCLAW_BROKER_IMAGE_CONFIG_DIGEST:-$(
    remote_image_digest openclaw "$OPENCLAW_SSH_TARGET" "$OPENCLAW_BROKER_IMAGE"
)}"
openviking_digest="$(
    remote_image_digest openviking "$OPENVIKING_SSH_TARGET" "$OPENVIKING_RUNTIME_IMAGE_ID"
)"
openviking_broker_digest="${DUAL_OPENVIKING_BROKER_IMAGE_CONFIG_DIGEST:-$(
    remote_image_digest openviking "$OPENVIKING_SSH_TARGET" "$OPENVIKING_BROKER_IMAGE"
)}"
require_digest DUAL_OPENCLAW_IMAGE_CONFIG_DIGEST "$openclaw_digest"
require_digest DUAL_OPENCLAW_BROKER_IMAGE_CONFIG_DIGEST "$openclaw_broker_digest"
require_digest DUAL_OPENVIKING_RUNTIME_IMAGE_ID "$openviking_digest"
require_digest DUAL_OPENVIKING_BROKER_IMAGE_CONFIG_DIGEST "$openviking_broker_digest"

spire_server entry delete -entryID dual-openclaw-workload >/dev/null 2>&1 || true
spire_server entry create \
    -entryID dual-openclaw-workload \
    -parentID "$OPENCLAW_PARENT_ID" \
    -spiffeID "$OPENCLAW_ID" \
    -selector docker:label:argus.workload:openclaw \
    -selector docker:image_id:"$OPENCLAW_IMAGE" \
    -selector docker:image_config_digest:"$openclaw_digest" \
    -disableX509SVIDPrefetch \
    -x509SVIDTTL 600 >/dev/null

spire_server entry delete -entryID dual-openclaw-broker >/dev/null 2>&1 || true
spire_server entry create \
    -entryID dual-openclaw-broker \
    -parentID "$OPENCLAW_PARENT_ID" \
    -spiffeID "$OPENCLAW_BROKER_ID" \
    -selector docker:label:argus.component:openclaw-broker \
    -selector docker:image_id:"$OPENCLAW_BROKER_IMAGE" \
    -selector docker:image_config_digest:"$openclaw_broker_digest" \
    -x509SVIDTTL 600 >/dev/null

spire_server entry delete -entryID dual-openviking-workload >/dev/null 2>&1 || true
spire_server entry delete -entryID dual-openviking-broker >/dev/null 2>&1 || true
spire_server entry create \
    -entryID dual-openviking-broker \
    -parentID "$OPENVIKING_PARENT_ID" \
    -spiffeID "$OPENVIKING_BROKER_ID" \
    -selector docker:label:argus.component:openviking-broker \
    -selector docker:image_id:"$OPENVIKING_BROKER_IMAGE" \
    -selector docker:image_config_digest:"$openviking_broker_digest" \
    -x509SVIDTTL 600 >/dev/null

spire_server entry delete -entryID dual-openviking-target >/dev/null 2>&1 || true
spire_server entry create \
    -entryID dual-openviking-target \
    -parentID "$OPENVIKING_PARENT_ID" \
    -spiffeID "$OPENVIKING_ID" \
    -selector docker:label:argus.workload:openviking-cmem \
    -selector docker:image_id:"$OPENVIKING_RUNTIME_IMAGE_ID" \
    -selector docker:image_config_digest:"$openviking_digest" \
    -selector argus_tdx_workload:verified:true \
    -selector argus_tdx_workload:workload_id:openviking-cmem \
    -selector argus_tdx_workload:policy:openviking-cmem-v1 \
    -disableX509SVIDPrefetch \
    -x509SVIDTTL 600 >/dev/null

printf '%s\n' \
    "OpenClaw Agent parent: $OPENCLAW_PARENT_ID" \
    "OpenViking Agent parent: $OPENVIKING_PARENT_ID" \
    "OpenClaw image config digest: $openclaw_digest" \
    "OpenClaw Egress Broker image config digest: $openclaw_broker_digest" \
    "OpenViking runtime image id: $OPENVIKING_RUNTIME_IMAGE_ID" \
    "OpenViking observed runtime image config digest: $openviking_digest" \
    "OpenViking Broker image config digest: $openviking_broker_digest"
spire_server entry show -entryID dual-openclaw-workload
spire_server entry show -entryID dual-openclaw-broker
spire_server entry show -entryID dual-openviking-broker
spire_server entry show -entryID dual-openviking-target
