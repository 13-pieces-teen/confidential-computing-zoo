#!/usr/bin/env bash
set -euo pipefail

OPENCLAW_CONTAINER="${V2_REAL_OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
OPENCLAW_USER="${V2_REAL_OPENCLAW_USER:-node}"
OPENCLAW_CONFIG="${V2_REAL_OPENCLAW_CONFIG:-/home/node/.openclaw/openclaw.json}"
MTLS_CONTAINER="${V2_OPENCLAW_MTLS_CONTAINER:-argus-v2-openclaw-mtls}"
GUARD_CONTAINER="${V2_GUARD_CONTAINER:-argus-v2-guard}"
PROXY_BIND="${V2_OPENCLAW_PROXY_BIND:-172.31.44.1}"
PROXY_PORT="${V2_OPENCLAW_PROXY_PORT:-1934}"
EGRESS_IP="${V2_OPENCLAW_EGRESS_IP:-172.31.44.2}"
EXPECTED_BASE_URL="${V2_OPENCLAW_PROXY_URL:-http://$PROXY_BIND:$PROXY_PORT}"
CAPTURE_ATTEMPTS="${V2_E2E_CAPTURE_ATTEMPTS:-30}"
CAPTURE_INTERVAL="${V2_E2E_CAPTURE_INTERVAL:-2}"
SESSION_SCAN_LIMIT="${V2_E2E_SESSION_SCAN_LIMIT:-100}"
COMMIT_ATTEMPTS="${V2_E2E_COMMIT_ATTEMPTS:-60}"
COMMIT_INTERVAL="${V2_E2E_COMMIT_INTERVAL:-5}"
AGENT_TIMEOUT="${V2_E2E_AGENT_TIMEOUT:-180}"
REQUIRE_MEMORY="${V2_E2E_REQUIRE_MEMORY:-0}"
RUN_ID="${V2_E2E_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$RANDOM}"
MARKER="${V2_E2E_MARKER:-ARGUS-MTLS-E2E-$RUN_ID}"
SESSION_KEY="${V2_E2E_SESSION_KEY:-argus-mtls-e2e-$RUN_ID}"
MESSAGE="${V2_E2E_MESSAGE:-[ARGUS-SPIFFE-V2-E2E] 请确认收到本轮消息。唯一标识是 $MARKER。本轮用于验证真实 OpenClaw context-engine 消息经过 SPIFFE mTLS 写入 OpenViking。}"

fail() {
    printf 'OpenClaw plugin E2E: FAIL: %s\n' "$1" >&2
    exit 1
}

[[ -n "${OPENVIKING_API_KEY:-}" ]] \
    || fail 'OPENVIKING_API_KEY must be set for authenticated E2E inspection'

