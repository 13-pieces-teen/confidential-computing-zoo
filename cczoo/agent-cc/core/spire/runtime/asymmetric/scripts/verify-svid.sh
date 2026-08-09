#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROFILE_DIR/compose.yaml"
RUNTIME_DIR="${V2_RUNTIME_DIR:-$PROFILE_DIR/runtime}"
OPENCLAW_CONTAINER="${V2_REAL_OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
OPENVIKING_CONTAINER="${V2_REAL_OPENVIKING_CONTAINER:-agentcc-openviking-service}"
REMOTE_RUN="${V2_GUEST_RUN:-/run/argus-spire-v2/openviking}"
TDVM_SSH_TARGET="${TDVM_SSH_TARGET:-tdx@127.0.0.1}"
TDVM_SSH_PORT="${TDVM_SSH_PORT:-2222}"
TDVM_SSH_IDENTITY="${TDVM_SSH_IDENTITY:-}"
TDVM_KNOWN_HOSTS="${TDVM_KNOWN_HOSTS:-/tmp/argus-openviking-v2-known-hosts}"
SERVER_SOCKET=/opt/spire/run/server/api.sock

ssh_options=(-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -o "UserKnownHostsFile=$TDVM_KNOWN_HOSTS" -p "$TDVM_SSH_PORT")
[[ -n "$TDVM_SSH_IDENTITY" ]] && ssh_options+=(-i "$TDVM_SSH_IDENTITY")

fail() { printf 'asymmetric SVID verification: FAIL: %s\n' "$1" >&2; exit 1; }
[[ "$RUNTIME_DIR" == /* ]] || fail "V2_RUNTIME_DIR must be absolute: $RUNTIME_DIR"

validate_status() {
    local expected_id="$1"
    python3 -c '
import json, sys, time
status = json.load(sys.stdin)
if status.get("spiffe_id") != sys.argv[1]:
    raise SystemExit("unexpected SPIFFE ID: {}".format(status.get("spiffe_id")))
if int(status.get("not_after_unix", 0)) <= time.time():
    raise SystemExit("X509-SVID is expired")
if int(status.get("not_before_unix", 0)) > time.time() + 60:
    raise SystemExit("X509-SVID is not valid yet")
' "$expected_id"
}

docker inspect "$OPENCLAW_CONTAINER" >/dev/null 2>&1 || fail 'OpenClaw workload is missing'
openclaw_mount="$(docker inspect "$OPENCLAW_CONTAINER" --format '{{range .Mounts}}{{if eq .Destination "/run/spire/agent"}}{{.Source}}{{end}}{{end}}')"
[[ "$openclaw_mount" == "$RUNTIME_DIR/openclaw-agent-run" ]] \
    || fail "OpenClaw Workload API mount is $openclaw_mount"
docker exec "$OPENCLAW_CONTAINER" cat /run/argus-svid/status.json \
    | validate_status spiffe://argus.local/agent/openclaw

ssh "${ssh_options[@]}" "$TDVM_SSH_TARGET" sudo -n /usr/local/bin/docker inspect "$OPENVIKING_CONTAINER" >/dev/null \
    || fail 'OpenViking workload is missing in the TD Guest'
openviking_mount="$(ssh "${ssh_options[@]}" "$TDVM_SSH_TARGET" sudo -n /usr/local/bin/docker inspect "$OPENVIKING_CONTAINER" --format '{{range .Mounts}}{{if eq .Destination "/opt/spire/run/openviking"}}{{.Source}}{{end}}{{end}}')"
[[ "$openviking_mount" == "$REMOTE_RUN" ]] || fail "OpenViking Workload API mount is $openviking_mount"
ssh "${ssh_options[@]}" "$TDVM_SSH_TARGET" sudo -n /usr/local/bin/docker exec "$OPENVIKING_CONTAINER" cat /run/argus-svid/status.json \
    | validate_status spiffe://argus.local/service/openviking-cmem

agents="$(docker compose -f "$COMPOSE_FILE" exec -T spire-server /opt/spire/bin/spire-server agent list -output json -socketPath "$SERVER_SOCKET")"
printf '%s' "$agents" | python3 -c '
import json, sys, time
agents = json.load(sys.stdin).get("agents", [])
paths = []
for agent in agents:
    if int(agent.get("x509svid_expires_at", 0)) <= time.time():
        continue
    identity = agent.get("id")
    if isinstance(identity, dict):
        paths.append(identity.get("path", ""))
    else:
        paths.append(str(identity).split("argus.local", 1)[-1])
if len([p for p in paths if p.startswith("/spire/agent/x509pop/")]) != 1:
    raise SystemExit("expected exactly one live x509pop OpenClaw Agent")
if len([p for p in paths if p.startswith("/spire/agent/argus_tdx/")]) != 1:
    raise SystemExit("expected exactly one live argus_tdx OpenViking Agent")
'

printf '%s\n' \
    'Asymmetric SVID verification passed.' \
    'OpenClaw: x509pop Agent + spiffe://argus.local/agent/openclaw.' \
    'OpenViking: argus_tdx-attested Agent + spiffe://argus.local/service/openviking-cmem.'
