#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-deploy}"
SOURCE_CONTAINER="${OPENVIKING_SOURCE_CONTAINER:-agentcc-openviking-service}"
SOURCE_STATE_DIR="${OPENVIKING_SOURCE_STATE_DIR:-/app/.openviking}"
GUEST_CONTAINER="${OPENVIKING_GUEST_CONTAINER:-agentcc-openviking-tdx}"
GUEST_STATE_DIR="${OPENVIKING_GUEST_STATE_DIR:-/var/lib/agentcc/openviking-real}"
OPENVIKING_IMAGE="${OPENVIKING_IMAGE:-}"
DOCKER_RUNTIME_ARCHIVE="${DOCKER_RUNTIME_ARCHIVE:-}"
TDVM_SSH_TARGET="${TDVM_SSH_TARGET:-tdx@127.0.0.1}"
TDVM_SSH_PORT="${TDVM_SSH_PORT:-2222}"
TDVM_SSH_IDENTITY="${TDVM_SSH_IDENTITY:-}"
TDVM_KNOWN_HOSTS="${TDVM_KNOWN_HOSTS:-/tmp/argus-openviking-tdx-known-hosts}"
DEPLOY_ID="$(date -u +%Y%m%dT%H%M%SZ)"
STAGING_DIR="${GUEST_STATE_DIR}.staging-${DEPLOY_ID}"
BACKUP_DIR="${GUEST_STATE_DIR}.backup-${DEPLOY_ID}"
SOURCE_PAUSED=0

resume_source() {
    if [[ "$SOURCE_PAUSED" == "1" ]]; then
        docker unpause "$SOURCE_CONTAINER" >/dev/null 2>&1 || true
        SOURCE_PAUSED=0
    fi
}
trap resume_source EXIT

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
    printf 'OpenViking TD VM %s: FAIL: %s\n' "$ACTION" "$1" >&2
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

resolve_image() {
    if [[ -z "$OPENVIKING_IMAGE" ]]; then
        OPENVIKING_IMAGE="$(docker inspect "$SOURCE_CONTAINER" --format '{{.Config.Image}}')"
    fi
    docker image inspect "$OPENVIKING_IMAGE" >/dev/null
}

install_docker_if_needed() {
    local binary runtime_complete=1
    local required_binaries=(
        docker
        dockerd
        containerd
        containerd-shim-runc-v2
        ctr
        runc
        docker-proxy
    )

    for binary in "${required_binaries[@]}"; do
        if ! remote_sudo test -x "/usr/local/bin/$binary"; then
            runtime_complete=0
            break
        fi
    done
    if [[ "$runtime_complete" == "1" ]] \
        && remote_sudo test -f /etc/systemd/system/docker-offline.service \
        && remote_sudo systemctl is-active --quiet docker-offline.service; then
        return
    fi

    if [[ "$runtime_complete" == "0" ]]; then
        [[ -n "$DOCKER_RUNTIME_ARCHIVE" ]] || fail 'DOCKER_RUNTIME_ARCHIVE is required when the Guest Docker runtime is incomplete'
        [[ -r "$DOCKER_RUNTIME_ARCHIVE" ]] || fail "Docker runtime archive is unreadable: $DOCKER_RUNTIME_ARCHIVE"
        case "$DOCKER_RUNTIME_ARCHIVE" in
            *.tgz|*.tar.gz) ;;
            *) fail 'DOCKER_RUNTIME_ARCHIVE must be the official static Docker .tgz archive' ;;
        esac
        for binary in "${required_binaries[@]}"; do
            tar -tzf "$DOCKER_RUNTIME_ARCHIVE" "docker/$binary" >/dev/null \
                || fail "Docker runtime archive is missing docker/$binary"
        done

        gzip -dc "$DOCKER_RUNTIME_ARCHIVE" \
            | remote_sudo tar -x -C /usr/local/bin --strip-components=1 \
                "${required_binaries[@]/#/docker/}"
    fi
    remote_sudo tee /etc/systemd/system/docker-offline.service >/dev/null <<'UNIT'
[Unit]
Description=Offline Docker Engine for OpenViking TD VM
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
ExecStart=/usr/local/bin/dockerd --host=unix:///run/docker.sock --bridge=none --iptables=false --ip-forward=false --ip-masq=false --storage-driver=overlay2 --userland-proxy-path=/usr/local/bin/docker-proxy
ExecReload=/bin/kill -s HUP $MAINPID
Restart=on-failure
RestartSec=2
Delegate=yes
KillMode=process

[Install]
WantedBy=multi-user.target
UNIT
    remote_sudo systemctl daemon-reload
    remote_sudo systemctl enable --now docker-offline.service
}

wait_for_health() {
    for _ in $(seq 1 90); do
        if remote curl -fsS --max-time 3 http://127.0.0.1:1933/health >/dev/null 2>&1 \
            && remote curl -fsS --max-time 3 http://127.0.0.1:1933/ready >/dev/null 2>&1; then
            printf 'OpenViking ready inside TD VM: http://127.0.0.1:1933\n'
            return
        fi
        read -r -t 1 _ || true
    done
    remote_sudo /usr/local/bin/docker logs --tail 80 "$GUEST_CONTAINER" >&2 || true
    fail 'OpenViking health/readiness did not become ready'
}