[[ "$OPENCLAW_CONTAINER" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] \
    || fail "invalid real OpenClaw container name: $OPENCLAW_CONTAINER"
[[ "$MTLS_CONTAINER" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] \
    || fail "invalid mTLS client container name: $MTLS_CONTAINER"
for numeric in \
    "$CAPTURE_ATTEMPTS" \
    "$CAPTURE_INTERVAL" \
    "$SESSION_SCAN_LIMIT" \
    "$COMMIT_ATTEMPTS" \
    "$COMMIT_INTERVAL" \
    "$AGENT_TIMEOUT"; do
    [[ "$numeric" =~ ^[1-9][0-9]*$ ]] \
        || fail "poll and timeout values must be positive integers: $numeric"
done
[[ "$REQUIRE_MEMORY" == 0 || "$REQUIRE_MEMORY" == 1 ]] \
    || fail "V2_E2E_REQUIRE_MEMORY must be 0 or 1"
[[ "$RUN_ID" =~ ^[A-Za-z0-9._-]+$ ]] \
    || fail "V2_E2E_RUN_ID contains unsupported characters"
[[ "$SESSION_KEY" =~ ^[A-Za-z0-9._:-]+$ ]] \
    || fail "V2_E2E_SESSION_KEY contains unsupported characters"
if ! python3 - "$EGRESS_IP" <<'PY'
import ipaddress
import sys

ipaddress.ip_address(sys.argv[1])
PY
then
    fail "V2_OPENCLAW_EGRESS_IP is not a valid IP address: $EGRESS_IP"
fi

docker inspect "$OPENCLAW_CONTAINER" >/dev/null 2>&1 \
    || fail "real OpenClaw container does not exist: $OPENCLAW_CONTAINER"
docker inspect "$MTLS_CONTAINER" >/dev/null 2>&1 \
    || fail "mTLS client container does not exist: $MTLS_CONTAINER"
[[ "$(docker inspect "$OPENCLAW_CONTAINER" --format '{{.State.Running}}')" == true ]] \
    || fail "real OpenClaw container is not running: $OPENCLAW_CONTAINER"
[[ "$(docker inspect "$MTLS_CONTAINER" --format '{{.State.Running}}')" == true ]] \
    || fail "mTLS client container is not running: $MTLS_CONTAINER"

docker exec -i -u "$OPENCLAW_USER" -e OPENVIKING_API_KEY \
        "$OPENCLAW_CONTAINER" \
    node - "$OPENCLAW_CONFIG" "$EXPECTED_BASE_URL" <<'NODE'
const { execFileSync } = require("node:child_process");

const configPath = process.argv[2];
const expectedBaseUrl = process.argv[3].replace(/\/+$/, "");

function readOpenClawConfig(path) {
  const output = execFileSync(
    "openclaw",
    ["config", "get", path, "--json"],
    {
      encoding: "utf8",
      env: { ...process.env, OPENCLAW_CONFIG_PATH: configPath },
      stdio: ["ignore", "pipe", "inherit"],
    },
  ).trim();
  if (!output) {
    throw new Error(`openclaw config get ${path} returned empty output`);
  }
  return JSON.parse(output);
}

const slot = readOpenClawConfig("plugins.slots.contextEngine");
const plugin = readOpenClawConfig("plugins.entries.openviking.config");

if (slot !== "openviking") {
  throw new Error(`plugins.slots.contextEngine is ${JSON.stringify(slot)}, expected "openviking"`);
}
if (!plugin || typeof plugin !== "object" || Array.isArray(plugin) || plugin.mode !== "remote") {
  throw new Error("OpenViking plugin is not configured in remote mode");
}
if (
  typeof plugin.baseUrl !== "string"
  || plugin.baseUrl.replace(/\/+$/, "") !== expectedBaseUrl
) {
  throw new Error(
    `OpenViking baseUrl is ${JSON.stringify(plugin?.baseUrl)}, expected ${expectedBaseUrl}`,
  );
}
if (typeof plugin.apiKey !== "string" || !plugin.apiKey) {
  throw new Error("OpenViking plugin API key is missing");
}

async function main() {
  const response = await fetch(`${expectedBaseUrl}/health`, {
    headers: { "X-Argus-Request-ID": "openclaw-e2e-preflight" },
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) {
    throw new Error(`mTLS egress health returned HTTP ${response.status}`);
  }
  if (!/^[0-9a-f]{24}$/.test(response.headers.get("x-argus-request-id") ?? "")) {
    throw new Error("mTLS egress preflight has no generated request ID");
  }
  if (!/^[0-9a-f]{32}$/.test(response.headers.get("x-argus-decision-id") ?? "")) {
    throw new Error("mTLS egress preflight has no Guard decision ID");
  }
  if (!/^sha256:[0-9a-f]{64}$/.test(response.headers.get("x-argus-request-digest") ?? "")) {
    throw new Error("mTLS egress preflight has no Guard-bound request digest");
  }
  if (response.headers.get("x-argus-verification-mode") !== "mock_allow") {
    throw new Error("mTLS egress preflight did not declare mock_allow");
  }
  console.log(`OpenClaw plugin preflight passed: ${expectedBaseUrl}`);
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
NODE

started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if ! agent_output="$(
    docker exec -u "$OPENCLAW_USER" \
        -e OPENCLAW_CONFIG_PATH="$OPENCLAW_CONFIG" \
        "$OPENCLAW_CONTAINER" \
        openclaw agent \
        --agent main \
        --session-key "$SESSION_KEY" \
        --message "$MESSAGE" \
        --timeout "$AGENT_TIMEOUT" \
        --json
)"; then
    fail 'real OpenClaw agent turn failed'
fi
[[ -n "$agent_output" ]] || fail 'real OpenClaw agent turn returned empty output'
if ! agent_run_id="$(
    printf '%s' "$agent_output" | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
if not isinstance(payload, dict):
    raise SystemExit("agent JSON output is not an object")
status = payload.get("status")
if status != "ok":
    raise SystemExit(f"agent JSON output status is not ok: {status!r}")
run_id = payload.get("runId")
if not isinstance(run_id, str) or not run_id.strip():
    raise SystemExit("agent JSON output has no non-empty runId")
result = payload.get("result")
if not isinstance(result, dict):
    raise SystemExit("agent JSON output has no result object")
if payload.get("error") not in (None, "", False):
    raise SystemExit("agent JSON output contains an error")
print(run_id.strip())
'
)"; then
    fail 'real OpenClaw agent turn did not return the successful gateway JSON envelope'
fi
[[ -n "$agent_run_id" ]] || fail 'real OpenClaw agent turn returned an empty runId'
printf 'Real OpenClaw agent turn completed: session_key=%s run_id=%s output_chars=%s\n' \
    "$SESSION_KEY" "$agent_run_id" "${#agent_output}"

if ! agent_turn_proxy_logs="$(
    docker logs --since "$started_at" "$MTLS_CONTAINER" 2>&1
)"; then
    fail 'could not read mTLS proxy logs for the real OpenClaw agent turn'
fi
agent_write_evidence="$(
    printf '%s\n' "$agent_turn_proxy_logs" \
        | EXPECTED_SOURCE_IP="$EGRESS_IP" python3 -c '
import os
import re
import sys

expected_source_ip = os.environ["EXPECTED_SOURCE_IP"]
write_methods = {"POST", "PUT", "PATCH", "DELETE"}
helper_prefixes = ("e2e-scan-", "e2e-commit-", "e2e-inspect-")

for raw_line in sys.stdin:
    line = raw_line.rstrip()
    fields = dict(re.findall(r"(?:^|\s)([a-z_]+)=([^\s]+)", line))
    client_request_id = fields.get("client_request_id", "")
    try:
        status = int(fields.get("status", "0"))
    except ValueError:
        status = 0
    if (
        fields.get("decision") == "forwarded_mtls"
        and fields.get("source_ip") == expected_source_ip
        and fields.get("method") in write_methods
        and fields.get("path", "").startswith("/api/v1/")
        and 200 <= status < 300
        and not client_request_id.startswith(helper_prefixes)
        and re.fullmatch(r"[0-9a-f]{24}", fields.get("request_id", ""))
        and re.fullmatch(r"[0-9a-f]{32}", fields.get("guard_decision_id", ""))
        and re.fullmatch(r"sha256:[0-9a-f]{64}", fields.get("request_digest", ""))
        and fields.get("verification_mode") == "mock_allow"
    ):
        print(line)
'
)"
if [[ -z "$agent_write_evidence" ]]; then
    fail 'real OpenClaw agent turn produced no attributable OpenViking write through the mTLS proxy'
