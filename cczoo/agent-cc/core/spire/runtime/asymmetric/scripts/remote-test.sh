#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-all}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SPIRE_ROOT="$(cd "$PROFILE_DIR/../.." && pwd)"
AGENT_CC_DIR="$(cd "$SPIRE_ROOT/../.." && pwd)"

[[ "$ACTION" == unit || "$ACTION" == attestation || "$ACTION" == integration || "$ACTION" == all ]] \
    || { echo 'usage: remote-test.sh [unit|attestation|integration|all]' >&2; exit 1; }

run_unit() {
    echo '=== Rust Guard ==='
    cargo test --manifest-path "$AGENT_CC_DIR/core/argus/Cargo.toml"

    echo '=== SPIRE NodeAttestor plug-in ==='
    (cd "$SPIRE_ROOT/plugins/argus-tdx-nodeattestor" && go test ./...)

    echo '=== SVID materializer ==='
    (cd "$SPIRE_ROOT/components/svid-materializer" && go test ./...)

    echo '=== Optional compatibility components ==='
    (cd "$SPIRE_ROOT/components/mtls-diagnostic" && go test ./...)
    (cd "$SPIRE_ROOT/components/docker-gate" && go test ./...)

    echo '=== OpenClaw SPIFFE transport ==='
    (cd "$AGENT_CC_DIR/adapters/OpenClaw/spiffe-transport" && npm install --ignore-scripts --package-lock=false && npm test)

    echo '=== OpenViking native SPIFFE server helpers ==='
    (cd "$AGENT_CC_DIR/adapters/OpenViking" && python3 -m unittest spiffe_server.test_server)
}

run_attestation() {
    echo '=== Isolated argus_tdx Node Attestation success and rejection matrix ==='
    M3_SERVER_METRICS_PORT="${M3_SERVER_METRICS_PORT:-29988}" \
    M3_AGENT_METRICS_PORT="${M3_AGENT_METRICS_PORT:-29989}" \
        bash "$SPIRE_ROOT/tests/nodeattestor-mock/test.sh"
    M3_SERVER_METRICS_PORT="${M3_SERVER_METRICS_PORT:-29988}" \
    M3_AGENT_METRICS_PORT="${M3_AGENT_METRICS_PORT:-29989}" \
        bash "$SPIRE_ROOT/tests/tdvm/test-failures.sh"
}

run_integration() {
    bash "$SCRIPT_DIR/verify-architecture.sh"
    bash "$SCRIPT_DIR/verify-guard-gate-failures.sh"
    if [[ "${V2_RUN_BUSINESS_E2E:-1}" == "1" ]]; then
        : "${OPENVIKING_API_KEY:?OPENVIKING_API_KEY is required for business E2E}"
        bash "$SCRIPT_DIR/verify-openclaw-plugin-e2e.sh"
    fi
}

[[ "$ACTION" == unit || "$ACTION" == all ]] && run_unit
[[ "$ACTION" == attestation || "$ACTION" == all ]] && run_attestation
[[ "$ACTION" == integration || "$ACTION" == all ]] && run_integration
