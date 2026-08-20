#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-status}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNTIME_DIR="${V2_RUNTIME_DIR:-$PROFILE_DIR/runtime}"
TDVM_SSH_TARGET="${TDVM_SSH_TARGET:-tdx@127.0.0.1}"
TDVM_SSH_PORT="${TDVM_SSH_PORT:-2222}"
TDVM_SSH_IDENTITY="${TDVM_SSH_IDENTITY:-}"
TDVM_KNOWN_HOSTS="${TDVM_KNOWN_HOSTS:-/tmp/argus-openviking-v2-known-hosts}"
TDVM_INSTANCE_ID="${V2_TDVM_INSTANCE_ID:-tdvm-v2-0001}"
REMOTE_ROOT="${V2_GUEST_ROOT:-/opt/argus-spire-v2}"
REMOTE_DATA="${V2_GUEST_DATA:-/var/lib/argus-spire-v2/openviking-agent}"
REMOTE_RUN="${V2_GUEST_RUN:-/run/argus-spire-v2/openviking}"
REMOTE_BROKER_RUN="${V2_GUEST_BROKER_RUN:-/run/argus-spire-v2/openviking-broker}"
SPIRE_AGENT_IMAGE="${V2_SPIRE_AGENT_IMAGE:-ghcr.io/spiffe/spire-agent:1.15.2}"
PROVIDER_IMAGE="${V2_PROVIDER_IMAGE:-argus-spire-v2-mock-evidence-provider:local}"
PROVIDER_CONTAINER="${V2_PROVIDER_CONTAINER:-argus-v2-mock-evidence-provider}"
AGENT_CONTAINER="${V2_OPENVIKING_AGENT_CONTAINER:-argus-v2-openviking-agent}"
OPENVIKING_CONTAINER="${V2_REAL_OPENVIKING_CONTAINER:-agentcc-openviking-service}"
BROKER_CONTAINER="${V2_OPENVIKING_BROKER_CONTAINER:-agentcc-openviking-broker-sidecar}"

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

validate_scoped_remote_path() {
    local name="$1"
    local value="$2"
    local allowed_root="$3"
    local allow_root="$4"

    [[ "$value" == /* ]] \
        || fail "$name must be an absolute Guest path: $value"
    [[ "$value" != *'//'*
        && "$value" != *'/./'*
        && "$value" != *'/../'*
        && "$value" != */.
        && "$value" != */..
        && "$value" != */ ]] \
        || fail "$name must not contain ambiguous path components: $value"
    if [[ "$allow_root" == "1" ]]; then
        [[ "$value" == "$allowed_root" || "$value" == "$allowed_root/"* ]] \
            || fail "$name must stay under $allowed_root: $value"
        return
    fi
    [[ "$value" == "$allowed_root/"* ]] \
        || fail "$name must be a child of $allowed_root: $value"
}

