#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-all}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPIRE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
AGENT_CC_DIR="$(cd "$SPIRE_ROOT/../.." && pwd)"
RUNTIME_SCRIPT_DIR="$SPIRE_ROOT/runtime/asymmetric/scripts"
RUNTIME_DIR="${V2_RUNTIME_DIR:-}"

[[ "$ACTION" == unit || "$ACTION" == preflight || "$ACTION" == e3 \
    || "$ACTION" == e4 || "$ACTION" == e5 || "$ACTION" == e6 \
    || "$ACTION" == e7 || "$ACTION" == report || "$ACTION" == all ]] \
    || { echo 'usage: run.sh [unit|preflight|e3|e4|e5|e6|e7|report|all]' >&2; exit 2; }

OPENCLAW_CONTAINER="${V2_REAL_OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
SPIRE_SERVER_CONTAINER="${V2_SPIRE_SERVER_CONTAINER:-argus-v2-spire-server}"
OPENCLAW_AGENT_CONTAINER="${V2_OPENCLAW_AGENT_CONTAINER:-argus-v2-openclaw-agent}"
GUARD_CONTAINER="${V2_GUARD_CONTAINER:-argus-v2-guard}"
TRUSTEE_CONTAINER="${V2_TRUSTEE_CONTAINER:-argus-v2-mock-trustee}"
OPENVIKING_CONTAINER="${V2_REAL_OPENVIKING_CONTAINER:-agentcc-openviking-service}"
OPENVIKING_AGENT_CONTAINER="${V2_OPENVIKING_AGENT_CONTAINER:-argus-v2-openviking-agent}"
PROVIDER_CONTAINER="${V2_PROVIDER_CONTAINER:-argus-v2-mock-evidence-provider}"
OPENVIKING_ORIGIN="${V2_OPENVIKING_ORIGIN:-https://openviking.argus.local:1943}"
TARGET_URL="${ARGUS_BENCHMARK_TARGET_URL:-$OPENVIKING_ORIGIN/health}"
GUARD_URL="${V2_GUARD_INTERNAL_URL:-http://guard:8007/guard/v1/authorize}"
GUARD_HOST_URL="${V2_GUARD_URL:-http://127.0.0.1:${V2_GUARD_PORT:-18007}}"
SPIRE_METRICS_URL="http://127.0.0.1:${V2_SPIRE_METRICS_PORT:-19988}/metrics"
TDVM_SSH_TARGET="${TDVM_SSH_TARGET:-tdx@127.0.0.1}"
TDVM_SSH_PORT="${TDVM_SSH_PORT:-2222}"
TDVM_SSH_IDENTITY="${TDVM_SSH_IDENTITY:-}"
TDVM_KNOWN_HOSTS="${TDVM_KNOWN_HOSTS:-/tmp/argus-benchmark-known-hosts}"
CONTAINER_LOAD_GENERATOR=/tmp/argus-asymmetric-load-generator.mjs
COLLECT_INTERVAL_SECONDS="${ARGUS_BENCHMARK_COLLECT_INTERVAL_SECONDS:-5}"

declare -a CASE_FAILURES=()
COLLECTOR_PID=""
COLLECTOR_STOP_FILE=""

fail() {
    printf 'Argus asymmetric benchmark: FAIL: %s\n' "$1" >&2
    exit 1
}

ssh_arguments=(
    -o BatchMode=yes
    -o ConnectTimeout=10
    -o StrictHostKeyChecking=accept-new
    -o "UserKnownHostsFile=$TDVM_KNOWN_HOSTS"
    -p "$TDVM_SSH_PORT"
)
[[ -n "$TDVM_SSH_IDENTITY" ]] && ssh_arguments+=(-i "$TDVM_SSH_IDENTITY")

run_unit() {
    (
        cd "$SCRIPT_DIR"
        node --test load-generator.test.mjs
        python3 -m unittest -v test_collector.py test_report.py
    )
}

