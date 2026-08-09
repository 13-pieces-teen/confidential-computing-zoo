#!/usr/bin/env bash
set -euo pipefail

TDX_DEVICE="${TDX_GUEST_DEVICE:-/dev/tdx_guest}"
TSM_REPORT_ROOT="${TRUCON_TSM_REPORT_ROOT:-/sys/kernel/config/tsm/report}"

fail() {
    printf 'TDX Guest preflight: FAIL: %s\n' "$1" >&2
    exit 1
}

[[ -c "$TDX_DEVICE" ]] || fail "$TDX_DEVICE is not a character device; run this check inside a TD VM, not on the TDX Host"
[[ -d /sys/module/tdx_guest ]] || fail "the tdx_guest kernel module is not loaded inside the TD VM"
[[ -d "$TSM_REPORT_ROOT" ]] || fail "TSM configfs report root is unavailable at $TSM_REPORT_ROOT"
[[ -w "$TSM_REPORT_ROOT" ]] || fail "TSM configfs report root is not writable at $TSM_REPORT_ROOT"

printf 'TDX Guest preflight: PASS\n'
printf 'Guest device: %s\n' "$TDX_DEVICE"
printf 'Guest module: /sys/module/tdx_guest\n'
printf 'TSM report root: %s\n' "$TSM_REPORT_ROOT"
