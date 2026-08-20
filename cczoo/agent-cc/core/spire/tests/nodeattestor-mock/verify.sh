#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

SERVER_SOCKET="/opt/spire/run/server/api.sock"
AGENT_SOCKET="/opt/spire/run/agent/agent.sock"
NODE_ALIAS="spiffe://argus.local/node/openviking-m3"
WORKLOAD_ID="spiffe://argus.local/service/openviking-m3"
WRONG_PARENT_ID="spiffe://argus.local/test/wrong-parent"
WRONG_LABEL_ID="spiffe://argus.local/test/wrong-label"
BROKER_ID="spiffe://argus.local/infra/openviking-broker"
BROKER_TARGET_ID="spiffe://argus.local/service/openviking-cmem"
BROKER_CONTAINER="argus-m3-openviking-broker"
BROKER_TARGET_CONTAINER="argus-m3-broker-target"
WORKLOAD_DECISION="${M4_WORKLOAD_DECISION:-allow}"
FAKE_METRICS_URL="http://127.0.0.1:${M3_AGENT_METRICS_PORT:-39989}/metrics"

spire_server() {
    docker compose exec -T spire-server /opt/spire/bin/spire-server \
        "$@" -socketPath "$SERVER_SOCKET"
}

reset_entry() {
    spire_server entry delete -entryID "$1" >/dev/null 2>&1 || true
}

for entry_id in m3-node m3-workload m3-wrong-parent m3-wrong-label m3-wrong-digest m3-broker m3-broker-target; do
    reset_entry "$entry_id"
done

agent_json="$(spire_server agent list -output json)"
agent_id="$(python3 -c '
import json
import sys
agents = json.load(sys.stdin).get("agents", [])
if len(agents) != 1:
    raise SystemExit("expected exactly one attested M3 Agent")
identity = agents[0]["id"]
print("spiffe://{}{}".format(identity["trust_domain"], identity["path"]))
' <<<"$agent_json")"

broker_socket_stat="$(stat -c '%u:%g %a' "$SCRIPT_DIR/runtime/broker-run/broker.sock")"
[[ "$broker_socket_stat" == "0:1000 770" ]] \
    || { echo "Broker API socket permissions are $broker_socket_stat, expected 0:1000 770" >&2; exit 1; }

workload_image="$(docker image inspect ghcr.io/spiffe/spire-agent:1.15.2 --format '{{.Id}}')"
wrong_image="$(docker image inspect argus-spire-m3-negative-workload:local --format '{{.Id}}')"
broker_image="$(docker image inspect argus-openviking-broker-sidecar:local --format '{{.Id}}')"
broker_target_image="$(docker image inspect argus-spire-m3-broker-target:local --format '{{.Id}}')"
case "$workload_image" in
    sha256:[0-9a-f][0-9a-f]*) ;;
    *) echo "invalid workload image config digest: $workload_image" >&2; exit 1 ;;
esac

spire_server entry create \
    -entryID m3-node -node -spiffeID "$NODE_ALIAS" \
    -selector argus_tdx:policy:openviking-m3-v1 \
    -selector argus_tdx:debug:false \
    -selector argus_tdx:tcb_status:up_to_date >/dev/null

spire_server entry create \
    -entryID m3-workload -parentID "$NODE_ALIAS" -spiffeID "$WORKLOAD_ID" \
    -selector docker:label:argus.workload:openviking-cmem \
    -selector docker:image_id:"$workload_image" \
    -selector docker:image_config_digest:"$workload_image" \
    -x509SVIDTTL 600 >/dev/null

spire_server entry create \
    -entryID m3-wrong-parent \
    -parentID spiffe://argus.local/spire/agent/argus_tdx/ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff \
    -spiffeID "$WRONG_PARENT_ID" \
    -selector docker:label:argus.workload:openviking-cmem \
    -selector docker:image_id:"$workload_image" \
    -selector docker:image_config_digest:"$workload_image" >/dev/null

spire_server entry create \
    -entryID m3-wrong-label -parentID "$NODE_ALIAS" -spiffeID "$WRONG_LABEL_ID" \
    -selector docker:label:argus.workload:not-openviking \
    -selector docker:image_id:"$workload_image" \
    -selector docker:image_config_digest:"$workload_image" >/dev/null

run_workload() {
    local name="$1" image="$2" label="$3"
    docker run --rm --name "$name" --network none \
        --label "argus.workload=$label" \
        -v "$SCRIPT_DIR/runtime/agent-run:/opt/spire/run/agent:ro" \
        --entrypoint /opt/spire/bin/spire-agent \
        "$image" api fetch x509 -socketPath "$AGENT_SOCKET"
}

wait_for_positive_svid() {
    local deadline output
    deadline=$(( $(date +%s) + 30 ))
    while true; do
        if output="$(run_workload argus-m3-positive "$workload_image" openviking-cmem 2>&1)" &&
            grep -Fq "$WORKLOAD_ID" <<<"$output"; then
            printf '%s\n' "$output"
            return 0
        fi
        if (( $(date +%s) >= deadline )); then
            printf '%s\n' "$output" >&2
            return 1
        fi
        read -r -t 1 _ || true
    done
}

positive_output="$(wait_for_positive_svid)"
for forbidden in "$WRONG_PARENT_ID" "$WRONG_LABEL_ID"; do
    if grep -Fq "$forbidden" <<<"$positive_output"; then
        echo "positive workload received forbidden identity $forbidden" >&2
        exit 1
    fi
done

