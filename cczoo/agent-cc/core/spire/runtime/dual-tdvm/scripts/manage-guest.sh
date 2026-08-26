#!/usr/bin/env bash
set -euo pipefail

ROLE="${1:-}"
ACTION="${2:-status}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNTIME_DIR="${DUAL_RUNTIME_DIR:-$PROFILE_DIR/runtime}"
SPIRE_AGENT_IMAGE="${DUAL_SPIRE_AGENT_IMAGE:-ghcr.io/spiffe/spire-agent:1.15.2}"
PROVIDER_IMAGE="${DUAL_PROVIDER_IMAGE:-argus-spire-dual-mock-evidence-provider:local}"
REMOTE_ROOT="${DUAL_GUEST_ROOT:-/opt/argus-spire-dual}"
OPENCLAW_ID="spiffe://argus.local/agent/openclaw"
OPENCLAW_BROKER_ID="spiffe://argus.local/infra/openclaw-broker"
OPENVIKING_ID="spiffe://argus.local/service/openviking-cmem"
OPENVIKING_APPLICATION_READY="${DUAL_OPENVIKING_APPLICATION_READY:-0}"
OPENVIKING_OLLAMA_CONTAINER="${DUAL_OPENVIKING_OLLAMA_CONTAINER:-argus-dual-openviking-ollama}"
OPENVIKING_OLLAMA_IMAGE="${DUAL_OPENVIKING_OLLAMA_IMAGE:-}"
OPENVIKING_OLLAMA_MODEL="${DUAL_OPENVIKING_OLLAMA_MODEL:-bge-m3}"
OPENVIKING_OLLAMA_VOLUME="${DUAL_OPENVIKING_OLLAMA_VOLUME:-argus-dual-openviking-ollama-data}"
OPENVIKING_OLLAMA_API_BASE="${DUAL_OPENVIKING_OLLAMA_API_BASE:-http://${OPENVIKING_OLLAMA_CONTAINER}:11434/v1}"
OPENVIKING_OLLAMA_EXTRA_ENV="${DUAL_OPENVIKING_OLLAMA_EXTRA_ENV:-}"

fail() {
    printf 'dual TDVM %s %s: FAIL: %s\n' "${ROLE:-guest}" "$ACTION" "$1" >&2
    exit 1
}

case "$ROLE" in
    openclaw)
        SSH_TARGET="${DUAL_OPENCLAW_TDVM_SSH_TARGET:-}"
        SSH_PORT="${DUAL_OPENCLAW_TDVM_SSH_PORT:-22}"
        SSH_IDENTITY="${DUAL_OPENCLAW_TDVM_SSH_IDENTITY:-}"
        KNOWN_HOSTS="${DUAL_OPENCLAW_TDVM_KNOWN_HOSTS:-/tmp/argus-dual-openclaw-known-hosts}"
        INSTANCE_ID="${DUAL_OPENCLAW_TDVM_INSTANCE_ID:-tdvm-openclaw-0001}"
        REMOTE_DATA="${DUAL_OPENCLAW_GUEST_DATA:-/var/lib/argus-spire-dual/openclaw-agent}"
        REMOTE_RUN="${DUAL_OPENCLAW_GUEST_RUN:-/run/argus-spire-dual/openclaw}"
        REMOTE_BROKER_RUN="${DUAL_OPENCLAW_GUEST_BROKER_RUN:-/run/argus-spire-dual/openclaw-broker}"
        AGENT_CONFIG="openclaw-agent.conf"
        PROVIDER_CONTAINER="argus-dual-openclaw-evidence"
        AGENT_CONTAINER="argus-dual-openclaw-agent"
        WORKLOAD_CONTAINER="${DUAL_OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
        WORKLOAD_IMAGE="${DUAL_OPENCLAW_WORKLOAD_IMAGE:-argus-dual-openclaw:local}"
        BROKER_CONTAINER="${DUAL_OPENCLAW_BROKER_CONTAINER:-argus-dual-openclaw-egress}"
        BROKER_IMAGE="${DUAL_OPENCLAW_BROKER_IMAGE:-argus-openclaw-egress-sidecar:local}"
        ;;
    openviking)
        SSH_TARGET="${DUAL_OPENVIKING_TDVM_SSH_TARGET:-}"
        SSH_PORT="${DUAL_OPENVIKING_TDVM_SSH_PORT:-22}"
        SSH_IDENTITY="${DUAL_OPENVIKING_TDVM_SSH_IDENTITY:-}"
        KNOWN_HOSTS="${DUAL_OPENVIKING_TDVM_KNOWN_HOSTS:-/tmp/argus-dual-openviking-known-hosts}"
        INSTANCE_ID="${DUAL_OPENVIKING_TDVM_INSTANCE_ID:-tdvm-openviking-0001}"
        REMOTE_DATA="${DUAL_OPENVIKING_GUEST_DATA:-/var/lib/argus-spire-dual/openviking-agent}"
        REMOTE_RUN="${DUAL_OPENVIKING_GUEST_RUN:-/run/argus-spire-dual/openviking}"
        REMOTE_BROKER_RUN="${DUAL_OPENVIKING_GUEST_BROKER_RUN:-/run/argus-spire-dual/openviking-broker}"
        AGENT_CONFIG="openviking-agent.conf"
        PROVIDER_CONTAINER="argus-dual-openviking-evidence"
        AGENT_CONTAINER="argus-dual-openviking-agent"
        WORKLOAD_CONTAINER="${DUAL_OPENVIKING_CONTAINER:-agentcc-openviking-service}"
        WORKLOAD_IMAGE="${DUAL_OPENVIKING_WORKLOAD_IMAGE:-argus-dual-openviking:v0.4.8}"
        BROKER_CONTAINER="${DUAL_OPENVIKING_BROKER_CONTAINER:-agentcc-openviking-broker-sidecar}"
        BROKER_IMAGE="${DUAL_OPENVIKING_BROKER_IMAGE:-argus-openviking-broker-sidecar:local}"
        ;;
    *) fail 'role must be openclaw or openviking' ;;
esac

# A shared command drives both guests, but every path, socket, and container is
# selected by role so SPIFFE sockets and SVID material never cross TDVMs.
[[ -n "$SSH_TARGET" ]] \
    || fail "set DUAL_${ROLE^^}_TDVM_SSH_TARGET to the TDVM SSH destination"