fi
agent_write_count="$(
    printf '%s\n' "$agent_write_evidence" \
        | awk 'NF { count += 1 } END { print count + 0 }'
)"
agent_write_first="${agent_write_evidence%%$'\n'*}"
printf 'Real OpenClaw agent-turn mTLS write evidence: count=%s\n%s\n' \
    "$agent_write_count" "$agent_write_first"

scan_marker() {
    docker exec -i -u "$OPENCLAW_USER" -e OPENVIKING_API_KEY \
        "$OPENCLAW_CONTAINER" \
        node - "$OPENCLAW_CONFIG" "$MARKER" "$RUN_ID" "$SESSION_SCAN_LIMIT" <<'NODE'
const { execFileSync } = require("node:child_process");

const configPath = process.argv[2];
const marker = process.argv[3];
const runID = process.argv[4];
const sessionScanLimit = Number(process.argv[5]);

function readOpenClawConfig(path) {
  const output = execFileSync(
    "openclaw",
    ["config", "get", path, "--json"],
    {
      encoding: "utf8",
      env: { ...process.env, OPENCLAW_CONFIG_PATH: configPath },
      stdio: ["ignore", "pipe", "inherit"],
    },
  ).trim();
  if (!output) {
    throw new Error(`openclaw config get ${path} returned empty output`);
  }
  return JSON.parse(output);
}

const plugin = readOpenClawConfig("plugins.entries.openviking.config");
const baseUrl =
  typeof plugin?.baseUrl === "string" ? plugin.baseUrl.replace(/\/+$/, "") : "";
const apiKey = process.env.OPENVIKING_API_KEY;
const actorPeerValue = plugin?.peer_prefix ?? "main";
if (
  !baseUrl
  || typeof apiKey !== "string"
  || !apiKey
  || typeof actorPeerValue !== "string"
  || !actorPeerValue.trim()
) {
  throw new Error("OpenViking remote plugin configuration is incomplete");
}
const actorPeer = actorPeerValue.trim();
if (!Number.isSafeInteger(sessionScanLimit) || sessionScanLimit <= 0) {
  throw new Error("session scan limit must be a positive integer");
}

const headers = {
  "X-API-Key": apiKey,
  "X-OpenViking-Actor-Peer": actorPeer,
  "X-Argus-Request-ID": `e2e-scan-${runID}`.slice(0, 128),
};

class HTTPResponseError extends Error {
  constructor(path, status, body) {
    super(`${path} returned HTTP ${status}: ${body.slice(0, 512)}`);
    this.status = status;
  }
}

async function fetchJSON(path, options = {}) {
  const response = await fetch(`${baseUrl}${path}`, {
    ...options,
    headers: { ...headers, ...(options.headers || {}) },
    signal: AbortSignal.timeout(10000),
  });
  const body = await response.text();
  if (!response.ok) {
    throw new HTTPResponseError(path, response.status, body);
  }
  return body ? JSON.parse(body) : {};
}

async function main() {
  const listing = await fetchJSON("/api/v1/sessions");
  if (listing?.status !== "ok" || !Array.isArray(listing.result)) {
    throw new Error("OpenViking sessions response does not contain an ok result list");
  }
  const sessions = listing.result
    .filter((item) => item && typeof item === "object" && !Array.isArray(item))
    .filter((item) => {
      const sessionID = String(item.session_id || "").trim();
      return sessionID && !sessionID.startsWith("memory-store-");
    })
    .sort((left, right) => {
      const leftTime = String(left.updated_at || left.created_at || "");
      const rightTime = String(right.updated_at || right.created_at || "");
      return rightTime.localeCompare(leftTime);
    })
    .slice(0, sessionScanLimit);

  for (const session of sessions) {
    const sessionID = String(session.session_id).trim();
    try {
      const context = await fetchJSON(
        `/api/v1/sessions/${encodeURIComponent(sessionID)}/context?token_budget=128000`,
      );
      if (JSON.stringify(context).includes(marker)) {
        process.stdout.write(sessionID);
        return;
      }
    } catch (error) {
      if (error instanceof HTTPResponseError && error.status === 404) {
        continue;
      }
      throw error;
    }
  }
  process.exit(4);
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
NODE
}

openviking_session_id=""
for _ in $(seq 1 "$CAPTURE_ATTEMPTS"); do
    scan_status=0
    if openviking_session_id="$(scan_marker)" \
        && [[ -n "$openviking_session_id" ]]; then
        break
    else
        scan_status=$?
    fi
    if [[ "$scan_status" -ne 4 ]]; then
        fail "OpenViking session scan failed with status $scan_status"
    fi
    read -r -t "$CAPTURE_INTERVAL" _ || true
done
if [[ -z "$openviking_session_id" ]]; then
    fail "OpenViking session capture did not contain marker $MARKER"
fi
printf 'OpenViking captured the real turn: session_id=%s marker=%s\n' \
    "$openviking_session_id" "$MARKER"

session_write_evidence="$(
    printf '%s\n' "$agent_write_evidence" \
        | EXPECTED_SESSION_ID="$openviking_session_id" python3 -c '
import os
import re
import sys
from urllib.parse import quote

session_id = os.environ["EXPECTED_SESSION_ID"]
encoded_session_id = quote(session_id, safe="")
expected_path = f"/api/v1/sessions/{encoded_session_id}/messages"
for raw_line in sys.stdin:
    line = raw_line.rstrip()
    fields = dict(re.findall(r"(?:^|\s)([a-z_]+)=([^\s]+)", line))
    if fields.get("path") == expected_path:
        print(line)
'
)"
if [[ -z "$session_write_evidence" ]]; then
    fail "agent-turn write evidence is not linked to captured OpenViking session $openviking_session_id"
