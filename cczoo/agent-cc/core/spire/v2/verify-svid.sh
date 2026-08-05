#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="${V2_RUNTIME_DIR:-$SCRIPT_DIR/runtime}"
export V2_RUNTIME_DIR="$RUNTIME_DIR"
SERVER_SOCKET="/opt/spire/run/server/api.sock"
OPENCLAW_CONTAINER="${V2_OPENCLAW_MTLS_CONTAINER:-argus-v2-openclaw-mtls}"
OPENVIKING_CONTAINER="${V2_OPENVIKING_MTLS_CONTAINER:-argus-v2-openviking-mtls}"
OPENCLAW_ID="spiffe://argus.local/agent/openclaw"
OPENVIKING_ID="spiffe://argus.local/service/openviking-cmem"
TDVM_SSH_TARGET="${TDVM_SSH_TARGET:-tdx@127.0.0.1}"
TDVM_SSH_PORT="${TDVM_SSH_PORT:-2222}"
TDVM_SSH_IDENTITY="${TDVM_SSH_IDENTITY:-}"
TDVM_KNOWN_HOSTS="${TDVM_KNOWN_HOSTS:-/tmp/argus-openviking-v2-known-hosts}"
REMOTE_RUN="${V2_GUEST_RUN:-/run/argus-spire-v2/openviking}"

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
    printf 'v2 SVID verification: FAIL: %s\n' "$1" >&2
    exit 1
}

[[ "$RUNTIME_DIR" == /* ]] \
    || fail "V2_RUNTIME_DIR must be an absolute host path: $RUNTIME_DIR"
[[ "$REMOTE_RUN" == /run/argus-spire-v2/* \
    && "$REMOTE_RUN" != *'//'*
    && "$REMOTE_RUN" != *'/./'*
    && "$REMOTE_RUN" != *'/../'*
    && "$REMOTE_RUN" != */.
    && "$REMOTE_RUN" != */..
    && "$REMOTE_RUN" != */ ]] \
    || fail "V2_GUEST_RUN must be an unambiguous child of /run/argus-spire-v2: $REMOTE_RUN"

expected_openclaw_run="$RUNTIME_DIR/openclaw-agent-run"
if ! actual_openclaw_run="$(
    docker inspect "$OPENCLAW_CONTAINER" | python3 -c '
import json
import os
import sys

containers = json.load(sys.stdin)
if len(containers) != 1:
    raise SystemExit("expected one OpenClaw mTLS container")
for mount in containers[0].get("Mounts") or []:
    if mount.get("Destination") == "/opt/spire/run/openclaw":
        source = str(mount.get("Source") or "")
        if not source:
            raise SystemExit("OpenClaw Workload API mount has no source")
        print(os.path.normpath(source))
        break
else:
    raise SystemExit("OpenClaw mTLS container has no Workload API mount")
'
)"; then
    fail "cannot inspect the active OpenClaw Workload API mount in $OPENCLAW_CONTAINER"
fi
expected_openclaw_run="$(
    python3 -c 'import os, sys; print(os.path.normpath(sys.argv[1]))' \
        "$expected_openclaw_run"
)"
[[ "$actual_openclaw_run" == "$expected_openclaw_run" ]] \
    || fail "V2_RUNTIME_DIR does not match the active OpenClaw runtime: expected mount $expected_openclaw_run, actual $actual_openclaw_run"

if ! actual_openviking_run="$(
    ssh "${ssh_options[@]}" "$TDVM_SSH_TARGET" \
        sudo -n /usr/local/bin/docker inspect "$OPENVIKING_CONTAINER" \
        | python3 -c '
import json
import os
import sys

containers = json.load(sys.stdin)
if len(containers) != 1:
    raise SystemExit("expected one OpenViking mTLS container")
for mount in containers[0].get("Mounts") or []:
    if mount.get("Destination") == "/opt/spire/run/openviking":
        source = str(mount.get("Source") or "")
        if not source:
            raise SystemExit("OpenViking Workload API mount has no source")
        print(os.path.normpath(source))
        break
else:
    raise SystemExit("OpenViking mTLS container has no Workload API mount")
'
)"; then
    fail "cannot inspect the active OpenViking Workload API mount in $OPENVIKING_CONTAINER"
