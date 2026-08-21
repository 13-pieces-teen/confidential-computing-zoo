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
OPENVIKING_ID="spiffe://argus.local/service/openviking-cmem"
OPENVIKING_APPLICATION_READY="${DUAL_OPENVIKING_APPLICATION_READY:-0}"
OPENVIKING_OLLAMA_CONTAINER="${DUAL_OPENVIKING_OLLAMA_CONTAINER:-argus-dual-openviking-ollama}"
OPENVIKING_OLLAMA_IMAGE="${DUAL_OPENVIKING_OLLAMA_IMAGE:-}"
OPENVIKING_OLLAMA_MODEL="${DUAL_OPENVIKING_OLLAMA_MODEL:-bge-m3}"
OPENVIKING_OLLAMA_VOLUME="${DUAL_OPENVIKING_OLLAMA_VOLUME:-argus-dual-openviking-ollama-data}"
OPENVIKING_OLLAMA_API_BASE="${DUAL_OPENVIKING_OLLAMA_API_BASE:-http://${OPENVIKING_OLLAMA_CONTAINER}:11434/v1}"

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
        REMOTE_BROKER_RUN=""
        AGENT_CONFIG="openclaw-agent.conf"
        PROVIDER_CONTAINER="argus-dual-openclaw-evidence"
        AGENT_CONTAINER="argus-dual-openclaw-agent"
        WORKLOAD_CONTAINER="${DUAL_OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
        WORKLOAD_IMAGE="${DUAL_OPENCLAW_WORKLOAD_IMAGE:-argus-dual-openclaw:local}"
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

[[ -n "$SSH_TARGET" ]] \
    || fail "set DUAL_${ROLE^^}_TDVM_SSH_TARGET to the TDVM SSH destination"