fi
session_write_first="${session_write_evidence%%$'\n'*}"
printf 'Captured-session mTLS write evidence:\n%s\n' "$session_write_first"
read -r session_request_id session_decision_id session_request_digest < <(
    printf '%s\n' "$session_write_first" | python3 -c '
import re
import sys

line = sys.stdin.read().strip()
fields = dict(re.findall(r"(?:^|\s)([a-z_]+)=([^\s]+)", line))
print(
    fields.get("request_id", ""),
    fields.get("guard_decision_id", ""),
    fields.get("request_digest", ""),
)
'
)
if [[ ! "$session_request_id" =~ ^[0-9a-f]{24}$ ]] \
    || [[ ! "$session_decision_id" =~ ^[0-9a-f]{32}$ ]] \
    || [[ ! "$session_request_digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    fail 'captured-session proxy evidence has an invalid Guard decision receipt'
fi
session_guard_evidence=""
for _ in 1 2 3 4 5; do
    if ! guard_logs="$(docker logs --since "$started_at" "$GUARD_CONTAINER" 2>&1)"; then
        fail 'could not read Guard logs for the captured OpenViking session'
    fi
    session_guard_evidence="$(
        printf '%s\n' "$guard_logs" \
            | grep -F "request_id=$session_request_id" \
            | grep -F "request_digest=$session_request_digest" \
            | grep -F "decision_id=$session_decision_id" \
            | grep -F 'Guard returned mock ALLOW' \
            | tail -n 1 || true
    )"
    [[ -n "$session_guard_evidence" ]] && break
    read -r -t 1 _ || true
done
if [[ -z "$session_guard_evidence" ]]; then
    fail 'captured OpenViking session write has no matching Guard ALLOW receipt'
fi
printf 'Captured-session Guard causal evidence:\n%s\n' "$session_guard_evidence"

task_id="$(
    docker exec -i -u "$OPENCLAW_USER" -e OPENVIKING_API_KEY \
        "$OPENCLAW_CONTAINER" \
        node - "$OPENCLAW_CONFIG" "$openviking_session_id" "$RUN_ID" <<'NODE'
const { execFileSync } = require("node:child_process");

const configPath = process.argv[2];
const sessionID = process.argv[3];
const runID = process.argv[4];

function readOpenClawConfig(path) {
  const output = execFileSync(
    "openclaw",
    ["config", "get", path, "--json"],
    {
      encoding: "utf8",
      env: { ...process.env, OPENCLAW_CONFIG_PATH: configPath },
      stdio: ["ignore", "pipe", "inherit"],
    },
  ).trim();
  if (!output) {
    throw new Error(`openclaw config get ${path} returned empty output`);
  }
  return JSON.parse(output);
}

const plugin = readOpenClawConfig("plugins.entries.openviking.config");
const baseUrl =
  typeof plugin?.baseUrl === "string" ? plugin.baseUrl.replace(/\/+$/, "") : "";
const apiKey = process.env.OPENVIKING_API_KEY;
const actorPeerValue = plugin?.peer_prefix ?? "main";
if (
  !baseUrl
  || typeof apiKey !== "string"
  || !apiKey
  || typeof actorPeerValue !== "string"
  || !actorPeerValue.trim()
) {
  throw new Error("OpenViking remote plugin configuration is incomplete");
}
const actorPeer = actorPeerValue.trim();

async function main() {
  const response = await fetch(
    `${baseUrl}/api/v1/sessions/${encodeURIComponent(sessionID)}/commit`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": apiKey,
        "X-OpenViking-Actor-Peer": actorPeer,
        "X-Argus-Request-ID": `e2e-commit-${runID}`.slice(0, 128),
      },
      body: "{}",
      signal: AbortSignal.timeout(30000),
    },
  );
  const body = await response.text();
  if (!response.ok) {
    throw new Error(`session commit returned HTTP ${response.status}: ${body.slice(0, 512)}`);
  }
  const payload = body ? JSON.parse(body) : {};
  if (
    payload?.status !== "ok"
    || !payload.result
    || typeof payload.result !== "object"
    || Array.isArray(payload.result)
  ) {
    throw new Error("session commit response does not contain an ok result object");
  }
  const taskID = payload.result.task_id;
  if (taskID != null && typeof taskID !== "string") {
    throw new Error("session commit task_id is not a string");
  }
  process.stdout.write(taskID || "");
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
NODE
)"
printf 'OpenViking session commit accepted: task_id=%s\n' "${task_id:-not-returned}"