verify_denied() {
    local name="$1" image="$2" label="$3" output
    if output="$(run_workload "$name" "$image" "$label" 2>&1)"; then
        if grep -Fq "SPIFFE ID" <<<"$output"; then
            echo "$name unexpectedly received an SVID" >&2
            printf '%s\n' "$output" >&2
            exit 1
        fi
    fi
    grep -Fq "no identity issued" <<<"$output"
}

verify_denied argus-m3-wrong-label "$workload_image" wrong-label
verify_denied argus-m3-wrong-digest "$wrong_image" openviking-cmem

docker rm -f "$BROKER_CONTAINER" "$BROKER_TARGET_CONTAINER" >/dev/null 2>&1 || true
cleanup_broker_test() {
    docker rm -f "$BROKER_CONTAINER" "$BROKER_TARGET_CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup_broker_test EXIT

spire_server entry create \
    -entryID m3-broker -parentID "$NODE_ALIAS" -spiffeID "$BROKER_ID" \
    -selector docker:label:argus.component:openviking-broker \
    -selector docker:image_id:argus-openviking-broker-sidecar:local \
    -selector docker:image_config_digest:"$broker_image" \
    -x509SVIDTTL 600 >/dev/null

spire_server entry create \
    -entryID m3-broker-target -parentID "$NODE_ALIAS" -spiffeID "$BROKER_TARGET_ID" \
    -selector docker:label:argus.workload:openviking-cmem \
    -selector docker:image_id:argus-spire-m3-broker-target:local \
    -selector docker:image_config_digest:"$broker_target_image" \
    -selector argus_tdx_workload:verified:true \
    -selector argus_tdx_workload:workload_id:openviking-cmem \
    -selector argus_tdx_workload:policy:openviking-cmem-v1 \
    -disableX509SVIDPrefetch \
    -x509SVIDTTL 600 >/dev/null

docker run -d --name "$BROKER_TARGET_CONTAINER" --network none \
    --label argus.workload=openviking-cmem \
    argus-spire-m3-broker-target:local >/dev/null
target_pid="$(docker inspect "$BROKER_TARGET_CONTAINER" --format '{{.State.Pid}}')"

docker run -d --name "$BROKER_CONTAINER" --network host --pid host \
    --label argus.component=openviking-broker \
    -v "$SCRIPT_DIR/runtime/agent-run:/opt/spire/run/agent:ro" \
    -v "$SCRIPT_DIR/runtime/broker-run:/opt/spire/run/broker:ro" \
    argus-openviking-broker-sidecar:local \
    -workload-api=unix:///opt/spire/run/agent/agent.sock \
    -broker-socket=/opt/spire/run/broker/broker.sock \
    -broker-spiffe-id="$BROKER_ID" \
    -agent-spiffe-id="$agent_id" \
    -target-spiffe-id="$BROKER_TARGET_ID" \
    -client-spiffe-id=spiffe://argus.local/agent/openclaw \
    -target-pid="$target_pid" \
    -listen=127.0.0.1:21943 \
    -upstream=http://127.0.0.1:21933 >/dev/null

broker_ready=0
for _ in $(seq 1 30); do
    if docker logs "$BROKER_CONTAINER" 2>&1 | grep -Fq 'OpenViking mTLS listener is ready'; then
        broker_ready=1
        break
    fi
    [[ "$(docker inspect "$BROKER_CONTAINER" --format '{{.State.Running}}')" == true ]] || break
    sleep 1
done
if [[ "$WORKLOAD_DECISION" == deny ]]; then
    [[ "$broker_ready" == 0 ]] \
        || { echo 'Broker Sidecar received a target SVID while Mock Trustee decision was deny' >&2; exit 1; }
    [[ "$(docker inspect "$BROKER_CONTAINER" --format '{{.State.Running}}')" == false ]] \
        || { echo 'Broker Sidecar did not stop after the Trustee DENY' >&2; exit 1; }
    broker_logs="$(docker logs "$BROKER_CONTAINER" 2>&1)"
    grep -Fq 'Broker subscription denied' <<<"$broker_logs" \
        || { echo 'Broker Sidecar did not report the expected Broker permission denial' >&2; exit 1; }
    fake_metrics="$(curl -fsS "$FAKE_METRICS_URL")"
    grep -Eq '^argus_m4_fake_requests_total\{service="workload_trustee",result="denied"\} [1-9][0-9]*$' <<<"$fake_metrics" \
        || { echo 'Mock Trustee did not record a denied workload verification request' >&2; exit 1; }
    printf 'M3 Broker deny matrix passed: Mock Trustee recorded DENY and no target SVID was delivered (PID %s).\n' "$target_pid"
    exit 0
fi
if [[ "$broker_ready" != 1 ]]; then
    docker logs "$BROKER_CONTAINER" >&2 || true
    echo 'Broker Sidecar did not receive the strongly selected target SVID' >&2
    exit 1
fi

docker stop "$BROKER_TARGET_CONTAINER" >/dev/null
for _ in $(seq 1 10); do
    [[ "$(docker inspect "$BROKER_CONTAINER" --format '{{.State.Running}}')" == false ]] && break
    sleep 1
done
[[ "$(docker inspect "$BROKER_CONTAINER" --format '{{.State.Running}}')" == false ]] \
    || { echo 'Broker Sidecar did not stop after target pidfd exit' >&2; exit 1; }

printf 'M3 workload and Broker PID-reference matrix passed\nAgent parent: %s\nWorkload image config digest: %s\nBroker target PID: %s\n%s\n' \
    "$agent_id" "$workload_image" "$target_pid" "$positive_output"
