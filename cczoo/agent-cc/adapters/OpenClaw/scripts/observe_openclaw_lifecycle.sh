#!/usr/bin/env bash
set -euo pipefail
umask 077
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/openclaw_spiffe_common.sh"
spiffe_preflight >&2
resolve_spiffe_plugin
[[ -n "${1:-}" && -n "${2:-}" && -n "${3:-}" ]] || { echo 'usage: observe_openclaw_lifecycle.sh GET_PATH SECONDS EXPECTED_MARKER' >&2; exit 1; }
oc node "$OPENCLAW_PLUGIN_DIR/dist/argus-spiffe/cli.mjs" watch "$1" "$2" "$3"
