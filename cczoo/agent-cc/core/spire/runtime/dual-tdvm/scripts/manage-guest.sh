#!/usr/bin/env bash
set -euo pipefail

ROLE="${1:-}"
ACTION="${2:-status}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNTIME_DIR="${DUAL_RUNTIME_DIR:-$PROFILE_DIR/runtime}"
SPIRE_AGENT_IMAGE="${DUAL_SPIRE_AGENT_IMAGE:-ghcr.io/spiffe/spire-agent:1.15.1}"
PROVIDER_IMAGE="${DUAL_PROVIDER_IMAGE:-argus-spire-dual-mock-evidence-provider:local}"
REMOTE_ROOT="${DUAL_GUEST_ROOT:-/opt/argus-spire-dual}"
OPENCLAW_ID="spiffe://argus.local/agent/openclaw"
OPENVIKING_ID="spiffe://argus.local/service/openviking-cmem"

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
        AGENT_CONFIG="openviking-agent.conf"
        PROVIDER_CONTAINER="argus-dual-openviking-evidence"
        AGENT_CONTAINER="argus-dual-openviking-agent"
        WORKLOAD_CONTAINER="${DUAL_OPENVIKING_CONTAINER:-agentcc-openviking-service}"
        WORKLOAD_IMAGE="${DUAL_OPENVIKING_WORKLOAD_IMAGE:-argus-dual-openviking:v0.4.8}"
        ;;
    *) fail 'role must be openclaw or openviking' ;;
esac

[[ -n "$SSH_TARGET" ]] \
    || fail "set DUAL_${ROLE^^}_TDVM_SSH_TARGET to the TDVM SSH destination"
[[ "$RUNTIME_DIR" == /* ]] \
    || fail "DUAL_RUNTIME_DIR must be an absolute host path: $RUNTIME_DIR"
[[ "$REMOTE_ROOT" == /* && "$REMOTE_DATA" == /* && "$REMOTE_RUN" == /* ]] \
    || fail 'Guest root, data, and run paths must be absolute'

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
    remote_sudo install -d -m 0700 "$REMOTE_DATA" "$REMOTE_DATA/argus-tdx"
    tar -C "$RUNTIME_DIR" -cpf - \
        "conf/$AGENT_CONFIG" \
        certs/upstream-ca.pem \
        plugins/argus-tdx-nodeattestor-agent \
        | remote_sudo tar -C "$REMOTE_ROOT" -xpf -
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
        "-replay-evidence=${DUAL_REPLAY_EVIDENCE:-false}" \
        "-evidence-status=${DUAL_EVIDENCE_STATUS:-0}" \
        "-evidence-delay=${DUAL_EVIDENCE_DELAY:-0s}" >/dev/null
    wait_for_provider

    remote_sudo /usr/local/bin/docker rm -f "$AGENT_CONTAINER" >/dev/null 2>&1 || true
    remote_sudo /usr/local/bin/docker run -d \
        --name "$AGENT_CONTAINER" \
        --network host \
        --pid host \
        --restart unless-stopped \
        -v "$REMOTE_ROOT/conf/$AGENT_CONFIG:/opt/spire/conf/$AGENT_CONFIG:ro" \
        -v "$REMOTE_ROOT/certs/upstream-ca.pem:/opt/spire/conf/certs/upstream-ca.pem:ro" \
        -v "$REMOTE_ROOT/plugins/argus-tdx-nodeattestor-agent:/opt/spire/plugins/argus-tdx-nodeattestor-agent:ro" \
        -v "$REMOTE_DATA:/opt/spire/data/$ROLE-agent" \
        -v "$REMOTE_RUN:/opt/spire/run/$ROLE" \
        -v /var/run/docker.sock:/var/run/docker.sock:ro \
        "$SPIRE_AGENT_IMAGE" \
        -config "/opt/spire/conf/$AGENT_CONFIG" >/dev/null
    wait_for_agent

    printf '%s\n' \
        "$ROLE TDVM SPIRE Agent is healthy." \
        "TDVM instance ID: $INSTANCE_ID" \
        "Workload API directory: $REMOTE_RUN" \
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
        local openviking_config
        openviking_config="${DUAL_OPENVIKING_CONFIG:-}"
        [[ -n "$openviking_config" && "$openviking_config" == /* && -s "$openviking_config" ]] \
            || fail 'DUAL_OPENVIKING_CONFIG must name an absolute, non-empty ov.conf file'
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
    local port state_dir network
    port="${DUAL_OPENVIKING_PORT:-1943}"
    network="${DUAL_OPENVIKING_NETWORK:-argus-dual-openviking}"
    state_dir="/var/lib/argus-spire-dual/openviking-state"
    remote_sudo test -S "$REMOTE_RUN/agent.sock" \
        || fail 'OpenViking Workload API socket is missing; deploy its Agent first'
    remote_sudo test -s "$state_dir/ov.conf" \
        || fail 'OpenViking ov.conf is missing; load its workload first'
    remote_sudo /usr/local/bin/docker image inspect "$WORKLOAD_IMAGE" >/dev/null \
        || fail "image is not loaded in OpenViking TDVM: $WORKLOAD_IMAGE"

    remote_sudo /usr/local/bin/docker rm -f "$WORKLOAD_CONTAINER" >/dev/null 2>&1 || true
    # The TDVM's static dockerd runs with --bridge=none, so the default bridge
    # network does not exist and a bare -p publish silently allocates no ports.
    # Put the workload on a dedicated user bridge (the same pattern the
    # OpenClaw workload uses) so docker-proxy can publish the mTLS port.
    remote_sudo /usr/local/bin/docker network inspect "$network" >/dev/null 2>&1 \
        || remote_sudo /usr/local/bin/docker network create "$network" >/dev/null
    remote_sudo /usr/local/bin/docker run -d \
        --name "$WORKLOAD_CONTAINER" \
        --restart unless-stopped \
        --label argus.workload=openviking-cmem \
        --network "$network" \
        -p "0.0.0.0:$port:1943" \
        -v "$REMOTE_RUN:/opt/spire/run/openviking:ro" \
        -v "$state_dir:/app/.openviking" \
        -e OPENVIKING_CONFIG_FILE=/app/.openviking/ov.conf \
        -e OPENVIKING_WITH_BOT=0 \
        -e ARGUS_SPIFFE_ENABLED=1 \
        -e SPIFFE_ENDPOINT_SOCKET=unix:///opt/spire/run/openviking/agent.sock \
        -e ARGUS_WORKLOAD_SPIFFE_ID="$OPENVIKING_ID" \
        -e ARGUS_EXPECTED_CLIENT_SPIFFE_ID="$OPENCLAW_ID" \
        -e ARGUS_OPENVIKING_MTLS_PORT=1943 \
        "$WORKLOAD_IMAGE" >/dev/null
    wait_for_svid "$OPENVIKING_ID"
    printf '%s\n' \
        'OpenViking SPIFFE mTLS service is ready in the OpenViking TDVM.' \
        "Published TDVM port: $port" \
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