[[ "$RUNTIME_DIR" == /* ]] \
    || fail "DUAL_RUNTIME_DIR must be an absolute host path: $RUNTIME_DIR"
[[ "$REMOTE_ROOT" == /* && "$REMOTE_DATA" == /* && "$REMOTE_RUN" == /* && "$REMOTE_BROKER_RUN" == /* ]] \
    || fail 'Guest root, data, and run paths must be absolute'
if [[ "$ROLE" == openviking ]]; then
    [[ "$OPENVIKING_APPLICATION_READY" == 0 || "$OPENVIKING_APPLICATION_READY" == 1 ]] \
        || fail 'DUAL_OPENVIKING_APPLICATION_READY must be 0 or 1'
    if [[ "$OPENVIKING_APPLICATION_READY" == 1 ]]; then
        [[ -n "$OPENVIKING_OLLAMA_IMAGE" ]] \
            || fail 'DUAL_OPENVIKING_OLLAMA_IMAGE is required when application readiness is enabled'
    fi
fi

ssh_options=(
    -o BatchMode=yes
    -o ConnectTimeout=10
    -o StrictHostKeyChecking=accept-new
    -o "UserKnownHostsFile=$KNOWN_HOSTS"
    -p "$SSH_PORT"
)
if [[ -n "$SSH_IDENTITY" ]]; then
    ssh_options+=(-i "$SSH_IDENTITY")
fi

# Preserve argv boundaries while executing commands through the guest SSH shell.
remote() {
    local command_string="" argument quoted
    for argument in "$@"; do
        printf -v quoted '%q' "$argument"
        command_string+="${command_string:+ }$quoted"
    done
    ssh "${ssh_options[@]}" "$SSH_TARGET" "$command_string"
}

remote_sudo() {
    remote sudo -n "$@"
}

require_local_image() {
    docker image inspect "$1" >/dev/null 2>&1 \
        || fail "local image is missing: $1"
}

stream_image() {
    local image="$1"
    printf 'Streaming %s to %s TDVM\n' "$image" "$ROLE"
    docker save "$image" | remote_sudo /usr/local/bin/docker load >/dev/null
}

wait_for_provider() {
    for _ in $(seq 1 30); do
        if remote curl -fsS --max-time 2 \
            http://127.0.0.1:18080/healthz >/dev/null 2>&1; then
            return
        fi
        read -r -t 1 _ || true
    done
    remote_sudo /usr/local/bin/docker logs --tail 80 "$PROVIDER_CONTAINER" >&2 || true
    fail 'mock Evidence Provider did not become healthy'
}

wait_for_agent() {
    for _ in $(seq 1 60); do
        if remote_sudo /usr/local/bin/docker exec "$AGENT_CONTAINER" \
            /opt/spire/bin/spire-agent healthcheck \
            -socketPath "/opt/spire/run/$ROLE/agent.sock" >/dev/null 2>&1; then
            return
        fi
        read -r -t 1 _ || true
    done
    remote_sudo /usr/local/bin/docker logs --tail 120 "$AGENT_CONTAINER" >&2 || true
    fail 'SPIRE Agent did not become healthy'
}

deploy_agent() {
    command -v docker >/dev/null 2>&1 || fail 'Docker is required on the deployment host'
    [[ -s "$RUNTIME_DIR/conf/$AGENT_CONFIG" ]] \
        || fail "missing generated $AGENT_CONFIG; run prepare.sh first"
    [[ -s "$RUNTIME_DIR/certs/upstream-ca.pem" ]] \
        || fail 'missing generated SPIRE upstream CA'
    [[ -x "$RUNTIME_DIR/plugins/argus-tdx-nodeattestor-agent" ]] \
        || fail 'missing argus_tdx Agent plugin'
    if [[ "$ROLE" == openviking ]]; then
        [[ -x "$RUNTIME_DIR/plugins/argus-tdx-workloadattestor" ]] \
            || fail 'missing argus_tdx_workload plugin'
        for certificate in trustee-ca.pem trustee-client.pem trustee-client-key.pem; do
            [[ -s "$RUNTIME_DIR/certs/$certificate" ]] \
                || fail "missing generated Trustee client material: $certificate"
        done
    fi
    require_local_image "$PROVIDER_IMAGE"
    require_local_image "$SPIRE_AGENT_IMAGE"

    # Cross into the guest only after proving this is a TDVM with its own Docker.
    remote true
    remote test -c /dev/tdx_guest || fail 'SSH target is not a TD Guest'
    remote_sudo test -x /usr/local/bin/docker \
        || fail 'Guest Docker must be installed at /usr/local/bin/docker'

    stream_image "$PROVIDER_IMAGE"
    stream_image "$SPIRE_AGENT_IMAGE"

    remote_sudo install -d -m 0755 \
        "$REMOTE_ROOT/conf" \
        "$REMOTE_ROOT/certs" \
        "$REMOTE_ROOT/plugins" \
        "$REMOTE_RUN"
    remote_sudo install -d -m 2770 "$REMOTE_BROKER_RUN"
    remote_sudo install -d -m 0700 "$REMOTE_DATA" "$REMOTE_DATA/argus-tdx"
    tar -C "$RUNTIME_DIR" -cpf - \
        "conf/$AGENT_CONFIG" \
        certs/upstream-ca.pem \
        plugins/argus-tdx-nodeattestor-agent \
        | remote_sudo tar -C "$REMOTE_ROOT" -xpf -
    if [[ "$ROLE" == openviking ]]; then
        tar -C "$RUNTIME_DIR" -cpf - \
            certs/trustee-ca.pem \
            certs/trustee-client.pem \
            certs/trustee-client-key.pem \
            plugins/argus-tdx-workloadattestor \
            | remote_sudo tar -C "$REMOTE_ROOT" -xpf -
        remote_sudo chmod 0755 "$REMOTE_ROOT/plugins/argus-tdx-workloadattestor"
        remote_sudo chmod 0644 \
            "$REMOTE_ROOT/certs/trustee-ca.pem" \
            "$REMOTE_ROOT/certs/trustee-client.pem"
        remote_sudo chmod 0600 "$REMOTE_ROOT/certs/trustee-client-key.pem"
    fi
    remote_sudo chmod 0755 "$REMOTE_ROOT/plugins/argus-tdx-nodeattestor-agent"
    remote_sudo chmod 0644 \
        "$REMOTE_ROOT/conf/$AGENT_CONFIG" \
        "$REMOTE_ROOT/certs/upstream-ca.pem"
    remote_sudo chown -R 1000:1000 \
        "$REMOTE_ROOT/conf" \
        "$REMOTE_ROOT/certs" \
        "$REMOTE_ROOT/plugins" \
        "$REMOTE_DATA" \
        "$REMOTE_RUN"
    remote_sudo chown -R 1000:1000 "$REMOTE_BROKER_RUN"
    remote_sudo chmod 2770 "$REMOTE_BROKER_RUN"

    # Evidence is collected inside this TDVM; verification stays at the center
    # Trustee, so the two sides of the attestation path remain separate.
    remote_sudo /usr/local/bin/docker rm -f "$PROVIDER_CONTAINER" >/dev/null 2>&1 || true
    remote_sudo /usr/local/bin/docker run -d \
        --name "$PROVIDER_CONTAINER" \
        --network host \
        --restart unless-stopped \
        "$PROVIDER_IMAGE" \
        -listen=127.0.0.1:18080 \
        "-instance-id=$INSTANCE_ID" \
        -tcb-status=up_to_date \
        -mrtd=aabb \
        -rtmr-0=0011 \
        -workload-id=openviking-cmem \
        -workload-policy-id=openviking-cmem-v1 \
        "-replay-evidence=${DUAL_REPLAY_EVIDENCE:-false}" \
        "-evidence-status=${DUAL_EVIDENCE_STATUS:-0}" \
        "-evidence-delay=${DUAL_EVIDENCE_DELAY:-0s}" >/dev/null
    wait_for_provider

    remote_sudo /usr/local/bin/docker rm -f "$AGENT_CONTAINER" >/dev/null 2>&1 || true
    agent_mounts=(
        -v "$REMOTE_ROOT/conf/$AGENT_CONFIG:/opt/spire/conf/$AGENT_CONFIG:ro"
        -v "$REMOTE_ROOT/certs:/opt/spire/conf/certs:ro"
        -v "$REMOTE_ROOT/plugins:/opt/spire/plugins:ro"
        -v "$REMOTE_DATA:/opt/spire/data/$ROLE-agent"
        -v "$REMOTE_RUN:/opt/spire/run/$ROLE"
        -v /var/run/docker.sock:/var/run/docker.sock:ro
    )
    agent_mounts+=(
        -v "$REMOTE_BROKER_RUN:/opt/spire/run/$ROLE-broker"
    )
    # Host PID visibility lets the Agent bind workload identity to the real target
    # process while exposing only role-local Workload and Broker API sockets.
    remote_sudo /usr/local/bin/docker run -d \
        --name "$AGENT_CONTAINER" \
        --network host \
        --pid host \
        --restart unless-stopped \
        "${agent_mounts[@]}" \
        "$SPIRE_AGENT_IMAGE" \
        -config "/opt/spire/conf/$AGENT_CONFIG" >/dev/null
    wait_for_agent

    broker_socket_stat="$(remote_sudo stat -c '%u:%g %a' "$REMOTE_BROKER_RUN/broker.sock")"
    [[ "$broker_socket_stat" == '0:1000 770' ]] \
        || fail "Broker API socket permissions are $broker_socket_stat, expected 0:1000 770"

    printf '%s\n' \
        "$ROLE TDVM SPIRE Agent is healthy." \
        "TDVM instance ID: $INSTANCE_ID" \
        "Workload API directory: $REMOTE_RUN" \
        "Broker API directory: ${REMOTE_BROKER_RUN:-not-enabled}" \
        'Attestation path: Guest-local mock Evidence Provider -> center mock Trustee.'
}

load_workload() {
    command -v docker >/dev/null 2>&1 || fail 'Docker is required on the deployment host'
    require_local_image "$WORKLOAD_IMAGE"
    # Stream deployment artifacts directly into the selected TDVM; no guest is
    # allowed to depend on the other guest's image store.
    stream_image "$WORKLOAD_IMAGE"

    if [[ "$ROLE" == openclaw ]]; then
        local guard_image sandbox_image
        guard_image="${DUAL_GUARD_IMAGE:-argus-spire-dual-guard:local}"
        sandbox_image="${DUAL_OPENCLAW_SANDBOX_IMAGE:-openclaw-sandbox:bookworm-slim}"
        require_local_image "$guard_image"
        require_local_image "$sandbox_image"
        require_local_image "$BROKER_IMAGE"
        stream_image "$guard_image"
        stream_image "$sandbox_image"
        stream_image "$BROKER_IMAGE"
        # Guard and gateway secrets are staged in the OpenClaw TDVM only. The business
        # container still receives no SPIFFE/SVID material or Guard token.
        remote_sudo /usr/local/bin/docker tag \
            "$sandbox_image" openclaw-sandbox:bookworm-slim

        for path in \
            "$RUNTIME_DIR/conf/guard-policy.yaml" \
            "$RUNTIME_DIR/secrets/guard-api-token" \
            "$RUNTIME_DIR/secrets/openclaw-guard-api-token" \
            "$RUNTIME_DIR/secrets/openclaw-gateway-token"; do
            [[ -s "$path" ]] || fail "missing generated file: $path"
        done
        remote_sudo install -d -m 0755 "$REMOTE_ROOT/conf" "$REMOTE_ROOT/secrets"
        tar -C "$RUNTIME_DIR" -cpf - \
            conf/guard-policy.yaml \
            secrets/guard-api-token \
            secrets/openclaw-guard-api-token \
            secrets/openclaw-gateway-token \
            | remote_sudo tar -C "$REMOTE_ROOT" -xpf -
        remote_sudo chown root:root \
            "$REMOTE_ROOT/conf/guard-policy.yaml" \
            "$REMOTE_ROOT/secrets/openclaw-gateway-token"
        remote_sudo chown 65532:65532 "$REMOTE_ROOT/secrets/guard-api-token"
        remote_sudo chown 1000:1000 "$REMOTE_ROOT/secrets/openclaw-guard-api-token"
        remote_sudo chmod 0444 "$REMOTE_ROOT/conf/guard-policy.yaml"
        remote_sudo chmod 0400 \
            "$REMOTE_ROOT/secrets/guard-api-token" \
            "$REMOTE_ROOT/secrets/openclaw-guard-api-token" \
            "$REMOTE_ROOT/secrets/openclaw-gateway-token"
        remote_sudo install -d -m 0755 "$REMOTE_ROOT/bin"
        remote_sudo tee "$REMOTE_ROOT/bin/connect_openclaw_openviking.sh" \
            <"$PROFILE_DIR/../../../../adapters/OpenClaw/scripts/connect_openclaw_openviking.sh" >/dev/null
        remote_sudo tee "$REMOTE_ROOT/bin/verify_openclaw_plugin_e2e.sh" \
            <"$PROFILE_DIR/../../../../adapters/OpenClaw/scripts/verify_openclaw_plugin_e2e.sh" >/dev/null
        remote_sudo chmod 0755 \
            "$REMOTE_ROOT/bin/connect_openclaw_openviking.sh" \
            "$REMOTE_ROOT/bin/verify_openclaw_plugin_e2e.sh"
    else
        local openviking_config source_image tc_api_url
        openviking_config="${DUAL_OPENVIKING_CONFIG:-}"
        source_image="${DUAL_OPENVIKING_SOURCE_IMAGE:-localhost:5000/openviking:v0.4.8}"
        tc_api_url="${DUAL_TC_API_URL:-http://127.0.0.1:8000}"
        [[ -n "$openviking_config" && "$openviking_config" == /* && -s "$openviking_config" ]] \
            || fail 'DUAL_OPENVIKING_CONFIG must name an absolute, non-empty ov.conf file'
        require_local_image "$BROKER_IMAGE"
        stream_image "$BROKER_IMAGE"
        if [[ "$OPENVIKING_APPLICATION_READY" == 1 ]]; then
            python3 - "$openviking_config" "$OPENVIKING_OLLAMA_API_BASE" "$OPENVIKING_OLLAMA_MODEL" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as stream:
    config = json.load(stream)
embedding = config.get("embedding") or {}
dense = embedding.get("dense") or embedding
if dense.get("provider") != "ollama":
    raise SystemExit("DUAL_OPENVIKING_APPLICATION_READY=1 requires embedding provider=ollama")
if str(dense.get("api_base", "")).rstrip("/") != sys.argv[2].rstrip("/"):
    raise SystemExit("OpenViking embedding api_base must be {}".format(sys.argv[2]))
if dense.get("model") != sys.argv[3]:
    raise SystemExit("OpenViking embedding model must be {}".format(sys.argv[3]))
PY
            require_local_image "$OPENVIKING_OLLAMA_IMAGE"
            stream_image "$OPENVIKING_OLLAMA_IMAGE"
        fi
        # TC-API and the registry are pre-existing OpenViking-TDVM services; this step
        # loads the approved source image and launcher without starting identity yet.
        remote curl -fsS --max-time 5 "$tc_api_url/" >/dev/null \
            || fail "existing TC-API is not healthy at $tc_api_url"
        remote curl -fsS --max-time 5 http://127.0.0.1:5000/v2/ >/dev/null \
            || fail 'existing OpenViking TDVM registry is not healthy at http://127.0.0.1:5000/v2/'
        remote_sudo /usr/local/bin/docker tag "$WORKLOAD_IMAGE" "$source_image"
        remote_sudo /usr/local/bin/docker push "$source_image" >/dev/null

        remote_sudo install -d -m 0755 "$REMOTE_ROOT/bin"
        remote_sudo tee "$REMOTE_ROOT/bin/launch_openviking.sh" \
            <"$PROFILE_DIR/../../../../adapters/OpenViking/scripts/launch_openviking.sh" >/dev/null
        remote_sudo chmod 0755 "$REMOTE_ROOT/bin/launch_openviking.sh"
        remote_sudo install -d -m 0700 \
            /var/lib/argus-spire-dual/openviking-state
        remote_sudo tee \
            /var/lib/argus-spire-dual/openviking-state/ov.conf \
            <"$openviking_config" >/dev/null
        remote_sudo chmod 0400 \
            /var/lib/argus-spire-dual/openviking-state/ov.conf
    fi

    printf '%s workload image and runtime files are loaded in its TDVM.\n' "$ROLE"
}

start_openviking_ollama() {
    local network="$1"
    local ready=0
    local -a ollama_envs=(--env "OLLAMA_HOST=0.0.0.0:11434")
    local -a ollama_extra=()
    local pair
    local user_no_proxy=""
    local no_proxy
    local -a no_proxy_mandatory=(localhost 127.0.0.1 0.0.0.0 ::1 10.0.0.0/8 172.16.0.0/12)

    [[ "$OPENVIKING_APPLICATION_READY" == 1 ]] || return 0
    remote_sudo /usr/local/bin/docker image inspect "$OPENVIKING_OLLAMA_IMAGE" >/dev/null \
        || fail "Ollama image is not loaded in OpenViking TDVM: $OPENVIKING_OLLAMA_IMAGE"
    remote_sudo /usr/local/bin/docker rm -f "$OPENVIKING_OLLAMA_CONTAINER" >/dev/null 2>&1 || true
    remote_sudo /usr/local/bin/docker volume create "$OPENVIKING_OLLAMA_VOLUME" >/dev/null
    # In proxy-only environments, pass extra Ollama settings as a
    # space-separated KEY=VALUE list in DUAL_OPENVIKING_OLLAMA_EXTRA_ENV.
    if [[ -n "$OPENVIKING_OLLAMA_EXTRA_ENV" ]]; then
        read -r -a ollama_extra <<<"$OPENVIKING_OLLAMA_EXTRA_ENV" || true
        for pair in "${ollama_extra[@]}"; do
            case "$pair" in
                NO_PROXY=*|no_proxy=*) user_no_proxy="${pair#*=}" ;;
            esac
            ollama_envs+=(--env "$pair")
        done
    fi
    # The Ollama CLI honors HTTP(S)_PROXY even for 0.0.0.0:11434, which would
    # send ls/pull through the proxy. Preserve caller NO_PROXY values and always
    # exempt loopback and container ranges.
    no_proxy="$user_no_proxy"
    [[ -n "$no_proxy" ]] && no_proxy="$no_proxy,"
    no_proxy+="$(IFS=,; echo "${no_proxy_mandatory[*]}")"
    ollama_envs+=(--env "NO_PROXY=$no_proxy" --env "no_proxy=$no_proxy")
    remote_sudo /usr/local/bin/docker run -d \
        --name "$OPENVIKING_OLLAMA_CONTAINER" \
        --network "$network" \
        --restart unless-stopped \
        "${ollama_envs[@]}" \
        --volume "$OPENVIKING_OLLAMA_VOLUME:/root/.ollama" \
        "$OPENVIKING_OLLAMA_IMAGE" >/dev/null
    for _ in $(seq 1 60); do
        if remote_sudo /usr/local/bin/docker exec "$OPENVIKING_OLLAMA_CONTAINER" \
            ollama ls >/dev/null 2>&1; then
            ready=1
            break
        fi
        read -r -t 1 _ || true
    done
    if [[ "$ready" != 1 ]]; then
        remote_sudo /usr/local/bin/docker logs --tail 120 "$OPENVIKING_OLLAMA_CONTAINER" >&2 || true
        fail 'Ollama did not become ready in the OpenViking TDVM'
    fi
    if ! remote_sudo /usr/local/bin/docker exec "$OPENVIKING_OLLAMA_CONTAINER" \
        ollama show "$OPENVIKING_OLLAMA_MODEL" >/dev/null 2>&1; then
        remote_sudo /usr/local/bin/docker exec "$OPENVIKING_OLLAMA_CONTAINER" \
            ollama pull "$OPENVIKING_OLLAMA_MODEL"
    fi
    remote_sudo /usr/local/bin/docker exec "$OPENVIKING_OLLAMA_CONTAINER" \
        ollama show "$OPENVIKING_OLLAMA_MODEL" >/dev/null
    printf 'OpenViking application dependency is ready: Ollama model %s\n' "$OPENVIKING_OLLAMA_MODEL"
}

wait_openclaw_broker() {
    local broker_logs running
    for _ in $(seq 1 180); do
        broker_logs="$(remote_sudo /usr/local/bin/docker logs "$BROKER_CONTAINER" 2>&1 || true)"
        if grep -Fq "OpenClaw Egress Broker is ready for identity $OPENCLAW_ID" <<<"$broker_logs"; then
            return
        fi
        running="$(remote_sudo /usr/local/bin/docker inspect "$BROKER_CONTAINER" \
            --format '{{.State.Running}}' 2>/dev/null || true)"
        if [[ "$running" != true ]]; then
            remote_sudo /usr/local/bin/docker logs "$BROKER_CONTAINER" >&2 || true
            fail 'OpenClaw Egress Broker exited before its target identity became ready'
        fi
        read -r -t 1 _ || true
    done
    remote_sudo /usr/local/bin/docker logs "$BROKER_CONTAINER" >&2 || true
    fail 'OpenClaw Egress Broker did not receive the OpenClaw target identity within 180s'
}

start_openclaw_broker() {
    local network origin target_address target_host agent_id egress_port
    network="${DUAL_OPENCLAW_NETWORK:-argus-dual-openclaw}"
    origin="${DUAL_OPENVIKING_ORIGIN:-https://openviking.argus.local:1943}"
    target_address="${DUAL_OPENVIKING_HOST_ADDRESS:-}"
    target_host="${origin#https://}"
    target_host="${target_host%%:*}"
    agent_id="${DUAL_OPENCLAW_PARENT_ID:-}"
    egress_port="${DUAL_OPENCLAW_EGRESS_PORT:-1934}"
    [[ -n "$target_address" ]] \
        || fail 'DUAL_OPENVIKING_HOST_ADDRESS must be reachable from the OpenClaw TDVM'
    [[ "$agent_id" == spiffe://argus.local/spire/agent/argus_tdx/* ]] \
        || fail 'DUAL_OPENCLAW_PARENT_ID must be the current OpenClaw Agent SPIFFE ID'

    # The Egress Broker, not OpenClaw, receives the Workload API and Broker API.
    # Binding target-pid ties the requested OpenClaw identity to the live process.
    remote_sudo bash -s -- \
        "$REMOTE_ROOT" "$REMOTE_RUN" "$REMOTE_BROKER_RUN" \
        "$WORKLOAD_CONTAINER" "$BROKER_CONTAINER" "$BROKER_IMAGE" "$network" \
        "$origin" "$target_address" "$target_host" "$OPENCLAW_ID" "$OPENVIKING_ID" \
        "$OPENCLAW_BROKER_ID" "$agent_id" "$egress_port" <<'REMOTE'
set -euo pipefail
remote_root="$1"
remote_run="$2"
remote_broker_run="$3"
workload_container="$4"
broker_container="$5"
broker_image="$6"
network="$7"
origin="$8"
target_address="$9"
target_host="${10}"
openclaw_id="${11}"
openviking_id="${12}"
broker_id="${13}"
agent_id="${14}"
egress_port="${15}"
docker=/usr/local/bin/docker

[[ "$($docker inspect "$workload_container" --format '{{.State.Running}}')" == true ]]
target_pid="$($docker inspect "$workload_container" --format '{{.State.Pid}}')"
[[ "$target_pid" =~ ^[1-9][0-9]*$ ]]
$docker rm -f "$broker_container" >/dev/null 2>&1 || true
$docker run -d \
    --name "$broker_container" \
    --network "$network" \
    --pid host \
    --label argus.component=openclaw-broker \
    --add-host "$target_host:$target_address" \
    -v "$remote_run:/opt/spire/run/agent:ro" \
    -v "$remote_broker_run:/opt/spire/run/broker:ro" \
    -v "$remote_root/secrets/openclaw-guard-api-token:/run/secrets/argus_guard_api_token:ro" \
    "$broker_image" \
    -workload-api=unix:///opt/spire/run/agent/agent.sock \
    -broker-socket=/opt/spire/run/broker/broker.sock \
    "-broker-spiffe-id=$broker_id" \
    "-agent-spiffe-id=$agent_id" \
    "-target-spiffe-id=$openclaw_id" \
    "-server-spiffe-id=$openviking_id" \
    "-target-pid=$target_pid" \
    "-listen=0.0.0.0:$egress_port" \
    "-target=$origin" \
    -guard-url=http://argus-dual-openclaw-guard:8007/guard/v1/authorize \
    -guard-token-file=/run/secrets/argus_guard_api_token \
    -target-service=openviking-cmem \
    -data-class=sensitive >/dev/null
REMOTE
    wait_openclaw_broker
}

start_openclaw() {
    local guard_image sandbox_image network origin target_address gateway_port bridge_port
    guard_image="${DUAL_GUARD_IMAGE:-argus-spire-dual-guard:local}"
    sandbox_image="${DUAL_OPENCLAW_SANDBOX_IMAGE:-openclaw-sandbox:bookworm-slim}"
    network="${DUAL_OPENCLAW_NETWORK:-argus-dual-openclaw}"
    origin="${DUAL_OPENVIKING_ORIGIN:-https://openviking.argus.local:1943}"
    target_address="${DUAL_OPENVIKING_HOST_ADDRESS:-}"
    gateway_port="${DUAL_OPENCLAW_GATEWAY_PORT:-18789}"
    bridge_port="${DUAL_OPENCLAW_BRIDGE_PORT:-18790}"
    [[ -n "$target_address" ]] \
        || fail 'DUAL_OPENVIKING_HOST_ADDRESS must be reachable from the OpenClaw TDVM'

    remote_sudo test -S "$REMOTE_RUN/agent.sock" \
        || fail 'OpenClaw Workload API socket is missing; deploy its Agent first'
    remote_sudo test -S "$REMOTE_BROKER_RUN/broker.sock" \
        || fail 'OpenClaw Broker API socket is missing; deploy its Agent first'
    for image in "$WORKLOAD_IMAGE" "$BROKER_IMAGE" "$guard_image" "$sandbox_image"; do
        remote_sudo /usr/local/bin/docker image inspect "$image" >/dev/null \
            || fail "image is not loaded in OpenClaw TDVM: $image"
    done
    remote_sudo test -s "$REMOTE_ROOT/conf/guard-policy.yaml" \
        || fail 'Guard policy is not loaded'
    remote_sudo test -s "$REMOTE_ROOT/secrets/openclaw-gateway-token" \
        || fail 'OpenClaw gateway token is not loaded'

    # Assemble the caller-local path: OpenClaw -> Guard -> Egress Broker. Only the
    # Broker is given the fixed remote origin and the identity sockets.
    remote_sudo bash -s -- \
        "$REMOTE_ROOT" "$WORKLOAD_CONTAINER" "$WORKLOAD_IMAGE" \
        "$BROKER_CONTAINER" "$guard_image" "$network" "$gateway_port" "$bridge_port" <<'REMOTE'
set -euo pipefail
remote_root="$1"
workload_container="$2"
workload_image="$3"
broker_container="$4"
guard_image="$5"
network="$6"
gateway_port="$7"
bridge_port="$8"
docker=/usr/local/bin/docker
guard_container=argus-dual-openclaw-guard
config_volume=argus-dual-openclaw-config
workspace_volume=argus-dual-openclaw-workspace

$docker network inspect "$network" >/dev/null 2>&1 \
    || $docker network create "$network" >/dev/null
# dockerd runs with --ip-masq=false, so workload traffic leaving the TDVM's
# slirp NIC is never NATed and business requests to the host relay (10.0.2.2)
# time out. Masquerade the workload bridge for container egress.
bridge_subnet="$($docker network inspect "$network" --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}')"
slirp_nic="$(ip route | awk '/^default/ {print $5; exit}')"
if [[ -n "$bridge_subnet" && -n "$slirp_nic" ]]; then
    iptables -t nat -C POSTROUTING -s "$bridge_subnet" -o "$slirp_nic" -j MASQUERADE 2>/dev/null \
        || iptables -t nat -A POSTROUTING -s "$bridge_subnet" -o "$slirp_nic" -j MASQUERADE
fi
$docker rm -f "$broker_container" "$workload_container" "$guard_container" >/dev/null 2>&1 || true
$docker run -d \
    --name "$guard_container" \
    --network "$network" \
    --restart unless-stopped \
    -e HOST=0.0.0.0 \
    -e PORT=8007 \
    -e GUARD_MODE=spiffe_identity \
    -e GUARD_SPIFFE_POLICY_FILE=/opt/argus/conf/guard-policy.yaml \
    -e ARGUS_API_TOKEN_FILE=/run/secrets/argus_guard_api_token \
    -v "$remote_root/conf/guard-policy.yaml:/opt/argus/conf/guard-policy.yaml:ro" \
    -v "$remote_root/secrets/guard-api-token:/run/secrets/argus_guard_api_token:ro" \
    "$guard_image" >/dev/null

gateway_token="$(tr -d '\r\n' <"$remote_root/secrets/openclaw-gateway-token")"
$docker run --rm \
    --user root \
    -v "$config_volume:/home/node/.openclaw" \
    -v "$workspace_volume:/home/node/.openclaw/workspace" \
    --entrypoint sh \
    "$workload_image" -ceu '
        chown -R node:node /home/node/.openclaw
        mkdir -p /home/node/.openclaw/identity /home/node/.openclaw/agents/main/agent /home/node/.openclaw/agents/main/sessions
        chown -R node:node /home/node/.openclaw
    '

openclaw_config() {
    $docker run --rm \
        --user node \
        -e "OPENCLAW_GATEWAY_TOKEN=$gateway_token" \
        -v "$config_volume:/home/node/.openclaw" \
        -v "$workspace_volume:/home/node/.openclaw/workspace" \
        --entrypoint node \
        "$workload_image" /app/dist/index.js config set "$@" >/dev/null
}
openclaw_config gateway.mode local
openclaw_config gateway.bind lan
openclaw_config agents.defaults.sandbox.mode all
openclaw_config agents.defaults.sandbox.scope agent
openclaw_config agents.defaults.sandbox.workspaceAccess rw
openclaw_config agents.defaults.sandbox.backend docker
openclaw_config gateway.controlUi.allowedOrigins \
    "[\"http://localhost:${gateway_port}\",\"http://127.0.0.1:${gateway_port}\"]" --strict-json

docker_socket_gid="$(stat -c '%g' /var/run/docker.sock)"
$docker run -d \
    --name "$workload_container" \
    --init \
    --restart unless-stopped \
    --network "$network" \
    --label argus.workload=openclaw \
    --group-add "$docker_socket_gid" \
    --device /dev/tdx_guest \
    -p "$gateway_port:18789" \
    -p "$bridge_port:18790" \
    -v "$config_volume:/home/node/.openclaw" \
    -v "$workspace_volume:/home/node/.openclaw/workspace" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -e OPENCLAW_GATEWAY_TOKEN="$gateway_token" \
    -e OPENCLAW_GATEWAY_BIND=lan \
    -e OPENCLAW_GATEWAY_PORT=18789 \
    --entrypoint node \
    "$workload_image" /app/dist/index.js gateway --bind lan --port 18789 >/dev/null

REMOTE
    start_openclaw_broker
    printf '%s\n' \
        'OpenClaw, caller-local Guard, and Egress Broker are ready in the OpenClaw TDVM.' \
        "OpenClaw Egress endpoint: http://$BROKER_CONTAINER:${DUAL_OPENCLAW_EGRESS_PORT:-1934}" \
        "OpenViking origin: $origin" \
        'OpenClaw has no SPIRE socket, SVID, Guard token, or Argus preload.'
}

start_openviking() {
    local port state_dir source_image image_url runtime_image_id tc_api_url agent_id identity_token bearer_token network runtime_digest container_digest
    port="${DUAL_OPENVIKING_PORT:-1943}"
    state_dir="/var/lib/argus-spire-dual/openviking-state"
    source_image="${DUAL_OPENVIKING_SOURCE_IMAGE:-localhost:5000/openviking:v0.4.8}"
    image_url="${DUAL_OPENVIKING_TC_API_IMAGE_URL:-docker://registry:5000/openviking:v0.4.8}"
    runtime_image_id="${DUAL_OPENVIKING_RUNTIME_IMAGE_ID:-openviking-cmem:latest}"
    tc_api_url="${DUAL_TC_API_URL:-http://127.0.0.1:8000}"
    agent_id="${DUAL_OPENVIKING_PARENT_ID:-}"
    identity_token="${DUAL_TC_API_IDENTITY_TOKEN:-}"
    bearer_token="${DUAL_TC_API_BEARER_TOKEN:-}"
    network="${DUAL_OPENVIKING_NETWORK:-argus-dual-openviking}"
    [[ "$agent_id" == spiffe://argus.local/spire/agent/argus_tdx/* ]] \
        || fail 'DUAL_OPENVIKING_PARENT_ID must be the current OpenViking Agent SPIFFE ID'
    [[ -z "$identity_token" || -z "$bearer_token" ]] \
        || fail 'set only one of DUAL_TC_API_IDENTITY_TOKEN or DUAL_TC_API_BEARER_TOKEN'
    [[ -n "$identity_token" || -n "$bearer_token" ]] \
        || fail 'non-interactive launch requires DUAL_TC_API_IDENTITY_TOKEN or DUAL_TC_API_BEARER_TOKEN'
    remote_sudo test -S "$REMOTE_RUN/agent.sock" \
        || fail 'OpenViking Workload API socket is missing; deploy its Agent first'
    remote_sudo test -S "$REMOTE_BROKER_RUN/broker.sock" \
        || fail 'OpenViking Broker API socket is missing; deploy its Agent first'
    remote_sudo test -s "$state_dir/ov.conf" \
        || fail 'OpenViking ov.conf is missing; load its workload first'
    remote_sudo test -x "$REMOTE_ROOT/bin/launch_openviking.sh" \
        || fail 'OpenViking launcher is missing; load its workload first'
    for image in "$source_image" "$BROKER_IMAGE"; do
        remote_sudo /usr/local/bin/docker image inspect "$image" >/dev/null \
            || fail "image is not loaded in OpenViking TDVM: $image"
    done
    remote curl -fsS --max-time 5 "$tc_api_url/" >/dev/null \
        || fail "existing TC-API is not healthy at $tc_api_url"

    remote_sudo /usr/local/bin/docker rm -f \
        "$BROKER_CONTAINER" "$WORKLOAD_CONTAINER" >/dev/null 2>&1 || true
    remote_sudo /usr/local/bin/docker network inspect "$network" >/dev/null 2>&1 \
        || remote_sudo /usr/local/bin/docker network create "$network" >/dev/null
    start_openviking_ollama "$network"
    # Launch the SPIFFE-credential-free business container before registering
    # its observed runtime digest; this avoids trusting a mutable source tag.
    launch_env=(
        "AUTO_START_INFRA=0"
        "OPENVIKING_LAUNCH_ACTION=launch"
        "OPENVIKING_START_BROKER=0"
        "TC_API_URL=$tc_api_url"
        "WORKLOAD_ID=openviking-cmem"
        "IMAGE_NAME=$source_image"
        "IMAGE_URL=$image_url"
        "IMAGE_ID=$runtime_image_id"
        "OPENVIKING_HOST_DATA_DIR=$state_dir"
        "OPENVIKING_USE_LUKS=0"
        "OPENVIKING_DOCKER_NETWORK=$network"
        "OPENVIKING_WORKLOAD_API_DIR=$REMOTE_RUN"
        "OPENVIKING_BROKER_API_DIR=$REMOTE_BROKER_RUN"
        "OPENVIKING_AGENT_SPIFFE_ID=$agent_id"
        "OPENVIKING_MTLS_PORT=$port"
        "OPENVIKING_CONTAINER=$WORKLOAD_CONTAINER"
        "OPENVIKING_BROKER_CONTAINER=$BROKER_CONTAINER"
        "OPENVIKING_BROKER_IMAGE=$BROKER_IMAGE"
        "ATTESTATION_REQUIRED=false"
    )
    if [[ -n "$identity_token" ]]; then
        launch_env+=("TC_API_IDENTITY_TOKEN=$identity_token")
    else
        launch_env+=("TC_API_BEARER_TOKEN=$bearer_token")
    fi
    remote_sudo env "${launch_env[@]}" "$REMOTE_ROOT/bin/launch_openviking.sh"
    runtime_digest="$(remote_sudo /usr/local/bin/docker image inspect \
        "$runtime_image_id" --format '{{.Id}}')"
    container_digest="$(remote_sudo /usr/local/bin/docker inspect \
        "$WORKLOAD_CONTAINER" --format '{{.Image}}')"
    [[ "$runtime_digest" =~ ^sha256:[0-9a-f]{64}$ ]] \
        || fail "TC-API runtime image config digest is invalid: $runtime_digest"
    [[ "$container_digest" == "$runtime_digest" ]] \
        || fail "OpenViking container uses $container_digest, expected runtime digest $runtime_digest"

    # Registration now binds the live OpenViking process image to its TDVM Agent.
    DUAL_RUNTIME_DIR="$RUNTIME_DIR" \
    DUAL_OPENVIKING_RUNTIME_IMAGE_ID="$runtime_image_id" \
        "$SCRIPT_DIR/register-workloads.sh"

    # Start the Broker only after the strong target entry exists; OpenViking itself
    # never receives a Workload API socket or SVID private key.
    broker_env=(
        "AUTO_START_INFRA=0"
        "OPENVIKING_LAUNCH_ACTION=broker"
        "OPENVIKING_WORKLOAD_API_DIR=$REMOTE_RUN"
        "OPENVIKING_BROKER_API_DIR=$REMOTE_BROKER_RUN"
        "OPENVIKING_AGENT_SPIFFE_ID=$agent_id"
        "OPENVIKING_MTLS_PORT=$port"
        "OPENVIKING_CONTAINER=$WORKLOAD_CONTAINER"
        "OPENVIKING_BROKER_CONTAINER=$BROKER_CONTAINER"
        "OPENVIKING_BROKER_IMAGE=$BROKER_IMAGE"
    )
    remote_sudo env "${broker_env[@]}" "$REMOTE_ROOT/bin/launch_openviking.sh"
    printf '%s\n' \
        'Unmodified OpenViking and its Broker Sidecar are ready in the OpenViking TDVM.' \
        "Observed runtime image config digest: $runtime_digest" \
        "Broker mTLS port: $port" \
        "Accepted client identity: $OPENCLAW_ID"
}

connect_openviking() {
    local api_key target_uri
    [[ "$ROLE" == openclaw ]] \
        || fail 'connect-openviking is only valid for the openclaw role'
    api_key="${DUAL_OPENVIKING_API_KEY:-}"
    target_uri="http://$BROKER_CONTAINER:${DUAL_OPENCLAW_EGRESS_PORT:-1934}"
    [[ -n "$api_key" ]] \
        || fail 'DUAL_OPENVIKING_API_KEY must contain a non-root OpenViking user key'
    remote_sudo test -x "$REMOTE_ROOT/bin/connect_openclaw_openviking.sh" \
        || fail 'OpenViking plugin connector is missing; load the OpenClaw workload first'
    for container in "$WORKLOAD_CONTAINER" "$BROKER_CONTAINER"; do
        [[ "$(remote_sudo /usr/local/bin/docker inspect "$container" \
            --format '{{.State.Running}}' 2>/dev/null || true)" == true ]] \
            || fail "required container is not running: $container"
    done

    # Configure the plugin to use caller-local HTTP; the Egress Broker performs
    # Guard authorization and cross-TDVM SPIFFE mTLS behind that endpoint.
    remote_sudo env \
        PATH=/usr/local/bin:/usr/bin:/bin \
        "TARGET_URI=$target_uri" \
        "OPENCLAW_CONTAINER=$WORKLOAD_CONTAINER" \
        OPENCLAW_USER=node \
        OPENCLAW_CONFIG_PATH=/home/node/.openclaw/openclaw.json \
        "OPENCLAW_INSTALL_PLUGIN=${DUAL_OPENCLAW_INSTALL_PLUGIN:-1}" \
        "OPENCLAW_PLUGIN_SPEC=${DUAL_OPENCLAW_PLUGIN_SPEC:-clawhub:@openviking/openclaw-plugin}" \
        OPENCLAW_RESTART_GATEWAY=0 \
        "OPENVIKING_REQUIRE_READY=$OPENVIKING_APPLICATION_READY" \
        "OPENVIKING_API_KEY=$api_key" \
        "$REMOTE_ROOT/bin/connect_openclaw_openviking.sh"

    remote_sudo /usr/local/bin/docker restart "$WORKLOAD_CONTAINER" >/dev/null
    start_openclaw_broker
    remote_sudo /usr/local/bin/docker exec -i -u node "$WORKLOAD_CONTAINER" \
        node - /home/node/.openclaw/openclaw.json "$target_uri" <<'NODE'
const { execFileSync } = require("node:child_process");
const configPath = process.argv[2];
const target = process.argv[3].replace(/\/+$/, "");
function get(path) {
  return JSON.parse(execFileSync("openclaw", ["config", "get", path, "--json"], {
    encoding: "utf8",
    env: { ...process.env, OPENCLAW_CONFIG_PATH: configPath },
  }).trim());
}
const slot = get("plugins.slots.contextEngine");
const plugin = get("plugins.entries.openviking.config");
if (slot !== "openviking") throw new Error(`unexpected context engine ${slot}`);
if (plugin?.mode !== "remote" || plugin?.baseUrl?.replace(/\/+$/, "") !== target) {
  throw new Error(`OpenViking plugin does not target ${target}`);
}
if (typeof plugin.apiKey !== "string" || !plugin.apiKey) throw new Error("plugin API key is missing");
NODE
    remote_sudo /usr/local/bin/docker exec -u node \
        -e OPENCLAW_CONFIG_PATH=/home/node/.openclaw/openclaw.json \
        "$WORKLOAD_CONTAINER" openclaw openviking status --json
    printf '%s\n' \
        'OpenClaw OpenViking plugin is configured through the Egress Broker.' \
        "Plugin baseUrl: $target_uri" \
        'API key: configured but not printed'
}

start_workload() {
    if [[ "$ROLE" == openclaw ]]; then
        start_openclaw
    else
        start_openviking
    fi
}

status() {
    remote test -c /dev/tdx_guest || fail 'SSH target is not a TD Guest'
    containers=("$PROVIDER_CONTAINER" "$AGENT_CONTAINER" "$WORKLOAD_CONTAINER")
    [[ "$ROLE" == openclaw ]] && containers+=(argus-dual-openclaw-guard "$BROKER_CONTAINER")
    [[ "$ROLE" == openviking ]] && containers+=("$BROKER_CONTAINER" "$OPENVIKING_OLLAMA_CONTAINER")
    for container in "${containers[@]}"; do
        if remote_sudo /usr/local/bin/docker inspect "$container" >/dev/null 2>&1; then
            remote_sudo /usr/local/bin/docker inspect "$container" \
                --format 'container={{.Name}} image={{.Config.Image}} status={{.State.Status}} restart={{.HostConfig.RestartPolicy.Name}}'
        else
            printf 'container=%s status=not-created\n' "$container"
        fi
    done
}

stop() {
    containers=("$WORKLOAD_CONTAINER" "$AGENT_CONTAINER" "$PROVIDER_CONTAINER")
    [[ "$ROLE" == openclaw ]] && containers=("$BROKER_CONTAINER" argus-dual-openclaw-guard "${containers[@]}")
    [[ "$ROLE" == openviking ]] && containers=("$BROKER_CONTAINER" "$OPENVIKING_OLLAMA_CONTAINER" "${containers[@]}")
    remote_sudo /usr/local/bin/docker rm -f "${containers[@]}" >/dev/null 2>&1 || true
    printf 'Stopped %s dual-TDVM containers; persistent data and Docker volumes were retained.\n' "$ROLE"
}

case "$ACTION" in
    deploy-agent) deploy_agent ;;
    load-workload) load_workload ;;
    start-workload) start_workload ;;
    connect-openviking) connect_openviking ;;
    status) status ;;
    stop) stop ;;
    *) fail 'action must be deploy-agent, load-workload, start-workload, connect-openviking, status, or stop' ;;
esac