validate_guest_paths() {
    validate_scoped_remote_path V2_GUEST_ROOT "$REMOTE_ROOT" /opt/argus-spire-v2 1
    validate_scoped_remote_path V2_GUEST_DATA "$REMOTE_DATA" /var/lib/argus-spire-v2 0
    validate_scoped_remote_path V2_GUEST_RUN "$REMOTE_RUN" /run/argus-spire-v2 0
    validate_scoped_remote_path V2_GUEST_BROKER_RUN "$REMOTE_BROKER_RUN" /run/argus-spire-v2 0
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
    validate_guest_paths
    [[ "$RUNTIME_DIR" == /* ]] \
        || fail "V2_RUNTIME_DIR must be an absolute host path: $RUNTIME_DIR"
    command -v docker >/dev/null 2>&1 || fail 'Docker is required on the host'
    [[ -s "$RUNTIME_DIR/conf/openviking-agent.conf" ]] \
        || fail "missing generated config; run $SCRIPT_DIR/prepare.sh first"
    [[ -s "$RUNTIME_DIR/certs/upstream-ca.pem" ]] \
        || fail 'missing generated SPIRE upstream CA'
    [[ -x "$RUNTIME_DIR/plugins/argus-tdx-nodeattestor-agent" ]] \
        || fail 'missing argus_tdx Agent plugin'
    [[ -x "$RUNTIME_DIR/plugins/argus-tdx-workloadattestor" ]] \
        || fail 'missing argus_tdx_workload plugin'
    for required_certificate in trustee-ca.pem trustee-client.pem trustee-client-key.pem; do
        [[ -s "$RUNTIME_DIR/certs/$required_certificate" ]] \
            || fail "missing generated Trustee client material: $required_certificate"
    done

    for image in "$PROVIDER_IMAGE" "$SPIRE_AGENT_IMAGE"; do
        require_local_image "$image"
    done

    remote true
    remote test -c /dev/tdx_guest || fail 'SSH target is not a TD Guest'
    remote_sudo test -x /usr/local/bin/docker \
        || fail 'Guest Docker is not installed; deploy the OpenViking TDVM runtime first'

    stream_image "$PROVIDER_IMAGE"
    stream_image "$SPIRE_AGENT_IMAGE"

    remote_sudo install -d -m 0755 \
        "$REMOTE_ROOT/conf" \
        "$REMOTE_ROOT/certs" \
        "$REMOTE_ROOT/plugins" \
        "$REMOTE_RUN" \
        "$REMOTE_BROKER_RUN"
    remote_sudo install -d -m 0700 "$REMOTE_DATA" "$REMOTE_DATA/argus-tdx"

    tar -C "$RUNTIME_DIR" -cpf - \
        conf/openviking-agent.conf \
        certs/upstream-ca.pem \
        certs/trustee-ca.pem \
        certs/trustee-client.pem \
        certs/trustee-client-key.pem \
        plugins/argus-tdx-nodeattestor-agent \
        plugins/argus-tdx-workloadattestor \
        | remote_sudo tar -C "$REMOTE_ROOT" -xpf -
    remote_sudo chmod 0755 \
        "$REMOTE_ROOT/plugins/argus-tdx-nodeattestor-agent" \
        "$REMOTE_ROOT/plugins/argus-tdx-workloadattestor"
    remote_sudo chmod 0644 \
        "$REMOTE_ROOT/conf/openviking-agent.conf" \
        "$REMOTE_ROOT/certs/upstream-ca.pem" \
        "$REMOTE_ROOT/certs/trustee-ca.pem" \
        "$REMOTE_ROOT/certs/trustee-client.pem"
    remote_sudo chmod 0600 "$REMOTE_ROOT/certs/trustee-client-key.pem"
    remote_sudo chown 1000:1000 \
        "$REMOTE_ROOT" \
        "$REMOTE_ROOT/conf" \
        "$REMOTE_ROOT/certs" \
        "$REMOTE_ROOT/plugins" \
        "$REMOTE_ROOT/conf/openviking-agent.conf" \
        "$REMOTE_ROOT/certs/upstream-ca.pem" \
        "$REMOTE_ROOT/certs/trustee-ca.pem" \
        "$REMOTE_ROOT/certs/trustee-client.pem" \
        "$REMOTE_ROOT/certs/trustee-client-key.pem" \
        "$REMOTE_ROOT/plugins/argus-tdx-nodeattestor-agent" \
        "$REMOTE_ROOT/plugins/argus-tdx-workloadattestor"
    remote_sudo chown -R 1000:1000 "$REMOTE_DATA" "$REMOTE_RUN" "$REMOTE_BROKER_RUN"

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
        -workload-id=openviking-cmem \
        -workload-policy-id=openviking-cmem-v1 \
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
        -v "$REMOTE_ROOT/certs:/opt/spire/conf/certs:ro" \
        -v "$REMOTE_ROOT/plugins:/opt/spire/plugins:ro" \
        -v "$REMOTE_DATA:/opt/spire/data/openviking-agent" \
        -v "$REMOTE_RUN:/opt/spire/run/openviking" \
        -v "$REMOTE_BROKER_RUN:/opt/spire/run/openviking-broker" \
        -v /var/run/docker.sock:/var/run/docker.sock:ro \
        "$SPIRE_AGENT_IMAGE" \
        -config /opt/spire/conf/openviking-agent.conf >/dev/null
    wait_for_agent

    printf '%s\n' \
        'OpenViking v2 Agent is healthy inside the TDVM.' \
        'NodeAttestor: argus_tdx' \
        'WorkloadAttestor: argus_tdx_workload' \
        "Workload API directory: $REMOTE_RUN" \
        "Broker API directory: $REMOTE_BROKER_RUN" \
        'Evidence Provider: Guest loopback mock' \
        'Trustee: independent center-side mock'
}

start_workload() {
    validate_guest_paths
    remote_sudo /usr/local/bin/docker inspect "$AGENT_CONTAINER" >/dev/null \
        || fail 'OpenViking SPIRE Agent is not deployed'
    remote_sudo /usr/local/bin/docker inspect "$OPENVIKING_CONTAINER" >/dev/null \
        || fail "unmodified OpenViking container is missing; run adapters/OpenViking/scripts/launch_openviking.sh in the Guest"
    remote_sudo /usr/local/bin/docker inspect "$BROKER_CONTAINER" >/dev/null \
        || fail 'OpenViking Broker Sidecar is missing; the launch script must start it after TC-API returns the container PID'

    local openviking_running broker_running openviking_socket_mount broker_workload_mount broker_api_mount target_pid broker_command
    openviking_running="$(remote_sudo /usr/local/bin/docker inspect "$OPENVIKING_CONTAINER" --format '{{.State.Running}}')"
    broker_running="$(remote_sudo /usr/local/bin/docker inspect "$BROKER_CONTAINER" --format '{{.State.Running}}')"
    [[ "$openviking_running" == true ]] || fail 'unmodified OpenViking container is not running'
    [[ "$broker_running" == true ]] || fail 'OpenViking Broker Sidecar is not running'

    openviking_socket_mount="$(remote_sudo /usr/local/bin/docker inspect "$OPENVIKING_CONTAINER" --format '{{range .Mounts}}{{if or (eq .Destination "/opt/spire/run/agent") (eq .Destination "/opt/spire/run/broker") (eq .Destination "/opt/spire/run/openviking")}}{{.Source}}{{end}}{{end}}')"
    [[ -z "$openviking_socket_mount" ]] \
        || fail 'unmodified OpenViking must not mount the Workload API or Broker API socket'
    broker_workload_mount="$(remote_sudo /usr/local/bin/docker inspect "$BROKER_CONTAINER" --format '{{range .Mounts}}{{if eq .Destination "/opt/spire/run/agent"}}{{.Source}}{{end}}{{end}}')"
    broker_api_mount="$(remote_sudo /usr/local/bin/docker inspect "$BROKER_CONTAINER" --format '{{range .Mounts}}{{if eq .Destination "/opt/spire/run/broker"}}{{.Source}}{{end}}{{end}}')"
    [[ "$broker_workload_mount" == "$REMOTE_RUN" ]] \
        || fail "Broker Workload API mount is $broker_workload_mount, expected $REMOTE_RUN"
    [[ "$broker_api_mount" == "$REMOTE_BROKER_RUN" ]] \
        || fail "Broker API mount is $broker_api_mount, expected $REMOTE_BROKER_RUN"

    target_pid="$(remote_sudo /usr/local/bin/docker inspect "$OPENVIKING_CONTAINER" --format '{{.State.Pid}}')"
    broker_command="$(remote_sudo /usr/local/bin/docker inspect "$BROKER_CONTAINER" --format '{{json .Config.Cmd}}')"
    [[ "$broker_command" == *"-target-pid=$target_pid"* ]] \
        || fail "Broker Sidecar does not reference the current OpenViking host PID $target_pid"
    remote_sudo /usr/local/bin/docker logs "$BROKER_CONTAINER" 2>&1 \
        | grep -Fq 'OpenViking mTLS listener is ready' \
        || fail 'Broker Sidecar has not received the OpenViking X.509-SVID'

    printf 'OpenViking Broker Sidecar mTLS workload is ready on TDVM port 1943 (target PID %s).\n' "$target_pid"
}

status() {
    remote test -c /dev/tdx_guest || fail 'SSH target is not a TD Guest'
    for container in "$PROVIDER_CONTAINER" "$AGENT_CONTAINER" "$OPENVIKING_CONTAINER" "$BROKER_CONTAINER"; do
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
        "$OPENVIKING_CONTAINER" \
        "$BROKER_CONTAINER" \
        "$AGENT_CONTAINER" \
        "$PROVIDER_CONTAINER" >/dev/null 2>&1 || true
    printf 'Stopped only the OpenViking asymmetric-profile workload, SPIRE Agent, and mock Evidence Provider containers; persistent state was retained.\n'
}

case "$ACTION" in
    deploy-agent) deploy_agent ;;
    start-workload) start_workload ;;
    status) status ;;
    stop) stop ;;
    *) fail 'action must be deploy-agent, start-workload, status, or stop' ;;
esac