run_guest_container() {
    remote_sudo /usr/local/bin/docker rm -f "$GUEST_CONTAINER" >/dev/null 2>&1 || true
    remote_sudo /usr/local/bin/docker run -d \
        --name "$GUEST_CONTAINER" \
        --network host \
        --restart unless-stopped \
        -v "$GUEST_STATE_DIR:/app/.openviking" \
        "$OPENVIKING_IMAGE" >/dev/null
    wait_for_health
}

deploy() {
    command -v docker >/dev/null 2>&1 || fail 'Docker is required on the Host'
    resolve_image
    docker inspect "$SOURCE_CONTAINER" >/dev/null
    docker exec "$SOURCE_CONTAINER" test -f "$SOURCE_STATE_DIR/ov.conf" \
        || fail 'source state does not contain ov.conf'
    remote true
    remote test -c /dev/tdx_guest || fail 'SSH target is not a TD Guest'
    install_docker_if_needed

    printf 'Streaming OpenViking image %s to TD VM\n' "$OPENVIKING_IMAGE"
    docker save "$OPENVIKING_IMAGE" | remote_sudo /usr/local/bin/docker load >/dev/null

    remote_sudo mkdir -p "$STAGING_DIR"
    docker pause "$SOURCE_CONTAINER" >/dev/null
    SOURCE_PAUSED=1
    if docker run --rm --volumes-from "$SOURCE_CONTAINER:ro" \
        --entrypoint tar "$OPENVIKING_IMAGE" \
        --numeric-owner -C "$SOURCE_STATE_DIR" -cpf - . \
        | remote_sudo tar --numeric-owner -C "$STAGING_DIR" -xpf -; then
        snapshot_transferred=1
    else
        snapshot_transferred=0
    fi
    docker unpause "$SOURCE_CONTAINER" >/dev/null
    SOURCE_PAUSED=0
    [[ "$snapshot_transferred" == "1" ]] \
        || fail "state snapshot transfer failed; staging retained at $STAGING_DIR"
    remote_sudo test -s "$STAGING_DIR/ov.conf" || fail 'transferred state is missing ov.conf'
    remote_sudo rm -f "$STAGING_DIR/data/.openviking.pid"
    remote_sudo chmod 0700 "$STAGING_DIR"

    remote_sudo /usr/local/bin/docker stop "$GUEST_CONTAINER" >/dev/null 2>&1 || true
    if remote_sudo test -e "$GUEST_STATE_DIR"; then
        remote_sudo mv "$GUEST_STATE_DIR" "$BACKUP_DIR"
    fi
    remote_sudo mv "$STAGING_DIR" "$GUEST_STATE_DIR"
    run_guest_container
    printf 'OpenViking deployed; previous Guest state backup: %s\n' "$BACKUP_DIR"
}

rollback() {
    remote_sudo test -x /usr/local/bin/docker || fail 'Docker is not installed in the Guest'
    latest_backup="$(remote_sudo sh -c 'ls -1dt "$1".backup-* 2>/dev/null | head -n 1' sh "$GUEST_STATE_DIR")"
    [[ -n "$latest_backup" ]] || fail 'no Guest state backup is available'
    if [[ -z "$OPENVIKING_IMAGE" ]]; then
        OPENVIKING_IMAGE="$(remote_sudo /usr/local/bin/docker inspect "$GUEST_CONTAINER" --format '{{.Config.Image}}')"
    fi
    remote_sudo /usr/local/bin/docker stop "$GUEST_CONTAINER" >/dev/null 2>&1 || true
    failed_dir="${GUEST_STATE_DIR}.replaced-${DEPLOY_ID}"
    if remote_sudo test -e "$GUEST_STATE_DIR"; then
        remote_sudo mv "$GUEST_STATE_DIR" "$failed_dir"
    fi
    remote_sudo mv "$latest_backup" "$GUEST_STATE_DIR"
    remote_sudo rm -f "$GUEST_STATE_DIR/data/.openviking.pid"
    run_guest_container
    printf 'OpenViking Guest state rolled back from %s; replaced state retained at %s\n' \
        "$latest_backup" "$failed_dir"
}

status() {
    remote test -c /dev/tdx_guest || fail 'SSH target is not a TD Guest'
    remote_sudo systemctl is-active docker-offline.service
    remote_sudo /usr/local/bin/docker inspect "$GUEST_CONTAINER" \
        --format 'container={{.Name}} image={{.Config.Image}} status={{.State.Status}} restart={{.HostConfig.RestartPolicy.Name}}'
    remote_sudo test -s "$GUEST_STATE_DIR/ov.conf" || fail 'Guest state is missing ov.conf'
    remote curl -fsS --max-time 5 http://127.0.0.1:1933/health >/dev/null
    remote curl -fsS --max-time 5 http://127.0.0.1:1933/ready >/dev/null
    printf 'OpenViking TD VM deployment healthy\n'
}

case "$ACTION" in
    deploy) deploy ;;
    rollback) rollback ;;
    status) status ;;
    *) fail 'action must be deploy, rollback, or status' ;;
esac