inspect_processing() {
    docker exec -i -u "$OPENCLAW_USER" -e OPENVIKING_API_KEY \
        "$OPENCLAW_CONTAINER" \
        node - \
        "$OPENCLAW_CONFIG" \
        "$openviking_session_id" \
        "$task_id" \
        "$REQUIRE_MEMORY" \
        "$RUN_ID" <<'NODE'
const { execFileSync } = require("node:child_process");

const configPath = process.argv[2];
const sessionID = process.argv[3];
const taskID = process.argv[4];
const requireMemory = process.argv[5] === "1";
const runID = process.argv[6];

function readOpenClawConfig(path) {
  const output = execFileSync(
    "openclaw",
    ["config", "get", path, "--json"],
    {
      encoding: "utf8",
      env: { ...process.env, OPENCLAW_CONFIG_PATH: configPath },
      stdio: ["ignore", "pipe", "inherit"],
    },
  ).trim();
  if (!output) {
    throw new Error(`openclaw config get ${path} returned empty output`);
  }
  return JSON.parse(output);
}

const plugin = readOpenClawConfig("plugins.entries.openviking.config");
const baseUrl =
  typeof plugin?.baseUrl === "string" ? plugin.baseUrl.replace(/\/+$/, "") : "";
const apiKey = process.env.OPENVIKING_API_KEY;
const actorPeerValue = plugin?.peer_prefix ?? "main";
if (
  !baseUrl
  || typeof apiKey !== "string"
  || !apiKey
  || typeof actorPeerValue !== "string"
  || !actorPeerValue.trim()
) {
  throw new Error("OpenViking remote plugin configuration is incomplete");
}
const actorPeer = actorPeerValue.trim();
const headers = {
  "X-API-Key": apiKey,
  "X-OpenViking-Actor-Peer": actorPeer,
  "X-Argus-Request-ID": `e2e-inspect-${runID}`.slice(0, 128),
};

async function fetchJSON(path) {
  const response = await fetch(`${baseUrl}${path}`, {
    headers,
    signal: AbortSignal.timeout(10000),
  });
  const body = await response.text();
  if (!response.ok) {
    throw new Error(`${path} returned HTTP ${response.status}: ${body.slice(0, 512)}`);
  }
  return body ? JSON.parse(body) : {};
}

function extractResult(payload, label) {
  if (
    payload?.status !== "ok"
    || !payload.result
    || typeof payload.result !== "object"
    || Array.isArray(payload.result)
  ) {
    throw new Error(`${label} response does not contain an ok result object`);
  }
  return payload.result;
}

function memoryTotal(detail) {
  const memories = detail.memories_extracted;
  if (!memories || typeof memories !== "object" || Array.isArray(memories)) {
    return 0;
  }
  if (Number.isInteger(memories.total)) {
    return memories.total;
  }
  return Object.values(memories).reduce(
    (sum, value) => sum + (Number.isInteger(value) ? value : 0),
    0,
  );
}

async function main() {
  const detailPayload = await fetchJSON(
    `/api/v1/sessions/${encodeURIComponent(sessionID)}`,
  );
  const contextPayload = await fetchJSON(
    `/api/v1/sessions/${encodeURIComponent(sessionID)}/context?token_budget=128000`,
  );
  const detail = extractResult(detailPayload, "session detail");
  const context = extractResult(contextPayload, "session context");
  const commitCount = Number.isInteger(detail.commit_count) ? detail.commit_count : 0;
  const hasArchive =
    typeof context.latest_archive_overview === "string"
    && context.latest_archive_overview.trim().length > 0;

  if (taskID) {
    const taskPayload = await fetchJSON(`/api/v1/tasks/${encodeURIComponent(taskID)}`);
    const task = extractResult(taskPayload, "commit task");
    if (task.status === "failed") {
      throw new Error(`OpenViking commit task failed: ${JSON.stringify(task).slice(0, 512)}`);
    }
  }
  const memoryCount = memoryTotal(detail);
  const ready = commitCount > 0 && hasArchive && (!requireMemory || memoryCount > 0);
  if (!ready) process.exit(4);
  process.stdout.write(JSON.stringify({ commit_count: commitCount, archive: true, memory_count: memoryCount }));
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
NODE
}

processing_summary=""
for _ in $(seq 1 "$COMMIT_ATTEMPTS"); do
    processing_status=0
    if processing_summary="$(inspect_processing)" \
        && [[ -n "$processing_summary" ]]; then
        break
    else
        processing_status=$?
    fi
    if [[ "$processing_status" -ne 4 ]]; then
        fail "OpenViking processing inspection failed with status $processing_status"
    fi
    read -r -t "$COMMIT_INTERVAL" _ || true
done
if [[ -z "$processing_summary" ]]; then
    fail "OpenViking commit/archive processing did not complete for $openviking_session_id"
fi

printf '%s\n' \
    'Real OpenClaw -> OpenViking plugin E2E passed.' \
    "Marker: $MARKER" \
    "OpenClaw session key: $SESSION_KEY" \
    "OpenViking session ID: $openviking_session_id" \
    "Processing: $processing_summary" \
    'Agent-turn proxy evidence: allowed source obtained a matching Guard decision receipt and wrote the captured session /messages endpoint before E2E inspection traffic.' \
    'The unique marker was subsequently captured and archived through the SPIFFE mTLS egress.' \
    'Memory extraction was required only when V2_E2E_REQUIRE_MEMORY=1.'