[[ "$RUNTIME_DIR" == /* ]] \
    || fail "DUAL_RUNTIME_DIR must be an absolute host path: $RUNTIME_DIR"
[[ "$REMOTE_ROOT" == /* && "$REMOTE_DATA" == /* && "$REMOTE_RUN" == /* ]] \
    || fail 'Guest root, data, and run paths must be absolute'
if [[ "$ROLE" == openviking ]]; then
    [[ "$REMOTE_BROKER_RUN" == /* ]] \
        || fail 'OpenViking Broker run path must be absolute'
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

wait_for_svid() {
    local expected_id="$1"
    # The OpenClaw gateway materializes its first SVID only after its own
    # startup sequence completes (config/sandbox init), which can take a
    # couple of minutes on a cold TDVM. Give the workload API plenty of
    # time before declaring failure.
    for _ in $(seq 1 180); do
        if remote_sudo /usr/local/bin/docker exec "$WORKLOAD_CONTAINER" \
            grep -q "\"spiffe_id\": \"$expected_id\"" \
            /run/argus-svid/status.json >/dev/null 2>&1; then
            return
        fi
        read -r -t 1 _ || true
    done
    remote_sudo /usr/local/bin/docker logs --tail 120 "$WORKLOAD_CONTAINER" >&2 || true
    fail "workload did not receive $expected_id within 180s"
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
    if [[ "$ROLE" == openviking ]]; then
        remote_sudo install -d -m 2770 "$REMOTE_BROKER_RUN"
    fi
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
    if [[ "$ROLE" == openviking ]]; then
        remote_sudo chown -R 1000:1000 "$REMOTE_BROKER_RUN"
        remote_sudo chmod 2770 "$REMOTE_BROKER_RUN"
    fi

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
    if [[ "$ROLE" == openviking ]]; then
        agent_mounts+=(
            -v "$REMOTE_BROKER_RUN:/opt/spire/run/openviking-broker"
        )
    fi
    remote_sudo /usr/local/bin/docker run -d \
        --name "$AGENT_CONTAINER" \
        --network host \
        --pid host \
        --restart unless-stopped \
        "${agent_mounts[@]}" \
        "$SPIRE_AGENT_IMAGE" \
        -config "/opt/spire/conf/$AGENT_CONFIG" >/dev/null
    wait_for_agent

    if [[ "$ROLE" == openviking ]]; then
        broker_socket_stat="$(remote_sudo stat -c '%u:%g %a' "$REMOTE_BROKER_RUN/broker.sock")"
        [[ "$broker_socket_stat" == '0:1000 770' ]] \
            || fail "Broker API socket permissions are $broker_socket_stat, expected 0:1000 770"
    fi

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
    stream_image "$WORKLOAD_IMAGE"

    if [[ "$ROLE" == openclaw ]]; then
        local guard_image sandbox_image
        guard_image="${DUAL_GUARD_IMAGE:-argus-spire-dual-guard:local}"
        sandbox_image="${DUAL_OPENCLAW_SANDBOX_IMAGE:-openclaw-sandbox:bookworm-slim}"
        require_local_image "$guard_image"
        require_local_image "$sandbox_image"
        stream_image "$guard_image"
        stream_image "$sandbox_image"
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

    [[ "$OPENVIKING_APPLICATION_READY" == 1 ]] || return 0
    remote_sudo /usr/local/bin/docker image inspect "$OPENVIKING_OLLAMA_IMAGE" >/dev/null \
        || fail "Ollama image is not loaded in OpenViking TDVM: $OPENVIKING_OLLAMA_IMAGE"
    remote_sudo /usr/local/bin/docker rm -f "$OPENVIKING_OLLAMA_CONTAINER" >/dev/null 2>&1 || true
    remote_sudo /usr/local/bin/docker volume create "$OPENVIKING_OLLAMA_VOLUME" >/dev/null
    remote_sudo /usr/local/bin/docker run -d \
        --name "$OPENVIKING_OLLAMA_CONTAINER" \
        --network "$network" \
        --restart unless-stopped \
        --env OLLAMA_HOST=0.0.0.0:11434 \
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

start_openclaw() {
    local guard_image sandbox_image network origin target_address target_host gateway_port bridge_port
    guard_image="${DUAL_GUARD_IMAGE:-argus-spire-dual-guard:local}"
    sandbox_image="${DUAL_OPENCLAW_SANDBOX_IMAGE:-openclaw-sandbox:bookworm-slim}"
    network="${DUAL_OPENCLAW_NETWORK:-argus-dual-openclaw}"
    origin="${DUAL_OPENVIKING_ORIGIN:-https://openviking.argus.local:1943}"
    target_address="${DUAL_OPENVIKING_HOST_ADDRESS:-}"
    target_host="${origin#https://}"
    target_host="${target_host%%:*}"
    gateway_port="${DUAL_OPENCLAW_GATEWAY_PORT:-18789}"
    bridge_port="${DUAL_OPENCLAW_BRIDGE_PORT:-18790}"
    [[ -n "$target_address" ]] \
        || fail 'DUAL_OPENVIKING_HOST_ADDRESS must be reachable from the OpenClaw TDVM'

    remote_sudo test -S "$REMOTE_RUN/agent.sock" \
        || fail 'OpenClaw Workload API socket is missing; deploy its Agent first'
    for image in "$WORKLOAD_IMAGE" "$guard_image" "$sandbox_image"; do
        remote_sudo /usr/local/bin/docker image inspect "$image" >/dev/null \
            || fail "image is not loaded in OpenClaw TDVM: $image"
    done
    remote_sudo test -s "$REMOTE_ROOT/conf/guard-policy.yaml" \
        || fail 'Guard policy is not loaded'
    remote_sudo test -s "$REMOTE_ROOT/secrets/openclaw-gateway-token" \
        || fail 'OpenClaw gateway token is not loaded'

    remote_sudo bash -s -- \
        "$REMOTE_ROOT" "$REMOTE_RUN" "$WORKLOAD_CONTAINER" "$WORKLOAD_IMAGE" \
        "$guard_image" "$network" "$origin" "$target_address" "$target_host" \
        "$gateway_port" "$bridge_port" "$OPENCLAW_ID" "$OPENVIKING_ID" <<'REMOTE'
set -euo pipefail
remote_root="$1"
remote_run="$2"
workload_container="$3"
workload_image="$4"
guard_image="$5"
network="$6"
origin="$7"
target_address="$8"
target_host="$9"
gateway_port="${10}"
bridge_port="${11}"
openclaw_id="${12}"
openviking_id="${13}"
docker=/usr/local/bin/docker
guard_container=argus-dual-openclaw-guard

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
$docker rm -f "$workload_container" "$guard_container" >/dev/null 2>&1 || true
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
$docker run -d \
    --name "$workload_container" \
    --init \
    --restart unless-stopped \
    --network "$network" \
    --label argus.workload=openclaw \
    --add-host "$target_host:$target_address" \
    --device /dev/tdx_guest \
    -p "$gateway_port:18789" \
    -p "$bridge_port:18790" \
    -v argus-dual-openclaw-config:/home/node/.openclaw \
    -v argus-dual-openclaw-workspace:/home/node/.openclaw/workspace \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "$remote_run:/run/spire/agent:ro" \
    -v "$remote_root/secrets/openclaw-guard-api-token:/run/secrets/argus_guard_api_token:ro" \
    -e OPENCLAW_GATEWAY_TOKEN="$gateway_token" \
    -e OPENCLAW_GATEWAY_BIND=lan \
    -e ARGUS_SPIFFE_ENABLED=1 \
    -e SPIFFE_ENDPOINT_SOCKET=unix:///run/spire/agent/agent.sock \
    -e ARGUS_CALLER_SPIFFE_ID="$openclaw_id" \
    -e ARGUS_TARGET_SPIFFE_ID="$openviking_id" \
    -e ARGUS_TARGET_SERVICE=openviking-cmem \
    -e ARGUS_OPENVIKING_ORIGIN="$origin" \
    -e ARGUS_GUARD_URL=http://argus-dual-openclaw-guard:8007/guard/v1/authorize \
    -e ARGUS_GUARD_API_TOKEN_FILE=/run/secrets/argus_guard_api_token \
    -e ARGUS_GUARD_DATA_CLASS=sensitive \
    "$workload_image" >/dev/null
REMOTE

    wait_for_svid "$OPENCLAW_ID"
    remote_sudo /usr/local/bin/docker exec -i "$WORKLOAD_CONTAINER" node - <<'NODE'
const response = await fetch("http://argus-dual-openclaw-guard:8007/health", {
  signal: AbortSignal.timeout(3000),
});
if (!response.ok) throw new Error(`Guard health returned HTTP ${response.status}`);
const health = await response.json();
if (health.status !== "OK" || health.mode !== "spiffe_identity") {
  throw new Error(`unexpected Guard health: ${JSON.stringify(health)}`);
}
NODE
    printf '%s\n' \
        'OpenClaw and its caller-local Guard are ready in the OpenClaw TDVM.' \
        "OpenViking origin: $origin" \
        'Guard authorizes metadata first; allowed business requests then go directly to OpenViking over SPIFFE mTLS.'
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

    DUAL_RUNTIME_DIR="$RUNTIME_DIR" \
    DUAL_OPENVIKING_RUNTIME_IMAGE_ID="$runtime_image_id" \
        "$SCRIPT_DIR/register-workloads.sh"

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
    [[ "$ROLE" == openclaw ]] && containers+=(argus-dual-openclaw-guard)
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
    [[ "$ROLE" == openclaw ]] && containers+=(argus-dual-openclaw-guard)
    [[ "$ROLE" == openviking ]] && containers=("$BROKER_CONTAINER" "$OPENVIKING_OLLAMA_CONTAINER" "${containers[@]}")
    remote_sudo /usr/local/bin/docker rm -f "${containers[@]}" >/dev/null 2>&1 || true
    printf 'Stopped %s dual-TDVM containers; persistent data and Docker volumes were retained.\n' "$ROLE"
}

case "$ACTION" in
    deploy-agent) deploy_agent ;;
    load-workload) load_workload ;;
    start-workload) start_workload ;;
    status) status ;;
    stop) stop ;;
    *) fail 'action must be deploy-agent, load-workload, start-workload, status, or stop' ;;
esac
