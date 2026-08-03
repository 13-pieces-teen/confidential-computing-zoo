#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-status}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="${V2_RUNTIME_DIR:-$SCRIPT_DIR/runtime}"
TDVM_SSH_TARGET="${TDVM_SSH_TARGET:-tdx@127.0.0.1}"
TDVM_SSH_PORT="${TDVM_SSH_PORT:-2222}"
TDVM_SSH_IDENTITY="${TDVM_SSH_IDENTITY:-}"
TDVM_KNOWN_HOSTS="${TDVM_KNOWN_HOSTS:-/tmp/argus-openviking-v2-known-hosts}"
TDVM_INSTANCE_ID="${V2_TDVM_INSTANCE_ID:-tdvm-v2-0001}"
REMOTE_ROOT="${V2_GUEST_ROOT:-/opt/argus-spire-v2}"
REMOTE_DATA="${V2_GUEST_DATA:-/var/lib/argus-spire-v2/openviking-agent}"
REMOTE_RUN="${V2_GUEST_RUN:-/run/argus-spire-v2/openviking}"
SPIRE_AGENT_IMAGE="${V2_SPIRE_AGENT_IMAGE:-ghcr.io/spiffe/spire-agent:1.15.1}"
PROVIDER_IMAGE="${V2_PROVIDER_IMAGE:-argus-spire-v2-mock-evidence-provider:local}"
MTLS_IMAGE="${V2_MTLS_IMAGE:-argus-spire-v2-mtls:local}"
PROVIDER_CONTAINER="${V2_PROVIDER_CONTAINER:-argus-v2-mock-evidence-provider}"
AGENT_CONTAINER="${V2_OPENVIKING_AGENT_CONTAINER:-argus-v2-openviking-agent}"
MTLS_CONTAINER="${V2_OPENVIKING_MTLS_CONTAINER:-argus-v2-openviking-mtls}"

ssh_options=(
    -o BatchMode=yes
    -o ConnectTimeout=10
    -o StrictHostKeyChecking=accept-new
    -o "UserKnownHostsFile=$TDVM_KNOWN_HOSTS"
    -p "$TDVM_SSH_PORT"
)
if [[ -n "$TDVM_SSH_IDENTITY" ]]; then
    ssh_options+=(-i "$TDVM_SSH_IDENTITY")
fi

fail() {
    printf 'OpenViking v2 Guest %s: FAIL: %s\n' "$ACTION" "$1" >&2
    exit 1
}

remote() {
    local command_string="" argument quoted
    for argument in "$@"; do
        printf -v quoted '%q' "$argument"
        command_string+="${command_string:+ }$quoted"
    done
    ssh "${ssh_options[@]}" "$TDVM_SSH_TARGET" "$command_string"
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
    printf 'Streaming %s to TDVM\n' "$image"
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
            -socketPath /opt/spire/run/openviking/agent.sock >/dev/null 2>&1; then
            return
        fi
        read -r -t 1 _ || true
    done
    remote_sudo /usr/local/bin/docker logs --tail 120 "$AGENT_CONTAINER" >&2 || true
    fail 'OpenViking SPIRE Agent did not become healthy'
}

deploy_agent() {
    command -v docker >/dev/null 2>&1 || fail 'Docker is required on the host'
    [[ -s "$RUNTIME_DIR/conf/openviking-agent.conf" ]] \
        || fail "missing generated config; run $SCRIPT_DIR/prepare.sh first"
    [[ -s "$RUNTIME_DIR/certs/upstream-ca.pem" ]] \
        || fail 'missing generated SPIRE upstream CA'
    [[ -x "$RUNTIME_DIR/plugins/argus-tdx-nodeattestor-agent" ]] \
        || fail 'missing argus_tdx Agent plugin'

    for image in "$PROVIDER_IMAGE" "$MTLS_IMAGE" "$SPIRE_AGENT_IMAGE"; do
        require_local_image "$image"
    done

    remote true
    remote test -c /dev/tdx_guest || fail 'SSH target is not a TD Guest'
    remote_sudo test -x /usr/local/bin/docker \
        || fail 'Guest Docker is not installed; deploy the OpenViking TDVM runtime first'

    stream_image "$PROVIDER_IMAGE"
    stream_image "$MTLS_IMAGE"
    stream_image "$SPIRE_AGENT_IMAGE"

    remote_sudo install -d -m 0755 \
        "$REMOTE_ROOT/conf" \
        "$REMOTE_ROOT/certs" \
        "$REMOTE_ROOT/plugins" \
        "$REMOTE_RUN"
    remote_sudo install -d -m 0700 "$REMOTE_DATA" "$REMOTE_DATA/argus-tdx"

    tar -C "$RUNTIME_DIR" -cpf - \
        conf/openviking-agent.conf \
        certs/upstream-ca.pem \
        plugins/argus-tdx-nodeattestor-agent \
        | remote_sudo tar -C "$REMOTE_ROOT" -xpf -
    remote_sudo chmod 0755 \
        "$REMOTE_ROOT/plugins/argus-tdx-nodeattestor-agent"
    remote_sudo chmod 0644 \
        "$REMOTE_ROOT/conf/openviking-agent.conf" \
        "$REMOTE_ROOT/certs/upstream-ca.pem"
    remote_sudo chown -R 1000:1000 "$REMOTE_ROOT" "$REMOTE_DATA" "$REMOTE_RUN"

    remote_sudo /usr/local/bin/docker rm -f "$PROVIDER_CONTAINER" >/dev/null 2>&1 || true
    remote_sudo /usr/local/bin/docker run -d \
        --name "$PROVIDER_CONTAINER" \
        --network host \
        --restart unless-stopped \
        "$PROVIDER_IMAGE" \
        -listen=127.0.0.1:18080 \
        "-instance-id=$TDVM_INSTANCE_ID" \
        -tcb-status=up_to_date \
        -mrtd=aabb \
        -rtmr-0=0011 \
        "-replay-evidence=${V2_REPLAY_EVIDENCE:-false}" \
        "-evidence-status=${V2_EVIDENCE_STATUS:-0}" \
        "-evidence-delay=${V2_EVIDENCE_DELAY:-0s}" >/dev/null
    wait_for_provider

    remote_sudo /usr/local/bin/docker rm -f "$AGENT_CONTAINER" >/dev/null 2>&1 || true
    remote_sudo /usr/local/bin/docker run -d \
        --name "$AGENT_CONTAINER" \
        --network host \
        --pid host \
        --restart unless-stopped \
        -v "$REMOTE_ROOT/conf/openviking-agent.conf:/opt/spire/conf/openviking-agent.conf:ro" \
        -v "$REMOTE_ROOT/certs/upstream-ca.pem:/opt/spire/conf/certs/upstream-ca.pem:ro" \
        -v "$REMOTE_ROOT/plugins/argus-tdx-nodeattestor-agent:/opt/spire/plugins/argus-tdx-nodeattestor-agent:ro" \
        -v "$REMOTE_DATA:/opt/spire/data/openviking-agent" \
        -v "$REMOTE_RUN:/opt/spire/run/openviking" \
        -v /var/run/docker.sock:/var/run/docker.sock:ro \
        "$SPIRE_AGENT_IMAGE" \
        -config /opt/spire/conf/openviking-agent.conf >/dev/null
    wait_for_agent

    printf '%s\n' \
        'OpenViking v2 Agent is healthy inside the TDVM.' \
        'NodeAttestor: argus_tdx' \
        'Evidence Provider: Guest loopback mock' \
        'Trustee: independent center-side mock'
}