preflight() {
    [[ "$(uname -s)" == Linux ]] || fail 'benchmarks must run on the remote Linux host'
    [[ -n "$RUNTIME_DIR" && "$RUNTIME_DIR" == /* ]] \
        || fail 'V2_RUNTIME_DIR must be an absolute remote-host path'
    local command
    for command in docker python3 node curl ssh git; do
        command -v "$command" >/dev/null 2>&1 || fail "missing command: $command"
    done
    local container
    for container in \
        "$OPENCLAW_CONTAINER" "$SPIRE_SERVER_CONTAINER" "$OPENCLAW_AGENT_CONTAINER" \
        "$GUARD_CONTAINER" "$TRUSTEE_CONTAINER"; do
        docker inspect "$container" >/dev/null 2>&1 || fail "host container is missing: $container"
    done
    for container in "$OPENVIKING_CONTAINER" "$OPENVIKING_AGENT_CONTAINER" "$PROVIDER_CONTAINER"; do
        ssh "${ssh_arguments[@]}" "$TDVM_SSH_TARGET" \
            sudo -n /usr/local/bin/docker inspect "$container" >/dev/null \
            || fail "TD Guest container is missing: $container"
    done
    curl -fsS --noproxy '127.0.0.1,localhost' --max-time 5 \
        "$GUARD_HOST_URL/health" \
        | python3 -c 'import json,sys; value=json.load(sys.stdin); assert value.get("status") == "OK" and value.get("mode") == "spiffe_identity"' \
        || fail 'Guard is not healthy in spiffe_identity mode'
    curl -fsS --noproxy '127.0.0.1,localhost' --max-time 5 \
        "$GUARD_HOST_URL/metrics" | grep -q '^argus_guard_requests_total' \
        || fail 'Guard benchmark metrics endpoint is unavailable; rebuild the Guard image'
    curl -fsS --noproxy '127.0.0.1,localhost' --max-time 5 \
        "$SPIRE_METRICS_URL" >/dev/null \
        || fail 'SPIRE Server metrics endpoint is unavailable'
    V2_RUNTIME_DIR="$RUNTIME_DIR" bash "$RUNTIME_SCRIPT_DIR/verify-svid.sh"
    docker cp "$SCRIPT_DIR/load-generator.mjs" \
        "$OPENCLAW_CONTAINER:$CONTAINER_LOAD_GENERATOR"
    docker exec -u node "$OPENCLAW_CONTAINER" test -r "$CONTAINER_LOAD_GENERATOR" \
        || fail 'load generator is not readable inside OpenClaw'
    docker exec -i -u node -e ARGUS_SPIFFE_TELEMETRY=1 "$OPENCLAW_CONTAINER" \
        node "$CONTAINER_LOAD_GENERATOR" \
        --mode guarded --url "$TARGET_URL" --guard-url "$GUARD_URL" \
        --requests 1 --concurrency 1 --profile preflight --fail-on-error 1 \
        >/dev/null || fail 'formal Guard + SPIFFE mTLS request failed'
}

new_run_directory() {
    if [[ -n "${ARGUS_BENCHMARK_RUN_DIR:-}" ]]; then
        RUN_DIR="$ARGUS_BENCHMARK_RUN_DIR"
    else
        local result_root="${ARGUS_BENCHMARK_RESULT_ROOT:-$RUNTIME_DIR/benchmarks}"
        local run_id="run-$(date -u +%Y%m%dT%H%M%SZ)"
        RUN_DIR="$result_root/$run_id"
    fi
    [[ "$RUN_DIR" == /* ]] || fail "benchmark run directory must be absolute: $RUN_DIR"
    if [[ -e "$RUN_DIR" ]]; then
        [[ -d "$RUN_DIR" ]] || fail "run path is not a directory: $RUN_DIR"
        [[ -n "${ARGUS_BENCHMARK_RUN_DIR:-}" ]] \
            || fail "refusing to reuse automatically generated run directory: $RUN_DIR"
    else
        mkdir -p "$RUN_DIR/cases"
    fi
    mkdir -p "$RUN_DIR/cases"
}

write_manifest() {
    local commit
    commit="$(git -C "$AGENT_CC_DIR" rev-parse HEAD)"
    python3 - "$RUN_DIR/manifest.json" "$commit" "$ACTION" "$TARGET_URL" \
        "$RUNTIME_DIR" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" <<'PY'
import json, platform, sys
path, commit, action, target, runtime, started = sys.argv[1:]
value = {
    "schema_version": "argus-benchmark-manifest-v1",
    "git_commit": commit,
    "action": action,
    "target_url": target,
    "runtime_dir": runtime,
    "started_at_utc": started,
    "host": platform.node(),
    "platform": platform.platform(),
    "attestation_profile": "mock_ra_mock_trustee",
    "real_quote_qgs": "deferred",
}
with open(path, "w", encoding="utf-8") as destination:
    json.dump(value, destination, indent=2, sort_keys=True)
    destination.write("\n")
PY
}

snapshot_metrics() {
    local suffix="$1"
    curl -fsS --noproxy '127.0.0.1,localhost' --max-time 5 \
        "$SPIRE_METRICS_URL" >"$RUN_DIR/spire-metrics-$suffix.prom"
    curl -fsS --noproxy '127.0.0.1,localhost' --max-time 5 \
        "$GUARD_HOST_URL/metrics" >"$RUN_DIR/guard-metrics-$suffix.prom"
}

stop_collector() {
    if [[ -n "$COLLECTOR_STOP_FILE" ]]; then
        touch "$COLLECTOR_STOP_FILE"
    fi
    if [[ -n "$COLLECTOR_PID" ]]; then
        wait "$COLLECTOR_PID" || CASE_FAILURES+=("resource collector (exit $?)")
    fi
    COLLECTOR_PID=""
    COLLECTOR_STOP_FILE=""
}

start_collector() {
    local case_directory="$1"
    COLLECTOR_STOP_FILE="$case_directory/collector.stop"
    local arguments=(
        --output "$case_directory/resources.jsonl"
        --stop-file "$COLLECTOR_STOP_FILE"
        --interval-seconds "$COLLECT_INTERVAL_SECONDS"
        --target-port 1943
        --host-container "openclaw=$OPENCLAW_CONTAINER"
        --host-container "guard=$GUARD_CONTAINER"
        --host-container "spire-server=$SPIRE_SERVER_CONTAINER"
        --host-container "openclaw-agent=$OPENCLAW_AGENT_CONTAINER"
        --host-container "mock-trustee=$TRUSTEE_CONTAINER"
        --guest-container "openviking=$OPENVIKING_CONTAINER"
        --guest-container "openviking-agent=$OPENVIKING_AGENT_CONTAINER"
        --guest-container "mock-evidence-provider=$PROVIDER_CONTAINER"
        --metrics-endpoint "spire-server=$SPIRE_METRICS_URL"
        --metrics-endpoint "guard=$GUARD_HOST_URL/metrics"
        --host-svid-container "$OPENCLAW_CONTAINER"
        --guest-svid-container "$OPENVIKING_CONTAINER"
        --tdvm-ssh-target "$TDVM_SSH_TARGET"
        --tdvm-ssh-port "$TDVM_SSH_PORT"
        --tdvm-known-hosts "$TDVM_KNOWN_HOSTS"
    )
    [[ -n "$TDVM_SSH_IDENTITY" ]] \
        && arguments+=(--tdvm-ssh-identity "$TDVM_SSH_IDENTITY")
    python3 "$SCRIPT_DIR/collector.py" "${arguments[@]}" \
        >"$case_directory/collector.stdout.log" \
        2>"$case_directory/collector.stderr.log" &
    COLLECTOR_PID=$!
    sleep "${ARGUS_BENCHMARK_COLLECTOR_STARTUP_SECONDS:-0.5}"
    kill -0 "$COLLECTOR_PID" >/dev/null 2>&1 \
        || fail "resource collector exited before case start: $case_directory"
}

write_case_metadata() {
    local path="$1" experiment="$2" profile="$3" mode="$4"
    local requests="$5" duration="$6" qps="$7" concurrency="$8" record_every="$9"
    python3 - "$path" "$experiment" "$profile" "$mode" "$requests" \
        "$duration" "$qps" "$concurrency" "$record_every" <<'PY'
import json, sys
path, experiment, profile, mode, requests, duration, qps, concurrency, record_every = sys.argv[1:]
value = {
    "schema_version": "argus-benchmark-case-v1",
    "experiment": experiment,
    "profile": profile,
    "mode": mode,
    "requests": int(requests),
    "duration_seconds": float(duration),
    "requested_qps": float(qps),
    "concurrency": int(concurrency),
    "record_every": int(record_every),
}
with open(path, "w", encoding="utf-8") as destination:
    json.dump(value, destination, indent=2, sort_keys=True)
    destination.write("\n")
PY
}

run_case() {
    local experiment="$1" name="$2" profile="$3" mode="$4"
    local requests="$5" duration="$6" qps="$7" concurrency="$8" record_every="${9:-1}"
    local case_directory="$RUN_DIR/cases/$name"
    [[ ! -e "$case_directory" ]] || fail "case directory already exists: $case_directory"
    mkdir "$case_directory"
    write_case_metadata "$case_directory/metadata.json" "$experiment" "$profile" \
        "$mode" "$requests" "$duration" "$qps" "$concurrency" "$record_every"
    printf '=== %s: %s ===\n' "$experiment" "$profile"
    start_collector "$case_directory"
    local status=0
    local failure_reason=""
    docker exec -i -u node \
        -e ARGUS_SPIFFE_TELEMETRY=1 \
        -e ARGUS_BENCHMARK_OPERATION=memory.read \
        "$OPENCLAW_CONTAINER" node "$CONTAINER_LOAD_GENERATOR" \
        --mode "$mode" \
        --url "$TARGET_URL" \
        --guard-url "$GUARD_URL" \
        --requests "$requests" \
        --duration-seconds "$duration" \
        --qps "$qps" \
        --concurrency "$concurrency" \
        --record-every "$record_every" \
        --warmup-requests "${ARGUS_BENCHMARK_WARMUP_REQUESTS:-20}" \
        --timeout-ms "${ARGUS_BENCHMARK_REQUEST_TIMEOUT_MS:-10000}" \
        --profile "$profile" \
        >"$case_directory/requests.jsonl" \
        2>"$case_directory/load-generator.stderr.log" || {
            status=$?
            failure_reason="load generator exit $status"
        }
    sleep "${ARGUS_BENCHMARK_COOLDOWN_SECONDS:-2}"
    stop_collector
    if (( status == 0 )); then
        if ! python3 - "$case_directory/requests.jsonl" <<'PY'
import json, sys
summary = None
with open(sys.argv[1], encoding="utf-8") as source:
    for line in source:
        value = json.loads(line)
        if value.get("type") == "summary":
            summary = value
if summary is None:
    raise SystemExit("load generator did not emit a summary")
if int(summary.get("requests", 0)) <= 0 or int(summary.get("succeeded", 0)) <= 0:
    raise SystemExit("benchmark case produced no successful requests")
PY
        then
            status=3
            failure_reason="missing or unsuccessful request summary"
        fi
    fi
    if ! python3 - "$case_directory/resources.jsonl" <<'PY'
import json, sys
records = []
try:
    with open(sys.argv[1], encoding="utf-8") as source:
        records = [json.loads(line) for line in source if line.strip()]
except FileNotFoundError:
    pass
if not records:
    raise SystemExit("resource collector emitted no samples")
if not any(record.get("host_containers") for record in records):
    raise SystemExit("resource collector emitted no host-container samples")
if not any(record.get("guest_containers") for record in records):
    raise SystemExit("resource collector emitted no TD Guest-container samples")
PY
    then
        if (( status == 0 )); then
            status=4
            failure_reason="incomplete resource samples"
        fi
    fi
    if (( status != 0 )); then
        CASE_FAILURES+=("$name (${failure_reason:-exit $status})")
        printf 'FAIL: %s (%s)\n' "$name" "${failure_reason:-exit $status}" >&2
    else
        printf 'PASS: %s completed\n' "$name"
    fi
}

run_e3() {
    local requests="${E3_REQUESTS_PER_STEP:-0}"
    local duration="${E3_STEP_DURATION_SECONDS:-30}"
    local raw_steps="${E3_CONCURRENCY_STEPS:-1,4,8,16,32}"
    local steps
    IFS=',' read -r -a steps <<<"$raw_steps"
    local concurrency
    for concurrency in "${steps[@]}"; do
        [[ "$concurrency" =~ ^[1-9][0-9]*$ ]] \
            || fail "invalid E3 concurrency step: $concurrency"
        run_case E3 "e3-guard-c$concurrency" "guard-c$concurrency" guard \
            "$requests" "$duration" 0 "$concurrency" "${E3_RECORD_EVERY:-10}"
    done
}

run_e4() {
    local requests="${E4_REQUESTS_PER_PROFILE:-2000}"
    local concurrency="${E4_CONCURRENCY:-8}"
    run_case E4 e4-guarded-new-connection guarded-new-connection \
        guarded-new-connection "$requests" 0 0 "$concurrency"
    run_case E4 e4-guarded-keepalive guarded-keepalive \
        guarded "$requests" 0 0 "$concurrency"
    run_case E4 e4-diagnostic-mtls-only diagnostic-mtls-only \
        diagnostic-mtls-only "$requests" 0 0 "$concurrency"
}

run_e5() {
    local duration="${E5_STEP_DURATION_SECONDS:-60}"
    local concurrency="${E5_CONCURRENCY:-32}"
    local raw_steps="${E5_QPS_STEPS:-10,25,50,100}"
    local steps
    IFS=',' read -r -a steps <<<"$raw_steps"
    local qps
    for qps in "${steps[@]}"; do
        [[ "$qps" =~ ^[0-9]+([.][0-9]+)?$ && "$qps" != "0" && "$qps" != "0.0" ]] \
            || fail "invalid E5 QPS step: $qps"
        run_case E5 "e5-qps-$qps" "guarded-keepalive-qps-$qps" guarded \
            0 "$duration" "$qps" "$concurrency"
    done
}

run_e6() {
    local ttl="${V2_SVID_TTL_SECONDS:-}"
    if [[ -z "$ttl" ]]; then
        ttl="$(docker exec "$OPENCLAW_CONTAINER" cat /run/argus-svid/status.json \
            | python3 -c 'import json,sys; value=json.load(sys.stdin); print(int(value["not_after_unix"]) - int(value["not_before_unix"]))')"
    fi
    [[ "$ttl" =~ ^[1-9][0-9]*$ ]] || fail "unable to determine a positive SVID TTL: $ttl"
    local duration="${E6_DURATION_SECONDS:-$((ttl * 3 + 60))}"
    [[ "$duration" =~ ^[1-9][0-9]*$ ]] || fail "E6 duration must be a positive integer: $duration"
    (( duration >= ttl * 3 )) \
        || fail "E6_DURATION_SECONDS must span at least three SVID TTL periods ($((ttl * 3))s)"
    run_case E6 e6-svid-rotation guarded-keepalive-rotation guarded \
        0 "$duration" "${E6_QPS:-10}" "${E6_CONCURRENCY:-8}"
}

generate_report() {
    python3 "$SCRIPT_DIR/report.py" --run-dir "$RUN_DIR"
}

if [[ "$ACTION" == unit ]]; then
    run_unit
    exit 0
fi

if [[ "$ACTION" == report || "$ACTION" == e7 ]]; then
    [[ -n "${ARGUS_BENCHMARK_RUN_DIR:-}" ]] \
        || fail 'ARGUS_BENCHMARK_RUN_DIR is required for report/e7'
    RUN_DIR="$ARGUS_BENCHMARK_RUN_DIR"
    [[ -d "$RUN_DIR" ]] || fail "run directory does not exist: $RUN_DIR"
    generate_report
    exit 0
fi

preflight
if [[ "$ACTION" == preflight ]]; then
    echo 'Argus asymmetric benchmark preflight passed.'
    exit 0
fi

new_run_directory
write_manifest
snapshot_metrics before

trap stop_collector EXIT
trap 'stop_collector; exit 130' INT
trap 'stop_collector; exit 143' TERM
[[ "$ACTION" == e3 || "$ACTION" == all ]] && run_e3
[[ "$ACTION" == e4 || "$ACTION" == all ]] && run_e4
[[ "$ACTION" == e5 || "$ACTION" == all ]] && run_e5
[[ "$ACTION" == e6 || "$ACTION" == all ]] && run_e6
stop_collector
trap - EXIT INT TERM

snapshot_metrics after
generate_report

if (( ${#CASE_FAILURES[@]} > 0 )); then
    echo '=== Benchmark case failures ===' >&2
    printf ' - %s\n' "${CASE_FAILURES[@]}" >&2
    exit 1
fi

printf '%s\n' \
    "Argus asymmetric benchmark action '$ACTION' completed." \
    "Run directory: $RUN_DIR" \
    "Report: $RUN_DIR/report.md"
