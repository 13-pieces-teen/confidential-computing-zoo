#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-all}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SPIRE_ROOT="$(cd "$PROFILE_DIR/../.." && pwd)"
AGENT_CC_DIR="$(cd "$SPIRE_ROOT/../.." && pwd)"

[[ "$ACTION" == unit || "$ACTION" == attestation || "$ACTION" == integration || "$ACTION" == all ]] \
    || { echo 'usage: remote-test.sh [unit|attestation|integration|all]' >&2; exit 1; }

declare -a FAILURES=()

run_check() {
    local label="$1"
    shift

    echo "=== $label ==="
    if "$@"; then
        echo "PASS: $label"
    else
        local status=$?
        echo "FAIL: $label (exit $status)" >&2
        FAILURES+=("$label (exit $status)")
    fi
}

run_in_directory() {
    local directory="$1"
    shift
    (cd "$directory" && "$@")
}

run_openclaw_transport_tests() {
    (
        cd "$AGENT_CC_DIR/adapters/OpenClaw/spiffe-transport"
        npm install --ignore-scripts --package-lock=false \
            && npm test
    )
}

run_benchmark_tool_tests() {
    (
        cd "$SPIRE_ROOT/benchmarks/asymmetric"
        node --test load-generator.test.mjs \
            && python3 -m unittest -v test_collector.py test_report.py
    )
}

run_unit() {
    run_check 'Rust Guard' \
        cargo test --manifest-path "$AGENT_CC_DIR/core/argus/Cargo.toml"
    run_check 'SPIRE NodeAttestor plug-in' \
        run_in_directory "$SPIRE_ROOT/plugins/argus-tdx-nodeattestor" go test ./...
    run_check 'SPIRE WorkloadAttestor plug-in' \
        run_in_directory "$SPIRE_ROOT/plugins/argus-tdx-workloadattestor" go test ./...
    run_check 'SVID materializer' \
        run_in_directory "$SPIRE_ROOT/components/svid-materializer" go test ./...
    run_check 'OpenClaw SPIFFE transport' run_openclaw_transport_tests
    run_check 'OpenViking Broker Sidecar' \
        run_in_directory "$AGENT_CC_DIR/adapters/OpenViking/broker_sidecar" go test ./...
    run_check 'Asymmetric evaluation tooling' run_benchmark_tool_tests
}

run_attestation() {
    # The isolated M3/M4 stack must stay on the compose network: the SPIRE
    # agent's argus_tdx plugin calls the mock Trustee at fake-services:18443.
    # A corporate proxy configured on the host (daemon systemd drop-in or the
    # docker client proxies block) would intercept that call and answer 504.
    # Pin the no-proxy list to the stack's service names before invoking the
    # matrix; environment variables take precedence over the docker client
    # proxy configuration.
    export no_proxy="fake-services,spire-server${no_proxy:+,$no_proxy}"
    export NO_PROXY="fake-services,spire-server${NO_PROXY:+,$NO_PROXY}"

    run_check 'Isolated Node Attestation and Broker Workload Attestation ALLOW matrix' \
        env \
        M3_SERVER_METRICS_PORT="${M3_SERVER_METRICS_PORT:-29988}" \
        M3_AGENT_METRICS_PORT="${M3_AGENT_METRICS_PORT:-29989}" \
        bash "$SPIRE_ROOT/tests/nodeattestor-mock/test.sh"
    run_check 'Isolated Broker Workload Attestation Trustee DENY' \
        env \
        M3_SERVER_METRICS_PORT="${M3_SERVER_METRICS_PORT:-29988}" \
        M3_AGENT_METRICS_PORT="${M3_AGENT_METRICS_PORT:-29989}" \
        M4_WORKLOAD_DECISION=deny \
        bash "$SPIRE_ROOT/tests/nodeattestor-mock/test.sh"
    run_check 'Isolated argus_tdx software failure matrix' \
        env \
        M3_SERVER_METRICS_PORT="${M3_SERVER_METRICS_PORT:-29988}" \
        M3_AGENT_METRICS_PORT="${M3_AGENT_METRICS_PORT:-29989}" \
        bash "$SPIRE_ROOT/tests/tdvm/test-failures.sh"
}

run_integration() {
    run_check 'Asymmetric runtime architecture' \
        bash "$SCRIPT_DIR/verify-architecture.sh"
    run_check 'Caller-local Guard failure matrix' \
        bash "$SCRIPT_DIR/verify-guard-gate-failures.sh"
    if [[ "${V2_RUN_BUSINESS_E2E:-1}" == "1" ]]; then
        if [[ -z "${OPENVIKING_API_KEY:-}" ]]; then
            echo 'FAIL: OpenClaw/OpenViking business E2E (OPENVIKING_API_KEY is required)' >&2
            FAILURES+=("OpenClaw/OpenViking business E2E (missing OPENVIKING_API_KEY)")
        else
            run_check 'OpenClaw/OpenViking business E2E' \
                bash "$SCRIPT_DIR/verify-openclaw-plugin-e2e.sh"
        fi
    fi
}

if [[ "$ACTION" == unit || "$ACTION" == all ]]; then run_unit; fi
if [[ "$ACTION" == attestation || "$ACTION" == all ]]; then run_attestation; fi
if [[ "$ACTION" == integration || "$ACTION" == all ]]; then run_integration; fi

if (( ${#FAILURES[@]} > 0 )); then
    echo '=== Remote validation failures ===' >&2
    printf ' - %s\n' "${FAILURES[@]}" >&2
    exit 1
fi

echo "Remote validation action '$ACTION' passed."
