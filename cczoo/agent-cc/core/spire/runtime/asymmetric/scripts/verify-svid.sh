#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROFILE_DIR/compose.yaml"
RUNTIME_DIR="${V2_RUNTIME_DIR:-$PROFILE_DIR/runtime}"
OPENCLAW_CONTAINER="${V2_REAL_OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
OPENVIKING_CONTAINER="${V2_REAL_OPENVIKING_CONTAINER:-agentcc-openviking-service}"
BROKER_CONTAINER="${V2_OPENVIKING_BROKER_CONTAINER:-agentcc-openviking-broker-sidecar}"
REMOTE_RUN="${V2_GUEST_RUN:-/run/argus-spire-v2/openviking}"
REMOTE_BROKER_RUN="${V2_GUEST_BROKER_RUN:-/run/argus-spire-v2/openviking-broker}"
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
ssh "${ssh_options[@]}" "$TDVM_SSH_TARGET" sudo -n /usr/local/bin/docker inspect "$BROKER_CONTAINER" >/dev/null \
    || fail 'OpenViking Broker Sidecar is missing in the TD Guest'

openviking_running="$(ssh "${ssh_options[@]}" "$TDVM_SSH_TARGET" sudo -n /usr/local/bin/docker inspect "$OPENVIKING_CONTAINER" --format '{{.State.Running}}')"
broker_running="$(ssh "${ssh_options[@]}" "$TDVM_SSH_TARGET" sudo -n /usr/local/bin/docker inspect "$BROKER_CONTAINER" --format '{{.State.Running}}')"
[[ "$openviking_running" == true ]] || fail 'OpenViking workload is not running'
[[ "$broker_running" == true ]] || fail 'OpenViking Broker Sidecar is not running'

openviking_spire_mount="$(ssh "${ssh_options[@]}" "$TDVM_SSH_TARGET" "sudo -n /usr/local/bin/docker inspect $OPENVIKING_CONTAINER --format '{{range .Mounts}}{{if or (eq .Destination \"/opt/spire/run/agent\") (eq .Destination \"/opt/spire/run/broker\") (eq .Destination \"/opt/spire/run/openviking\")}}{{.Source}}{{end}}{{end}}'")"
[[ -z "$openviking_spire_mount" ]] \
    || fail 'OpenViking must not mount a SPIRE Workload API or Broker API socket'

broker_workload_mount="$(ssh "${ssh_options[@]}" "$TDVM_SSH_TARGET" "sudo -n /usr/local/bin/docker inspect $BROKER_CONTAINER --format '{{range .Mounts}}{{if eq .Destination \"/opt/spire/run/agent\"}}{{.Source}}{{end}}{{end}}'")"
broker_api_mount="$(ssh "${ssh_options[@]}" "$TDVM_SSH_TARGET" "sudo -n /usr/local/bin/docker inspect $BROKER_CONTAINER --format '{{range .Mounts}}{{if eq .Destination \"/opt/spire/run/broker\"}}{{.Source}}{{end}}{{end}}'")"
[[ "$broker_workload_mount" == "$REMOTE_RUN" ]] \
    || fail "Broker Workload API mount is $broker_workload_mount, expected $REMOTE_RUN"
[[ "$broker_api_mount" == "$REMOTE_BROKER_RUN" ]] \
    || fail "Broker API mount is $broker_api_mount, expected $REMOTE_BROKER_RUN"

target_pid="$(ssh "${ssh_options[@]}" "$TDVM_SSH_TARGET" sudo -n /usr/local/bin/docker inspect "$OPENVIKING_CONTAINER" --format '{{.State.Pid}}')"
broker_command="$(ssh "${ssh_options[@]}" "$TDVM_SSH_TARGET" sudo -n /usr/local/bin/docker inspect "$BROKER_CONTAINER" --format '{{json .Config.Cmd}}')"
[[ "$broker_command" == *"-target-pid=$target_pid"* ]] \
    || fail "Broker Sidecar does not reference the current OpenViking host PID $target_pid"
ssh "${ssh_options[@]}" "$TDVM_SSH_TARGET" sudo -n /usr/local/bin/docker logs "$BROKER_CONTAINER" 2>&1 \
    | grep -Fq 'OpenViking mTLS listener is ready' \
    || fail 'Broker Sidecar has not received the OpenViking X.509-SVID'

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
    'OpenViking: no SPIRE socket or key material in the Python container.' \
    'Broker Sidecar: actual OpenViking PID reference + spiffe://argus.local/service/openviking-cmem.'
