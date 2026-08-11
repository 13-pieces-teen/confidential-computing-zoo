#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-all}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCHMARK_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SPIRE_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
AGENT_CC_DIR="$(cd "$SPIRE_ROOT/../.." && pwd)"
RUNTIME_SCRIPT_DIR="$SPIRE_ROOT/runtime/asymmetric/scripts"
RUNTIME_DIR="${V2_RUNTIME_DIR:-}"

[[ "$ACTION" == unit || "$ACTION" == preflight || "$ACTION" == pilot \
    || "$ACTION" == c1 || "$ACTION" == c2 || "$ACTION" == c4 || "$ACTION" == c8 \
    || "$ACTION" == all || "$ACTION" == report ]] \
    || { echo 'usage: run.sh [unit|preflight|pilot|c1|c2|c4|c8|all|report]' >&2; exit 2; }

SOURCE_OPENCLAW_CONTAINER="${V2_REAL_OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
OPENCLAW_CONFIG="${V2_REAL_OPENCLAW_CONFIG:-/home/node/.openclaw/openclaw.json}"
OPENVIKING_ORIGIN="${V2_OPENVIKING_ORIGIN:-https://openviking.argus.local:1943}"
SPIRE_SERVER_CONTAINER="${V2_SPIRE_SERVER_CONTAINER:-argus-v2-spire-server}"
OPENCLAW_AGENT_CONTAINER="${V2_OPENCLAW_AGENT_CONTAINER:-argus-v2-openclaw-agent}"
GUARD_CONTAINER="${V2_GUARD_CONTAINER:-argus-v2-guard}"
TRUSTEE_CONTAINER="${V2_TRUSTEE_CONTAINER:-argus-v2-mock-trustee}"
OPENVIKING_CONTAINER="${V2_REAL_OPENVIKING_CONTAINER:-agentcc-openviking-service}"
OPENVIKING_AGENT_CONTAINER="${V2_OPENVIKING_AGENT_CONTAINER:-argus-v2-openviking-agent}"
PROVIDER_CONTAINER="${V2_PROVIDER_CONTAINER:-argus-v2-mock-evidence-provider}"
GUARD_HOST_URL="${V2_GUARD_URL:-http://127.0.0.1:${V2_GUARD_PORT:-18007}}"
SPIRE_METRICS_URL="http://127.0.0.1:${V2_SPIRE_METRICS_PORT:-19988}/metrics"
TDVM_SSH_TARGET="${TDVM_SSH_TARGET:-tdx@127.0.0.1}"
TDVM_SSH_PORT="${TDVM_SSH_PORT:-2222}"
TDVM_SSH_IDENTITY="${TDVM_SSH_IDENTITY:-}"
TDVM_KNOWN_HOSTS="${TDVM_KNOWN_HOSTS:-/tmp/argus-e8-known-hosts}"
COLLECT_INTERVAL_SECONDS="${E8_COLLECT_INTERVAL_SECONDS:-5}"

AGENT_TIMEOUT_MS="${E8_AGENT_TIMEOUT_MS:-180000}"
CAPTURE_TIMEOUT_MS="${E8_CAPTURE_TIMEOUT_MS:-60000}"
COMMIT_TIMEOUT_MS="${E8_COMMIT_TIMEOUT_MS:-300000}"
ARCHIVE_TIMEOUT_MS="${E8_ARCHIVE_TIMEOUT_MS:-300000}"
CAPTURE_POLL_MS="${E8_CAPTURE_POLL_MS:-1000}"
ARCHIVE_POLL_MS="${E8_ARCHIVE_POLL_MS:-2000}"

RUN_DIR=""
RUN_ID=""
SOURCE_CONFIG_VOLUME=""
COLLECTOR_PID=""
COLLECTOR_STOP_FILE=""
declare -a ACTIVE_CONTAINERS=()

fail() {
    printf 'Argus E8 Agent-task benchmark: FAIL: %s\n' "$1" >&2
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
        node --test task-worker.test.mjs
        python3 -m unittest -v test_report.py
    )
}

