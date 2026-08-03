#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-status}"
TDVM_NAME="${TDVM_NAME:-argus-openviking-tdx}"
TDVM_BASE_IMAGE="${TDVM_BASE_IMAGE:-}"
TDVM_OVERLAY_IMAGE="${TDVM_OVERLAY_IMAGE:-}"
TDVM_FIRMWARE="${TDVM_FIRMWARE:-/usr/share/edk2/ovmf/OVMF.inteltdx.fd}"
TDVM_SSH_PUBLIC_KEY="${TDVM_SSH_PUBLIC_KEY:-$HOME/.ssh/id_rsa.pub}"
TDVM_GUEST_USER="${TDVM_GUEST_USER:-tdx}"
TDVM_GUEST_UID="${TDVM_GUEST_UID:-1000}"
TDVM_GUEST_GID="${TDVM_GUEST_GID:-1000}"
TDVM_CPUS="${TDVM_CPUS:-8}"
TDVM_MEMORY="${TDVM_MEMORY:-8G}"
TDVM_SSH_PORT="${TDVM_SSH_PORT:-2222}"
TDVM_OPENVIKING_PORT="${TDVM_OPENVIKING_PORT:-2933}"
TDVM_RUNTIME_DIR="${TDVM_RUNTIME_DIR:-/tmp/argus-spiffe-m4-$UID/$TDVM_NAME}"
PID_FILE="$TDVM_RUNTIME_DIR/qemu.pid"
CONSOLE_LOG="$TDVM_RUNTIME_DIR/console.log"

fail() {
    printf 'TD VM %s: FAIL: %s\n' "$ACTION" "$1" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"
}

running_pid() {
    local pid command_line
    if [[ -s "$PID_FILE" ]]; then
        pid="$(cat "$PID_FILE")"
        if command_line="$(ps -p "$pid" -o args= 2>/dev/null)" \
            && [[ "$command_line" == *qemu-system-x86_64* ]] \
            && [[ "$command_line" == *"-name $TDVM_NAME"* ]]; then
            printf '%s\n' "$pid"
            return
        fi
    fi
    ps -eo pid=,args= | awk -v name="" '
        index(-e, "qemu-system-x86_64") && index(-e, "-name " name) {
            print            found = 1
            exit
        }
        END { exit(found ? 0 : 1) }
    '
}

require_images() {
    [[ -n "$TDVM_BASE_IMAGE" ]] || fail 'TDVM_BASE_IMAGE is required'
    [[ -n "$TDVM_OVERLAY_IMAGE" ]] || fail 'TDVM_OVERLAY_IMAGE is required'
    [[ -r "$TDVM_BASE_IMAGE" ]] || fail "base image is unreadable: $TDVM_BASE_IMAGE"
    [[ -r "$TDVM_FIRMWARE" ]] || fail "TDVF firmware is unreadable: $TDVM_FIRMWARE"
}

prepare() {
    require_command qemu-img
    require_command guestfish
    require_images
    [[ -r "$TDVM_SSH_PUBLIC_KEY" ]] || fail "SSH public key is unreadable: $TDVM_SSH_PUBLIC_KEY"
    mkdir -p "$(dirname "$TDVM_OVERLAY_IMAGE")"
    if [[ ! -e "$TDVM_OVERLAY_IMAGE" ]]; then
        qemu-img create -f qcow2 -F qcow2 -b "$TDVM_BASE_IMAGE" "$TDVM_OVERLAY_IMAGE"
        guestfish --rw -a "$TDVM_OVERLAY_IMAGE" -i <<GUESTFISH
mkdir-p /home/$TDVM_GUEST_USER/.ssh
upload $TDVM_SSH_PUBLIC_KEY /home/$TDVM_GUEST_USER/.ssh/authorized_keys
chmod 0700 /home/$TDVM_GUEST_USER/.ssh
chmod 0600 /home/$TDVM_GUEST_USER/.ssh/authorized_keys
chown $TDVM_GUEST_UID $TDVM_GUEST_GID /home/$TDVM_GUEST_USER/.ssh
chown $TDVM_GUEST_UID $TDVM_GUEST_GID /home/$TDVM_GUEST_USER/.ssh/authorized_keys
GUESTFISH
    fi
    qemu-img check "$TDVM_OVERLAY_IMAGE"
    printf 'TD VM overlay ready: %s\n' "$TDVM_OVERLAY_IMAGE"
}

start() {
    require_command qemu-system-x86_64
    mkdir -p "$TDVM_RUNTIME_DIR"
    if pid="$(running_pid)"; then
        printf '%s\n' "$pid" >"$PID_FILE"
        printf 'TD VM already running: pid=%s\n' "$pid"
        return
    fi
    prepare
    rm -f "$PID_FILE"
    for port in "$TDVM_SSH_PORT" "$TDVM_OPENVIKING_PORT"; do
        if ss -ltn "sport = :$port" | grep -q LISTEN; then
            fail "host loopback port is already in use: $port"
        fi
    done

    qemu-system-x86_64 \
        -name "$TDVM_NAME" \
        -enable-kvm -cpu host -smp "$TDVM_CPUS" -m "$TDVM_MEMORY" \
        -object tdx-guest,id=tdx0 \
        -machine q35,confidential-guest-support=tdx0,kernel_irqchip=split,smm=off \
        -bios "$TDVM_FIRMWARE" \
        -drive "file=$TDVM_OVERLAY_IMAGE,if=virtio,format=qcow2" \
        -netdev "user,id=net0,hostfwd=tcp:127.0.0.1:$TDVM_SSH_PORT-:22,hostfwd=tcp:127.0.0.1:$TDVM_OPENVIKING_PORT-:1933" \
        -device virtio-net-pci,netdev=net0 \
        -display none -serial "file:$CONSOLE_LOG" -monitor none \
        -daemonize -pidfile "$PID_FILE" -no-reboot

    for _ in $(seq 1 60); do
        pid="$(running_pid)" || fail "QEMU exited; inspect $CONSOLE_LOG"
        if ss -ltn "sport = :$TDVM_SSH_PORT" | grep -q LISTEN; then
            printf 'TD VM started: pid=%s ssh=127.0.0.1:%s openviking=127.0.0.1:%s\n' \
                "$pid" "$TDVM_SSH_PORT" "$TDVM_OPENVIKING_PORT"
            return
        fi
        read -r -t 1 _ || true
    done
    fail "SSH forwarding did not become ready; inspect $CONSOLE_LOG"
}

status() {
    if ! pid="$(running_pid)"; then
        printf 'TD VM stopped\n'
        return 1
    fi
    printf 'TD VM running: pid=%s overlay=%s\n' "$pid" "${TDVM_OVERLAY_IMAGE:-unknown}"
    ss -ltn | grep -E ":($TDVM_SSH_PORT|$TDVM_OPENVIKING_PORT)[[:space:]]" || true
}

stop() {
    if ! pid="$(running_pid)"; then
        printf 'TD VM already stopped\n'
        return
    fi
    kill "$pid"
    for _ in $(seq 1 30); do
        if ! kill -0 "$pid" 2>/dev/null; then
            rm -f "$PID_FILE"
            printf 'TD VM stopped: pid=%s\n' "$pid"
            return
        fi
        read -r -t 1 _ || true
    done
    fail "QEMU did not stop after SIGTERM; pid=$pid"
}

case "$ACTION" in
    prepare) prepare ;;
    start) start ;;
    status) status ;;
    stop) stop ;;
    *) fail 'action must be prepare, start, status, or stop' ;;
esac
