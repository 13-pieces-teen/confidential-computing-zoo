#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TDVM_TOOLS="$SCRIPT_DIR/../../../core/spire/tests/tdvm"
export TDVM_PROFILE=openclaw
if [[ "${1:-status}" == preflight ]]; then
    export TDVF_FIRMWARE="${TDVM_FIRMWARE:-/usr/share/edk2/ovmf/OVMF.inteltdx.fd}"
    exec bash "$TDVM_TOOLS/check-tdx-host.sh" boot
fi
exec bash "$TDVM_TOOLS/tdvm.sh" "${1:-status}"
