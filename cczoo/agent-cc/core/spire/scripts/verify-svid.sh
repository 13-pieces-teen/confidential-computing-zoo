#!/usr/bin/env bash
set -euo pipefail

SPIRE_HOME="${SPIRE_HOME:-/opt/spire}"
SOCKET_DIR="${SPIRE_SOCKET_DIR:-/run/spire/sockets}"
OPENCLAW_CONTAINER="${OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
OPENVIKING_CONTAINER="${OPENVIKING_CONTAINER:-agentcc-openviking-service}"
OPENCLAW_ID="spiffe://argus.local/agent/openclaw"
OPENVIKING_ID="spiffe://argus.local/service/openviking-cmem"

fetch_svid() {
    docker exec "$1" spire-agent api fetch x509 \
        -socketPath "$SOCKET_DIR/agent.sock"
}

verify_identity() {
    local container="$1" expected="$2" forbidden="$3" output
    output="$(fetch_svid "$container")"
    grep -Fq "$expected" <<<"$output"
    if grep -Fq "$forbidden" <<<"$output"; then
        echo "$container received forbidden identity: $forbidden" >&2
        exit 1
    fi
    printf '%s\n' "$output"
}

verify_label() {
    local container="$1" expected="$2" actual
    actual="$(docker inspect "$container" \
        --format '{{index .Config.Labels "argus.workload"}}')"
    if [[ "$actual" != "$expected" ]]; then
        echo "$container has argus.workload=$actual; expected $expected" >&2
        exit 1
    fi
}

verify_negative_container() {
    local image="$1" label_value="${2:-}" output
    local label_args=()

    if [[ -n "$label_value" ]]; then
        label_args=(--label "argus.workload=$label_value")
    fi

    if output="$(docker run --rm \
        "${label_args[@]}" \
        -v "$SOCKET_DIR:$SOCKET_DIR" \
        -v "$SPIRE_HOME/bin/spire-agent:/usr/local/bin/spire-agent:ro" \
        --entrypoint /usr/local/bin/spire-agent \
        "$image" api fetch x509 \
        -socketPath "$SOCKET_DIR/agent.sock" 2>&1)"; then
        if grep -Fq "SPIFFE ID" <<<"$output"; then
            echo "Unregistered workload unexpectedly received an SVID:" >&2
            printf '%s\n' "$output" >&2
            exit 1
        fi
    fi
}

verify_label "$OPENCLAW_CONTAINER" openclaw
verify_label "$OPENVIKING_CONTAINER" openviking-cmem

echo "OpenClaw X.509-SVID:"
verify_identity "$OPENCLAW_CONTAINER" "$OPENCLAW_ID" "$OPENVIKING_ID"
echo
echo "OpenViking X.509-SVID:"
verify_identity "$OPENVIKING_CONTAINER" "$OPENVIKING_ID" "$OPENCLAW_ID"

openviking_image="$(docker inspect "$OPENVIKING_CONTAINER" --format '{{.Config.Image}}')"
verify_negative_container "$openviking_image"
verify_negative_container "$openviking_image" unregistered
verify_negative_container "$openviking_image" openclaw-invalid

echo "SVID identity, isolation, and negative selector checks passed."