start_workload() {
    local mtls_image_id
    remote_sudo /usr/local/bin/docker inspect "$AGENT_CONTAINER" >/dev/null \
        || fail 'OpenViking SPIRE Agent is not deployed'
    mtls_image_id="$(
        remote_sudo /usr/local/bin/docker image inspect "$MTLS_IMAGE" \
            --format '{{.Id}}'
    )"
    [[ "$mtls_image_id" == sha256:* ]] \
        || fail "unable to resolve immutable Guest mTLS image ID from $MTLS_IMAGE"
    remote_sudo /usr/local/bin/docker rm -f "$MTLS_CONTAINER" >/dev/null 2>&1 || true
    remote_sudo /usr/local/bin/docker run -d \
        --name "$MTLS_CONTAINER" \
        --network host \
        --restart unless-stopped \
        --label argus.workload=openviking-cmem \
        -v "$REMOTE_RUN:/opt/spire/run/openviking:ro" \
        "$mtls_image_id" \
        server \
        -socket=unix:///opt/spire/run/openviking/agent.sock \
        -listen=0.0.0.0:1943 \
        -client-id=spiffe://argus.local/agent/openclaw \
        -upstream=http://127.0.0.1:1933 >/dev/null

    for _ in $(seq 1 30); do
        if remote_sudo /usr/local/bin/docker exec "$MTLS_CONTAINER" \
            /spire-mtls identity \
            -socket=unix:///opt/spire/run/openviking/agent.sock \
            -expected-id=spiffe://argus.local/service/openviking-cmem \
            >/dev/null 2>&1; then
            printf 'OpenViking SPIFFE mTLS workload is ready on TDVM port 1943.\n'
            return
        fi
        read -r -t 1 _ || true
    done
    remote_sudo /usr/local/bin/docker logs --tail 80 "$MTLS_CONTAINER" >&2 || true
    fail 'OpenViking workload did not receive its X.509-SVID'
}

status() {
    remote test -c /dev/tdx_guest || fail 'SSH target is not a TD Guest'
    for container in "$PROVIDER_CONTAINER" "$AGENT_CONTAINER" "$MTLS_CONTAINER"; do
        if remote_sudo /usr/local/bin/docker inspect "$container" >/dev/null 2>&1; then
            remote_sudo /usr/local/bin/docker inspect "$container" \
                --format 'container={{.Name}} image={{.Config.Image}} status={{.State.Status}} restart={{.HostConfig.RestartPolicy.Name}}'
        else
            printf 'container=%s status=not-created\n' "$container"
        fi
    done
    remote curl -fsS --max-time 5 http://127.0.0.1:18080/healthz
}

stop() {
    remote_sudo /usr/local/bin/docker rm -f \
        "$MTLS_CONTAINER" \
        "$AGENT_CONTAINER" \
        "$PROVIDER_CONTAINER" >/dev/null 2>&1 || true
    printf 'Stopped only the three Argus SPIFFE v2 Guest containers.\n'
}

case "$ACTION" in
    deploy-agent) deploy_agent ;;
    start-workload) start_workload ;;
    status) status ;;
    stop) stop ;;
    *) fail 'action must be deploy-agent, start-workload, status, or stop' ;;
esac
