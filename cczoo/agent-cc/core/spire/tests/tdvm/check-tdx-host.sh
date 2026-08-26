#!/usr/bin/env bash
set -euo pipefail

QEMU_BINARY="${QEMU_BINARY:-qemu-system-x86_64}"
TDVF_FIRMWARE="${TDVF_FIRMWARE:-/usr/share/edk2/ovmf/OVMF.inteltdx.fd}"
TDX_QGS_SOCKET="${TDX_QGS_SOCKET:-/var/run/tdx-qgs/qgs.socket}"

fail() {
    printf 'TDX Host preflight: FAIL: %s\n' "$1" >&2
    exit 1
}

# A usable host needs KVM/TDVF to launch TDVMs and QGS to service Quote requests.
[[ -c /dev/kvm ]] || fail "/dev/kvm is not a character device"
[[ -r /sys/module/kvm_intel/parameters/tdx ]] || fail "kvm_intel does not expose the TDX enablement parameter"
[[ "$(cat /sys/module/kvm_intel/parameters/tdx)" == "Y" ]] || fail "KVM TDX support is not enabled"
command -v "$QEMU_BINARY" >/dev/null 2>&1 || fail "$QEMU_BINARY is unavailable"
"$QEMU_BINARY" -object help 2>&1 | grep -q '^  tdx-guest$' || fail "QEMU does not expose the tdx-guest object"
[[ -r "$TDVF_FIRMWARE" ]] || fail "TDVF firmware is unavailable at $TDVF_FIRMWARE"
[[ -S "$TDX_QGS_SOCKET" ]] || fail "QGS socket is unavailable at $TDX_QGS_SOCKET; install/start Host QGS before requesting a TD Quote"

printf 'TDX Host preflight: PASS\n'
printf 'KVM TDX: enabled\n'
printf 'QEMU: %s\n' "$(command -v "$QEMU_BINARY")"
printf 'TDVF: %s\n' "$TDVF_FIRMWARE"
printf 'QGS socket: %s\n' "$TDX_QGS_SOCKET"
