#!/usr/bin/env bash
set -euo pipefail

OPENCLAW_CONTAINER="${DUAL_OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
OPENCLAW_USER="${DUAL_OPENCLAW_USER:-node}"
OPENCLAW_CONFIG="${DUAL_OPENCLAW_CONFIG:-/home/node/.openclaw/openclaw.json}"
EXPECTED_BASE_URL="${DUAL_OPENVIKING_PLUGIN_BASE_URL:-http://argus-dual-openclaw-egress:1934}"
GUARD_CONTAINER="${DUAL_GUARD_CONTAINER:-argus-dual-openclaw-guard}"
RUN_ID="${DUAL_E2E_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$RANDOM}"
MARKER="${DUAL_E2E_MARKER:-ARGUS-DUAL-TDVM-E2E-$RUN_ID}"
SESSION_KEY="${DUAL_E2E_SESSION_KEY:-argus-dual-tdvm-e2e-$RUN_ID}"
MESSAGE="${DUAL_E2E_MESSAGE:-[ARGUS dual-TDVM E2E] 请确认收到唯一标识 $MARKER。本轮验证 OpenClaw 经 caller-local Guard 与 Egress/Ingress Broker SPIFFE mTLS 写入 OpenViking。}"
AGENT_TIMEOUT="${DUAL_E2E_AGENT_TIMEOUT:-180}"
CAPTURE_ATTEMPTS="${DUAL_E2E_CAPTURE_ATTEMPTS:-30}"
COMMIT_ATTEMPTS="${DUAL_E2E_COMMIT_ATTEMPTS:-60}"

fail() { printf 'Dual-TDVM OpenClaw plugin E2E: FAIL: %s\n' "$1" >&2; exit 1; }
[[ -n "${OPENVIKING_API_KEY:-}" ]] || fail 'OPENVIKING_API_KEY is required'
docker inspect "$OPENCLAW_CONTAINER" >/dev/null 2>&1 || fail 'OpenClaw container is missing'

started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
agent_output="$(docker exec -u "$OPENCLAW_USER" \
    -e OPENCLAW_CONFIG_PATH="$OPENCLAW_CONFIG" \
    "$OPENCLAW_CONTAINER" openclaw agent \
    --agent main --session-key "$SESSION_KEY" --message "$MESSAGE" \
    --timeout "$AGENT_TIMEOUT" --json)" || fail 'real OpenClaw agent turn failed'
printf '%s' "$agent_output" | docker exec -i -u "$OPENCLAW_USER" "$OPENCLAW_CONTAINER" \
    node -e '
const fs = require("fs");
const value = JSON.parse(fs.readFileSync(0, "utf8"));
if (value.status !== "ok" || !value.runId || ![undefined, null, "", false].includes(value.error)) {
  throw new Error("OpenClaw agent did not return a successful JSON result");
}
'

processing_summary="$(docker exec -i -u "$OPENCLAW_USER" -e OPENVIKING_API_KEY \
    "$OPENCLAW_CONTAINER" node - \
    "$OPENCLAW_CONFIG" "$EXPECTED_BASE_URL" "$MARKER" "$RUN_ID" \
    "$CAPTURE_ATTEMPTS" "$COMMIT_ATTEMPTS" <<'NODE'