fi
expected_openviking_run="$(
    python3 -c 'import os, sys; print(os.path.normpath(sys.argv[1]))' "$REMOTE_RUN"
)"
[[ "$actual_openviking_run" == "$expected_openviking_run" ]] \
    || fail "V2_GUEST_RUN does not match the active OpenViking runtime: expected mount $expected_openviking_run, actual $actual_openviking_run"

printf '%s\n' \
    "OpenClaw runtime mount: $actual_openclaw_run" \
    "OpenViking runtime mount: $actual_openviking_run"

spire_server() {
    docker compose -f "$SCRIPT_DIR/compose.center.yaml" exec -T spire-server \
        /opt/spire/bin/spire-server "$@" -socketPath "$SERVER_SOCKET"
}

agent_summary="$(
    spire_server agent list -output json | python3 -c '
import json
import sys
import time
from urllib.parse import urlparse

groups = {"x509pop": [], "argus_tdx": [], "join_token": []}
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
    for attestor in groups:
        if path.startswith("/spire/agent/{}/".format(attestor)):
            groups[attestor].append(value)
if len(groups["x509pop"]) != 1:
    raise SystemExit("expected one non-expired x509pop Agent")
if len(groups["argus_tdx"]) != 1:
    raise SystemExit("expected one non-expired argus_tdx Agent")
if groups["join_token"]:
    raise SystemExit("join_token Agent is still active in the formal v2 runtime")
if groups["x509pop"][0] == groups["argus_tdx"][0]:
    raise SystemExit("the two Agent IDs are not independent")
print("OpenClaw Agent (x509pop): {}".format(groups["x509pop"][0]))
print("OpenViking Agent (argus_tdx): {}".format(groups["argus_tdx"][0]))
'
)"
printf '%s\n' "$agent_summary"

openclaw_identity="$(
    docker exec "$OPENCLAW_CONTAINER" /spire-mtls identity \
        -socket=unix:///opt/spire/run/openclaw/agent.sock \
        -expected-id="$OPENCLAW_ID"
)"
openviking_identity="$(
    ssh "${ssh_options[@]}" "$TDVM_SSH_TARGET" \
        sudo -n /usr/local/bin/docker exec "$OPENVIKING_CONTAINER" \
        /spire-mtls identity \
        -socket=unix:///opt/spire/run/openviking/agent.sock \
        -expected-id="$OPENVIKING_ID"
)"
[[ "$openclaw_identity" == "$OPENCLAW_ID" ]] \
    || fail "unexpected OpenClaw identity: $openclaw_identity"
[[ "$openviking_identity" == "$OPENVIKING_ID" ]] \
    || fail "unexpected OpenViking identity: $openviking_identity"

openclaw_digest="$(docker inspect "$OPENCLAW_CONTAINER" --format '{{.Image}}')"
if ! openclaw_denial="$(
    docker run --rm \
        --network none \
        --label argus.workload=openviking-cmem \
        -v "$RUNTIME_DIR/openclaw-agent-run:/opt/spire/run/openclaw:ro" \
        "$openclaw_digest" \
        identity \
        -socket=unix:///opt/spire/run/openclaw/agent.sock \
        -expect-no-identity \
        -timeout=3s 2>&1
)"; then
    printf 'v2 SVID verification: FAIL: OpenClaw cross-role check failed:\n%s\n' \
        "$openclaw_denial" >&2
    exit 1
fi

openviking_digest="$(
    ssh "${ssh_options[@]}" "$TDVM_SSH_TARGET" \
        sudo -n /usr/local/bin/docker inspect "$OPENVIKING_CONTAINER" \
        --format '{{.Image}}'
)"
if ! openviking_denial="$(
    ssh "${ssh_options[@]}" "$TDVM_SSH_TARGET" \
        sudo -n /usr/local/bin/docker run --rm \
        --network none \
        --label argus.workload=openclaw \
        -v "$REMOTE_RUN:/opt/spire/run/openviking:ro" \
        "$openviking_digest" \
        identity \
        -socket=unix:///opt/spire/run/openviking/agent.sock \
        -expect-no-identity \
        -timeout=3s 2>&1
)"; then
    printf 'v2 SVID verification: FAIL: OpenViking cross-role check failed:\n%s\n' \
        "$openviking_denial" >&2
    exit 1
fi

printf '%s\n' \
    "OpenClaw workload SVID: $openclaw_identity" \
    "OpenViking workload SVID: $openviking_identity" \
    'Cross-role label checks: denied on both independent Workload APIs'
