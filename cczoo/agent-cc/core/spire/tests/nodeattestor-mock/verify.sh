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

spire_server() {
    docker compose exec -T spire-server /opt/spire/bin/spire-server \
        "$@" -socketPath "$SERVER_SOCKET"
}

reset_entry() {
    spire_server entry delete -entryID "$1" >/dev/null 2>&1 || true
}

for entry_id in m3-node m3-workload m3-wrong-parent m3-wrong-label m3-wrong-digest; do
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

workload_image="$(docker image inspect ghcr.io/spiffe/spire-agent:1.15.1 --format '{{.Id}}')"
wrong_image="$(docker image inspect argus-spire-m3-negative-workload:local --format '{{.Id}}')"
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

printf 'M3 workload matrix passed\nAgent parent: %s\nWorkload image config digest: %s\n%s\n' \
    "$agent_id" "$workload_image" "$positive_output"
