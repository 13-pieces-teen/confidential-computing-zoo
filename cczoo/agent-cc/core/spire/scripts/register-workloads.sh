#!/usr/bin/env bash
set -euo pipefail

SPIRE_HOME="${SPIRE_HOME:-/opt/spire}"
SERVER_BIN="$SPIRE_HOME/bin/spire-server"
SERVER_SOCKET="${SPIRE_SERVER_SOCKET:-/tmp/spire-server/private/api.sock}"

if [[ "$EUID" -ne 0 ]]; then
    echo "Run this script as root." >&2
    exit 1
fi

resolve_agent_parent() {
    if [[ -n "${ARGUS_AGENT_PARENT:-}" ]]; then
        printf '%s\n' "$ARGUS_AGENT_PARENT"
        return 0
    fi

    "$SERVER_BIN" agent list -socketPath "$SERVER_SOCKET" -output json | python3 -c '
import json
import sys
import time

agents = json.load(sys.stdin).get("agents", [])
valid_agents = [agent for agent in agents if int(agent.get("x509svid_expires_at", 0)) > time.time()]
if len(valid_agents) != 1:
    raise SystemExit("Expected exactly one non-expired Agent; set ARGUS_AGENT_PARENT explicitly")
agent_id = valid_agents[0]["id"]
print("spiffe://{}{}".format(agent_id["trust_domain"], agent_id["path"]))
'
}

create_entry() {
    local agent_parent="$1"
    local spiffe_id="$2"
    local selector="$3"
    local entry_id

    entry_id="$("$SERVER_BIN" entry show -socketPath "$SERVER_SOCKET" -spiffeID "$spiffe_id" -output json | grep -o '"id":"[^"]*"' | head -n 1 | cut -d '"' -f 4)"
    if [[ -n "$entry_id" ]]; then
        "$SERVER_BIN" entry update -socketPath "$SERVER_SOCKET" -entryID "$entry_id" -parentID "$agent_parent" -spiffeID "$spiffe_id" -selector "$selector" -x509SVIDTTL 600
        return 0
    fi

    "$SERVER_BIN" entry create -socketPath "$SERVER_SOCKET" -parentID "$agent_parent" -spiffeID "$spiffe_id" -selector "$selector" -x509SVIDTTL 600
}

agent_parent="$(resolve_agent_parent)"
echo "Using Agent parent: $agent_parent"

create_entry \
    "$agent_parent" \
    "spiffe://argus.local/agent/openclaw" \
    "docker:label:argus.workload:openclaw"
create_entry \
    "$agent_parent" \
    "spiffe://argus.local/service/openviking-cmem" \
    "docker:label:argus.workload:openviking-cmem"

"$SERVER_BIN" entry show -socketPath "$SERVER_SOCKET"