const { execFileSync } = require("node:child_process");
(async () => {
const [configPath, expectedBase, marker, runID, captureAttemptsRaw, commitAttemptsRaw] = process.argv.slice(2);
const captureAttempts = Number(captureAttemptsRaw);
const commitAttempts = Number(commitAttemptsRaw);
const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
function config(path) {
  return JSON.parse(execFileSync("openclaw", ["config", "get", path, "--json"], {
    encoding: "utf8", env: { ...process.env, OPENCLAW_CONFIG_PATH: configPath },
  }).trim());
}
const plugin = config("plugins.entries.openviking.config");
const slot = config("plugins.slots.contextEngine");
const base = plugin?.baseUrl?.replace(/\/+$/, "");
if (slot !== "openviking" || plugin?.mode !== "remote" || base !== expectedBase) {
  throw new Error("OpenViking plugin is not configured for the local Egress Broker");
}
const headers = {
  "X-API-Key": process.env.OPENVIKING_API_KEY,
  "X-OpenViking-Actor-Peer": plugin.peer_prefix ?? "main",
};
async function request(path, options = {}) {
  const response = await fetch(`${base}${path}`, {
    ...options,
    headers: { ...headers, ...(options.headers ?? {}) },
    signal: AbortSignal.timeout(30000),
  });
  const body = await response.text();
  if (!response.ok) throw new Error(`${path} returned HTTP ${response.status}: ${body.slice(0, 512)}`);
  return body ? JSON.parse(body) : {};
}
function result(payload, label) {
  if (payload?.status !== "ok" || typeof payload.result !== "object" || !payload.result) {
    throw new Error(`${label} did not return an ok result`);
  }
  return payload.result;
}
let sessionID = "";
for (let attempt = 0; attempt < captureAttempts && !sessionID; attempt += 1) {
  const sessions = result(await request("/api/v1/sessions"), "sessions");
  const recentSessions = sessions
    .slice()
    .sort((a, b) => String(b.mod_time ?? "").localeCompare(String(a.mod_time ?? "")))
    .slice(0, 100);
  for (const session of recentSessions) {
    const candidate = String(session.session_id ?? "");
    if (!candidate || candidate.startsWith("memory-store-")) continue;
    let context;
    try {
      context = await request(`/api/v1/sessions/${encodeURIComponent(candidate)}/context?token_budget=128000`);
    } catch (error) {
      if (String(error?.message ?? error).includes("returned HTTP 404")) continue;
      throw error;
    }
    if (JSON.stringify(context).includes(marker)) { sessionID = candidate; break; }
  }
  if (!sessionID) await sleep(2000);
}
if (!sessionID) throw new Error(`marker ${marker} was not captured by OpenViking`);
const commit = result(await request(`/api/v1/sessions/${encodeURIComponent(sessionID)}/commit`, {
  method: "POST",
  headers: { "Content-Type": "application/json", "X-Argus-Request-ID": `e2e-commit-${runID}` },
  body: "{}",
}), "commit");
const taskID = commit.task_id ?? "";
let summary;
for (let attempt = 0; attempt < commitAttempts; attempt += 1) {
  const detail = result(await request(`/api/v1/sessions/${encodeURIComponent(sessionID)}`), "session detail");
  const context = result(await request(`/api/v1/sessions/${encodeURIComponent(sessionID)}/context?token_budget=128000`), "session context");
  if (taskID) {
    const task = result(await request(`/api/v1/tasks/${encodeURIComponent(taskID)}`), "commit task");
    if (task.status === "failed") throw new Error("OpenViking commit task failed");
  }
  if ((detail.commit_count ?? 0) > 0 && String(context.latest_archive_overview ?? "").trim()) {
    summary = { session_id: sessionID, task_id: taskID || null, commit_count: detail.commit_count, archive: true };
    break;
  }
  await sleep(5000);
}
if (!summary) throw new Error(`commit/archive did not complete for ${sessionID}`);
process.stdout.write(JSON.stringify(summary));
})().catch((error) => { console.error(error); process.exit(1); });
NODE
)" || fail 'OpenViking marker capture or commit/archive verification failed'

guard_logs="$(docker logs --since "$started_at" "$GUARD_CONTAINER" 2>&1)"
printf '%s' "$guard_logs" | grep -F 'caller-local SPIFFE authorization decision' | grep -F 'decision=Allow' >/dev/null \
    || fail 'no matching Guard ALLOW audit decision was observed for the E2E turn'

printf '%s\n' \
    'Real OpenClaw -> Egress Broker -> OpenViking Ingress Broker plugin E2E passed.' \
    "Marker: $MARKER" \
    "OpenClaw session key: $SESSION_KEY" \
    "OpenViking processing: $processing_summary" \
    'The business turn was Guard-authorized, transmitted through SPIFFE mTLS, captured, committed, and archived.'
