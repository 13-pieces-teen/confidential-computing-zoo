#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
M3_DIR="$(cd "$SCRIPT_DIR/../nodeattestor-mock" && pwd)"
RUNTIME_DIR="$M3_DIR/runtime"
SERVER_SOCKET="/opt/spire/run/server/api.sock"

cd "$M3_DIR"
bash "$M3_DIR/prepare.sh" >/dev/null

agent_count() {
    docker compose exec -T spire-server /opt/spire/bin/spire-server agent list \
        -socketPath "$SERVER_SOCKET" -output json | python3 -c \
        'import json,sys; print(len(json.load(sys.stdin).get("agents", [])))'
}

reset_agent_dir() {
    local directory="$1"
    mkdir -p "$directory/argus-tdx"
    chmod 0700 "$directory" "$directory/argus-tdx"
}

wait_for_log() {
    local pattern="$1" timeout_seconds="${2:-30}" start
    start="$(date +%s)"
    while true; do
        if docker compose logs --no-color spire-agent 2>&1 | grep -Fq "$pattern"; then
            return 0
        fi
        if (( $(date +%s) - start >= timeout_seconds )); then
            docker compose logs --no-color --tail=100 spire-agent >&2
            return 1
        fi
        read -r -t 1 _ || true
    done
}

start_case() {
    local name="$1" agent_directory="$2"
    shift 2
    docker compose rm -sf spire-agent fake-services >/dev/null 2>&1 || true
    env M4_AGENT_DATA_DIR="$agent_directory" "$@" docker compose up -d fake-services spire-agent >/dev/null
    printf 'started %s\n' "$name"
}

mkdir -p "$RUNTIME_DIR/m4"
case_directory="$(mktemp -d "$RUNTIME_DIR/m4/run-XXXXXXXX")"
replay_first="$case_directory/replay-first"
replay_second="$case_directory/replay-second"
provider_fault="$case_directory/provider-fault"
trustee_fault="$case_directory/trustee-fault"
trustee_timeout="$case_directory/trustee-timeout"
export M4_SERVER_DATA_DIR="$case_directory/server-data"
mkdir -p "$M4_SERVER_DATA_DIR"
chmod 0700 "$M4_SERVER_DATA_DIR"
chown 1000:1000 "$M4_SERVER_DATA_DIR"
for directory in "$replay_first" "$replay_second" "$provider_fault" "$trustee_fault" "$trustee_timeout"; do
    reset_agent_dir "$directory"
done

docker compose up -d --force-recreate spire-server >/dev/null
baseline_agents="$(agent_count)"

start_case replay-first "$replay_first" M4_REPLAY_EVIDENCE=true
wait_for_log "Node attestation was successful"
after_first="$(agent_count)"
if (( after_first != baseline_agents + 1 )); then
    echo "first replay-control Agent was not admitted" >&2
    exit 1
fi

# Keep fake-services alive so its first evidence remains cached; replace only the Agent.
docker compose rm -sf spire-agent >/dev/null
M4_AGENT_DATA_DIR="$replay_second" M4_REPLAY_EVIDENCE=true \
    docker compose up -d spire-agent >/dev/null
wait_for_log "Trustee returned HTTP 422"
after_replay="$(agent_count)"
if (( after_replay != after_first )); then
    echo "replayed evidence admitted a new Agent" >&2
    exit 1
fi
start_case provider-503 "$provider_fault" M4_EVIDENCE_STATUS=503
wait_for_log "evidence provider returned HTTP 503"
if (( $(agent_count) != after_first )); then
    echo "Provider failure admitted a new Agent" >&2
    exit 1
fi

start_case trustee-503 "$trustee_fault" M4_TRUSTEE_STATUS=503
wait_for_log "Trustee returned HTTP 503"
if (( $(agent_count) != after_first )); then
    echo "Trustee failure admitted a new Agent" >&2
    exit 1
fi

start_case trustee-timeout "$trustee_timeout" M4_TRUSTEE_DELAY=20s
wait_for_log "context deadline exceeded" 40
if (( $(agent_count) != after_first )); then
    echo "Trustee timeout admitted a new Agent" >&2
    exit 1
fi

server_metrics="$(curl -fsS "http://127.0.0.1:${M3_SERVER_METRICS_PORT:-39988}/metrics")"
require_metric() {
    local expected="$1"
    if ! grep -Fq "$expected" <<<"$server_metrics"; then
        echo "missing Server metric: $expected" >&2
        return 1
    fi
}
require_metric 'spire_server_argus_nodeattestor_attempts{host="spire-server",reason="ok",result="success",side="server"} 1'
require_metric 'spire_server_argus_nodeattestor_attempts{host="spire-server",reason="permission_denied",result="error",side="server"} 3'
require_metric 'spire_server_argus_nodeattestor_attempts{host="spire-server",reason="unavailable",result="error",side="server"} 1'
require_metric 'spire_server_argus_nodeattestor_trustee_requests{host="spire-server",reason="ok",result="success"} 1'
require_metric 'spire_server_argus_nodeattestor_trustee_requests{host="spire-server",reason="http_422",result="error"} 1'
require_metric 'spire_server_argus_nodeattestor_trustee_requests{host="spire-server",reason="http_503",result="error"} 1'
require_metric 'spire_server_argus_nodeattestor_trustee_requests{host="spire-server",reason="deadline_exceeded",result="error"} 1'

printf 'M4 software failure matrix passed\nBaseline agents: %s\nAgents after fresh control: %s\n' \
    "$baseline_agents" "$after_first"
printf 'Server attempts: 1 success, 4 rejected; Trustee failures: replay, HTTP 503, timeout\n'
printf 'Real Quote/QGS status: DEFERRED (mock v2 attestation profile)\n'