source_config_volume() {
    docker inspect "$SOURCE_OPENCLAW_CONTAINER" --format \
        '{{range .Mounts}}{{if eq .Destination "/home/node/.openclaw"}}{{println .Name}}{{end}}{{end}}' \
        | sed -n '/./{p;q;}'
}

source_model_ca_bundle() {
    docker inspect "$SOURCE_OPENCLAW_CONTAINER" --format \
        '{{range .Mounts}}{{if eq .Destination "/opt/model-ca/argus-ca-bundle.pem"}}{{println .Source}}{{end}}{{end}}' \
        | sed -n '/./{p;q;}'
}

check_remote_environment() {
    [[ "$(uname -s)" == Linux ]] || fail 'this benchmark runs only on the remote Linux host'
    [[ -n "$RUNTIME_DIR" && "$RUNTIME_DIR" == /* ]] || fail 'V2_RUNTIME_DIR must be an absolute path'
    local command
    for command in docker node python3 curl ssh git sha256sum; do
        command -v "$command" >/dev/null 2>&1 || fail "missing command: $command"
    done
    [[ -n "${OPENVIKING_API_KEY:-}" ]] || fail 'OPENVIKING_API_KEY is required'
    [[ -S "$RUNTIME_DIR/openclaw-agent-run/agent.sock" ]] || fail 'OpenClaw Workload API socket is missing'
    docker inspect "$SOURCE_OPENCLAW_CONTAINER" >/dev/null 2>&1 || fail "source OpenClaw container is missing: $SOURCE_OPENCLAW_CONTAINER"
    [[ "$(docker inspect "$SOURCE_OPENCLAW_CONTAINER" --format '{{.State.Running}}')" == true ]] || fail 'source OpenClaw container is not running'
    SOURCE_CONFIG_VOLUME="$(source_config_volume)"
    [[ -n "$SOURCE_CONFIG_VOLUME" ]] || fail 'source OpenClaw config must use a named volume'
    docker volume inspect "$SOURCE_CONFIG_VOLUME" >/dev/null 2>&1 || fail "source config volume is missing: $SOURCE_CONFIG_VOLUME"
    local guest_container
    for guest_container in "$OPENVIKING_CONTAINER" "$OPENVIKING_AGENT_CONTAINER" "$PROVIDER_CONTAINER"; do
        ssh "${ssh_arguments[@]}" "$TDVM_SSH_TARGET" \
            sudo -n /usr/local/bin/docker inspect "$guest_container" >/dev/null \
            || fail "TD Guest container is missing: $guest_container"
    done
    curl -fsS --noproxy '127.0.0.1,localhost' --max-time 5 "$GUARD_HOST_URL/health" \
        | python3 -c 'import json,sys; value=json.load(sys.stdin); assert value.get("status") == "OK" and value.get("mode") == "spiffe_identity"' \
        || fail 'Guard is not healthy in spiffe_identity mode'
    curl -fsS --noproxy '127.0.0.1,localhost' --max-time 5 "$SPIRE_METRICS_URL" >/dev/null \
        || fail 'SPIRE metrics endpoint is unavailable'
    docker exec -i -u node "$SOURCE_OPENCLAW_CONTAINER" node - "$OPENCLAW_CONFIG" "$OPENVIKING_ORIGIN" <<'NODE'
const { execFileSync } = require("node:child_process");
const [configPath, expected] = process.argv.slice(2);
function get(path) {
  return JSON.parse(execFileSync("openclaw", ["config", "get", path, "--json"], {
    encoding: "utf8", env: { ...process.env, OPENCLAW_CONFIG_PATH: configPath },
  }).trim());
}
const plugin = get("plugins.entries.openviking.config");
if (get("plugins.slots.contextEngine") !== "openviking") throw new Error("OpenViking is not the context-engine slot");
if (plugin?.mode !== "remote" || plugin?.baseUrl?.replace(/\/+$/, "") !== expected.replace(/\/+$/, "")) {
  throw new Error("OpenViking plugin does not target the expected origin");
}
if (!plugin.apiKey) throw new Error("OpenViking plugin API key is missing");
NODE
}

preflight() {
    check_remote_environment
    V2_RUNTIME_DIR="$RUNTIME_DIR" bash "$RUNTIME_SCRIPT_DIR/verify-svid.sh"
    if [[ "${E8_PREFLIGHT_RUN_REAL_E2E:-1}" == "1" ]]; then
        bash "$RUNTIME_SCRIPT_DIR/verify-openclaw-plugin-e2e.sh"
    fi
}

new_run_directory() {
    if [[ -n "${E8_RUN_DIR:-}" ]]; then
        RUN_DIR="$E8_RUN_DIR"
    else
        local root="${E8_RESULT_ROOT:-/var/lib/argus-spire-asymmetric/agent-tasks}"
        RUN_DIR="$root/run-$(date -u +%Y%m%dT%H%M%SZ)"
    fi
    [[ "$RUN_DIR" == /* ]] || fail "run directory must be absolute: $RUN_DIR"
    [[ ! -e "$RUN_DIR" ]] || fail "run directory already exists: $RUN_DIR"
    mkdir -p "$RUN_DIR/cases"
    RUN_ID="$(basename "$RUN_DIR")"
}

write_manifest() {
    local safe_profile="$RUN_DIR/config-profile.json"
    docker exec -i -u node "$SOURCE_OPENCLAW_CONTAINER" node - "$OPENCLAW_CONFIG" >"$safe_profile" <<'NODE'
const { execFileSync } = require("node:child_process");
const { createHash } = require("node:crypto");
const configPath = process.argv[2];
function optional(path) {
  try {
    return JSON.parse(execFileSync("openclaw", ["config", "get", path, "--json"], {
      encoding: "utf8", env: { ...process.env, OPENCLAW_CONFIG_PATH: configPath },
    }).trim());
  } catch { return null; }
}
const model = optional("agents.defaults.model");
const primary = typeof model === "string" ? model : model?.primary ?? null;
const plugin = optional("plugins.entries.openviking.config") ?? {};
const profile = {
  openclaw_provider: typeof primary === "string" && primary.includes("/") ? primary.split("/", 1)[0] : null,
  openclaw_model: primary,
  openclaw_temperature: optional("agents.defaults.temperature"),
  openclaw_max_tokens: optional("agents.defaults.maxTokens"),
  openviking_plugin: {
    mode: plugin.mode ?? null,
    base_url: plugin.baseUrl ?? null,
    peer_prefix: plugin.peer_prefix ?? null,
  },
};
profile.non_secret_config_fingerprint = createHash("sha256").update(JSON.stringify(profile)).digest("hex");
process.stdout.write(JSON.stringify(profile));
NODE
    local commit prompt_hash image_digest openviking_image_digest
    commit="$(git -C "$AGENT_CC_DIR" rev-parse HEAD)"
    prompt_hash="$(sha256sum "$SCRIPT_DIR/prompts.json" | awk '{print $1}')"
    image_digest="$(docker inspect "$SOURCE_OPENCLAW_CONTAINER" --format '{{.Image}}')"
    openviking_image_digest="$(ssh "${ssh_arguments[@]}" "$TDVM_SSH_TARGET" \
        sudo -n /usr/local/bin/docker inspect "$OPENVIKING_CONTAINER" --format '{{.Image}}')"
    python3 - "$RUN_DIR/manifest.json" "$safe_profile" "$commit" "$ACTION" "$RUN_ID" \
        "$SOURCE_OPENCLAW_CONTAINER" "$SOURCE_CONFIG_VOLUME" "$image_digest" "$openviking_image_digest" "$prompt_hash" \
        "$AGENT_TIMEOUT_MS" "$CAPTURE_TIMEOUT_MS" "$COMMIT_TIMEOUT_MS" "$ARCHIVE_TIMEOUT_MS" \
        "$CAPTURE_POLL_MS" "$ARCHIVE_POLL_MS" <<'PY'
import json, platform, sys
(
    target, profile_path, commit, action, run_id, source_container, source_volume,
    image_digest, openviking_image_digest, prompt_hash, agent_timeout, capture_timeout, commit_timeout,
    archive_timeout, capture_poll, archive_poll,
) = sys.argv[1:]
profile = json.load(open(profile_path, encoding="utf-8"))
manifest = {
    "schema_version": "argus-e8-agent-task-run-v1",
    "profile": "multi_openclaw_real_llm_shared_x509pop_agent",
    "git_commit": commit,
    "action": action,
    "run_id": run_id,
    "host": platform.node(),
    "started_at_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    "attestation_profile": "mock_ra_mock_trustee",
    "guard_evidence_mode": "case_level",
    "source_openclaw_container": source_container,
    "source_config_volume": source_volume,
    "openclaw_image_digest": image_digest,
    "openviking_image_digest": openviking_image_digest,
    "openviking_archive_provider": None,
    "openviking_archive_model": None,
    "prompt_suite_sha256": prompt_hash,
    "agent_timeout_ms": int(agent_timeout),
    "capture_timeout_ms": int(capture_timeout),
    "commit_timeout_ms": int(commit_timeout),
    "archive_timeout_ms": int(archive_timeout),
    "capture_poll_interval_ms": int(capture_poll),
    "archive_poll_interval_ms": int(archive_poll),
    "provider_retry_policy": None,
    **profile,
}
with open(target, "w", encoding="utf-8") as destination:
    json.dump(manifest, destination, indent=2, sort_keys=True)
    destination.write("\n")
PY
    rm -f "$safe_profile"
    cp "$SCRIPT_DIR/prompts.json" "$RUN_DIR/prompts.json"
}

snapshot_metrics() {
    local directory="$1" suffix="$2"
    curl -fsS --noproxy '127.0.0.1,localhost' --max-time 5 "$SPIRE_METRICS_URL" >"$directory/spire-metrics-$suffix.prom"
    curl -fsS --noproxy '127.0.0.1,localhost' --max-time 5 "$GUARD_HOST_URL/metrics" >"$directory/guard-metrics-$suffix.prom"
}

clone_config_volume() {
    local destination="$1"
    docker volume create "$destination" >/dev/null
    docker run --rm --user root \
        -v "$SOURCE_CONFIG_VOLUME:/source:ro" \
        -v "$destination:/destination" \
        --entrypoint sh openclaw-sbx:latest -ec '
            cp -a /source/. /destination/
            rm -rf /destination/identity /destination/logs /destination/cache /destination/workspace
            rm -rf /destination/agents/*/sessions
            # The source gateway writes a recent workspace-attestations marker into
            # its config dir; a clone with an empty workspace would then look like a
            # vanished, recently-attested workspace and refuse to reseed.
            rm -rf /destination/workspace-attestations
            rm -f /destination/workspace.attested /destination/.attested
            find /destination -xdev -type f \( -name "*.lock" -o -name "*.pid" \) -delete
            mkdir -p /destination/agents/main/sessions
            touch /destination/.sbx-initialized
            chown -R 1000:1000 /destination
        '
}

start_unit() {
    local case_name="$1" index="$2" case_directory="$3"
    local unit_id container config_volume workspace_volume
    unit_id="openclaw-$(printf '%02d' "$index")"
    container="argus-e8-${RUN_ID}-${case_name,,}-${unit_id}"
    config_volume="argus-e8-${RUN_ID}-${case_name,,}-${unit_id}-config"
    workspace_volume="argus-e8-${RUN_ID}-${case_name,,}-${unit_id}-workspace"
    mkdir -p "$case_directory/units/$unit_id"
    clone_config_volume "$config_volume"
    docker volume create "$workspace_volume" >/dev/null
    # The source gateway authenticates its lan bind with OPENCLAW_GATEWAY_TOKEN.
    # A cloned config volume marks the unit as already initialized, so run-sbx.sh
    # skips first-boot token generation; provide a fresh per-unit token instead.
    local gateway_token
    if command -v openssl >/dev/null 2>&1; then
        gateway_token="$(openssl rand -hex 32)"
    else
        gateway_token="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    fi
    # The model gateway is reached over the same TLS chain as the source OpenClaw;
    # carry the source container's model-CA bundle so units can reach the provider.
    local model_ca_bundle="${V2_MODEL_CA_BUNDLE:-$(source_model_ca_bundle)}"
    [[ -z "$model_ca_bundle" ]] || [[ -f "$model_ca_bundle" ]] \
        || fail "model CA bundle not found: $model_ca_bundle"
    V2_RUNTIME_DIR="$RUNTIME_DIR" \
    V2_REAL_OPENCLAW_CONTAINER="$container" \
    OPENCLAW_CONFIG_VOLUME="$config_volume" \
    OPENCLAW_WORKSPACE_VOLUME="$workspace_volume" \
    OPENCLAW_PUBLISH_PORTS=0 \
    OPENCLAW_GATEWAY_TOKEN="$gateway_token" \
    V2_MODEL_CA_BUNDLE="$model_ca_bundle" \
        bash "$RUNTIME_SCRIPT_DIR/start-openclaw-workload.sh" \
        >"$case_directory/units/$unit_id/launcher.log" 2>&1
    ACTIVE_CONTAINERS+=("$container")
    docker cp "$SCRIPT_DIR/task-worker.mjs" "$container:/tmp/argus-e8-task-worker.mjs"
    docker cp "$SCRIPT_DIR/prompts.json" "$container:/tmp/argus-e8-prompts.json"
    printf '%s\t%s\t%s\t%s\n' "$unit_id" "$container" "$config_volume" "$workspace_volume" \
        >>"$case_directory/units.tsv"
}

stop_collector() {
    if [[ -n "$COLLECTOR_STOP_FILE" ]]; then touch "$COLLECTOR_STOP_FILE"; fi
    if [[ -n "$COLLECTOR_PID" ]]; then wait "$COLLECTOR_PID" || true; fi
    COLLECTOR_PID=""
    COLLECTOR_STOP_FILE=""
}

stop_active_containers() {
    local container
    for container in "${ACTIVE_CONTAINERS[@]:-}"; do
        [[ -n "$container" ]] && docker stop "$container" >/dev/null 2>&1 || true
    done
    ACTIVE_CONTAINERS=()
}

start_collector() {
    local case_directory="$1"
    COLLECTOR_STOP_FILE="$case_directory/collector.stop"
    local arguments=(
        --output "$case_directory/resources.jsonl"
        --stop-file "$COLLECTOR_STOP_FILE"
        --interval-seconds "$COLLECT_INTERVAL_SECONDS"
        --target-port 1943
        --host-container "guard=$GUARD_CONTAINER"
        --host-container "spire-server=$SPIRE_SERVER_CONTAINER"
        --host-container "openclaw-agent=$OPENCLAW_AGENT_CONTAINER"
        --host-container "mock-trustee=$TRUSTEE_CONTAINER"
        --guest-container "openviking=$OPENVIKING_CONTAINER"
        --guest-container "openviking-agent=$OPENVIKING_AGENT_CONTAINER"
        --guest-container "mock-evidence-provider=$PROVIDER_CONTAINER"
        --metrics-endpoint "spire-server=$SPIRE_METRICS_URL"
        --metrics-endpoint "guard=$GUARD_HOST_URL/metrics"
        --guest-svid-container "$OPENVIKING_CONTAINER"
        --tdvm-ssh-target "$TDVM_SSH_TARGET"
        --tdvm-ssh-port "$TDVM_SSH_PORT"
        --tdvm-known-hosts "$TDVM_KNOWN_HOSTS"
    )
    local first_container=""
    while IFS=$'\t' read -r unit_id container _; do
        arguments+=(--host-container "$unit_id=$container")
        [[ -n "$first_container" ]] || first_container="$container"
    done <"$case_directory/units.tsv"
    arguments+=(--host-svid-container "$first_container")
    [[ -n "$TDVM_SSH_IDENTITY" ]] && arguments+=(--tdvm-ssh-identity "$TDVM_SSH_IDENTITY")
    python3 "$BENCHMARK_DIR/collector.py" "${arguments[@]}" \
        >"$case_directory/collector.stdout.log" 2>"$case_directory/collector.stderr.log" &
    COLLECTOR_PID=$!
    sleep "${E8_COLLECTOR_STARTUP_SECONDS:-1}"
    kill -0 "$COLLECTOR_PID" >/dev/null 2>&1 || fail 'resource collector exited during startup'
}

write_case_metadata() {
    local path="$1" case_name="$2" units="$3" warmups="$4" tasks="$5"
    python3 - "$path" "$case_name" "$units" "$warmups" "$tasks" <<'PY'
import json, sys
path, case_name, units, warmups, tasks = sys.argv[1:]
with open(path, "w", encoding="utf-8") as destination:
    json.dump({
        "schema_version": "argus-e8-agent-task-case-v1",
        "case": case_name,
        "openclaw_units": int(units),
        "warmup_tasks_per_unit": int(warmups),
        "measured_tasks_per_unit": int(tasks),
        "load_model": "closed_loop_one_task_per_unit",
    }, destination, indent=2, sort_keys=True)
    destination.write("\n")
PY
}

validate_case() {
    local case_directory="$1" units="$2" warmups="$3" tasks="$4" require_success="$5"
    python3 - "$case_directory/tasks.jsonl" "$case_directory/resources.jsonl" "$units" "$warmups" "$tasks" "$require_success" <<'PY'
import json, sys
path, resource_path, units, warmups, tasks, require_success = sys.argv[1:]
units, warmups, tasks = int(units), int(warmups), int(tasks)
records = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
ids = [record.get("task_id") for record in records]
if len(ids) != len(set(ids)):
    raise SystemExit("duplicate task receipts")
measured = [record for record in records if record.get("measured") is True]
warm = [record for record in records if record.get("measured") is False]
if len(measured) != units * tasks:
    raise SystemExit(f"expected {units * tasks} measured receipts, got {len(measured)}")
unit_ids = {f"openclaw-{index:02d}" for index in range(1, units + 1)}
for unit_id in unit_ids:
    unit_records = [record for record in measured if record.get("unit_id") == unit_id]
    if len(unit_records) != tasks:
        raise SystemExit(f"{unit_id} has {len(unit_records)} measured receipts, expected {tasks}")
if any(record.get("started_unix_ms") is None or record.get("finished_unix_ms") is None for record in measured):
    raise SystemExit("a measured task did not enter and leave the timed window")
if len(warm) != units * warmups or any(record.get("status") != "completed" for record in warm):
    raise SystemExit("warm-up receipts are missing or failed")
if require_success == "1" and any(record.get("status") != "completed" for record in measured):
    raise SystemExit("Pilot requires every task to complete")
for record in records:
    if record.get("status") not in {"completed", "failed"}:
        raise SystemExit("receipt has no final status")
resources = [json.loads(line) for line in open(resource_path, encoding="utf-8") if line.strip()]
if not resources or not any(record.get("host_containers") for record in resources):
    raise SystemExit("host resource samples are missing")
if not any(record.get("guest_containers") for record in resources):
    raise SystemExit("TD Guest resource samples are missing")
PY
}

run_case() {
    local case_name="$1" units="$2" warmups="$3" tasks="$4" require_success="$5"
    local case_directory="$RUN_DIR/cases/$case_name"
    [[ ! -e "$case_directory" ]] || fail "case already exists: $case_name"
    mkdir -p "$case_directory/units"
    : >"$case_directory/units.tsv"
    write_case_metadata "$case_directory/metadata.json" "$case_name" "$units" "$warmups" "$tasks"
    printf '=== E8 %s: %s OpenClaw unit(s) ===\n' "$case_name" "$units"
    local index
    for index in $(seq 1 "$units"); do start_unit "$case_name" "$index" "$case_directory"; done
    snapshot_metrics "$case_directory" before
    start_collector "$case_directory"
    local start_at worker_pids=() unit_id container _
    start_at="$(( $(date +%s%3N) + 5000 ))"
    while IFS=$'\t' read -r unit_id container _; do
        docker exec -i -u node -e OPENVIKING_API_KEY "$container" \
            node /tmp/argus-e8-task-worker.mjs \
            --run-id "$RUN_ID" --case "$case_name" --unit-id "$unit_id" \
            --prompts /tmp/argus-e8-prompts.json --base-url "$OPENVIKING_ORIGIN" \
            --openclaw-config "$OPENCLAW_CONFIG" --warmup-tasks "$warmups" \
            --measured-tasks "$tasks" --start-at-unix-ms "$start_at" \
            --agent-timeout-ms "$AGENT_TIMEOUT_MS" --capture-timeout-ms "$CAPTURE_TIMEOUT_MS" \
            --commit-timeout-ms "$COMMIT_TIMEOUT_MS" --archive-timeout-ms "$ARCHIVE_TIMEOUT_MS" \
            --capture-poll-ms "$CAPTURE_POLL_MS" --archive-poll-ms "$ARCHIVE_POLL_MS" \
            >"$case_directory/units/$unit_id/tasks.jsonl" \
            2>"$case_directory/units/$unit_id/worker.stderr.log" &
        worker_pids+=("$!")
    done <"$case_directory/units.tsv"
    local status=0 pid
    for pid in "${worker_pids[@]}"; do wait "$pid" || status=1; done
    stop_collector
    snapshot_metrics "$case_directory" after
    : >"$case_directory/tasks.jsonl"
    while IFS=$'\t' read -r unit_id container _; do
        cat "$case_directory/units/$unit_id/tasks.jsonl" >>"$case_directory/tasks.jsonl"
        docker logs "$container" >"$case_directory/units/$unit_id/container.log" 2>&1 || true
    done <"$case_directory/units.tsv"
    stop_active_containers
    (( status == 0 )) || fail "$case_name had a worker process failure"
    validate_case "$case_directory" "$units" "$warmups" "$tasks" "$require_success" \
        || fail "$case_name receipts failed validation"
}

generate_report() {
    python3 "$SCRIPT_DIR/report.py" --run-dir "$RUN_DIR"
    (
        cd "$RUN_DIR"
        find . -type f ! -name SHA256SUMS.txt -print0 | sort -z | xargs -0 sha256sum >SHA256SUMS.txt
    )
}

run_selected_cases() {
    case "$ACTION" in
        pilot)
            # Pilot exercises the full harness (start -> agent -> capture ->
            # validate -> commit -> archive) at small scale. The per-task format
            # gate is reachable but the real model (minimax-m2.7) omits the
            # end-marker on a meaningful fraction of turns, so the pilot reports
            # the measured success rate (require_success=0) instead of demanding
            # 100% completion; C1/C2/C4/C8 use the same policy.
            run_case P0 1 0 2 0
            run_case P1 2 0 3 0
            run_case P2 4 0 3 0
            ;;
        c1) run_case C1 1 1 10 0 ;;
        c2) run_case C2 2 1 10 0 ;;
        c4) run_case C4 4 1 10 0 ;;
        c8) run_case C8 8 1 10 0 ;;
        all)
            run_case P0 1 0 2 0
            run_case P1 2 0 3 0
            run_case P2 4 0 3 0
            run_case C1 1 1 10 0
            run_case C2 2 1 10 0
            run_case C4 4 1 10 0
            run_case C8 8 1 10 0
            ;;
    esac
}

if [[ "$ACTION" == unit ]]; then run_unit; exit 0; fi
if [[ "$ACTION" == report ]]; then
    RUN_DIR="${E8_RUN_DIR:-}"
    [[ -n "$RUN_DIR" && -d "$RUN_DIR" ]] || fail 'E8_RUN_DIR must name an existing run directory'
    generate_report
    exit 0
fi

preflight
if [[ "$ACTION" == preflight ]]; then
    echo 'Argus E8 Agent-task preflight passed.'
    exit 0
fi

new_run_directory
write_manifest
snapshot_metrics "$RUN_DIR" before
trap 'stop_collector; stop_active_containers' EXIT INT TERM
run_selected_cases
snapshot_metrics "$RUN_DIR" after
generate_report
trap - EXIT INT TERM
printf 'Argus E8 result: %s\n' "$RUN_DIR"
