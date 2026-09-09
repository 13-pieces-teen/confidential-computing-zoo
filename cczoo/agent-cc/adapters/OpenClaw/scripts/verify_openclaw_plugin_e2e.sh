#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/openclaw_spiffe_common.sh"
spiffe_preflight
resolve_spiffe_plugin
OPENCLAW_CONFIG="$OPENCLAW_CONFIG_PATH"
EXPECTED_BASE_URL="$TARGET_URI"
spiffe_health
RUN_ID="${DUAL_E2E_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$RANDOM}"
[[ "$RUN_ID" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo 'Invalid RUN_ID' >&2; exit 1; }
EVIDENCE_DIR="${DUAL_E2E_EVIDENCE_DIR:-$SCRIPT_DIR/../spiffe_client/evidence/$RUN_ID}"
mkdir -p "$(dirname "$EVIDENCE_DIR")"
mkdir "$EVIDENCE_DIR"
finish() {
    local code=$?
    trap - EXIT
    python3 - "$EVIDENCE_DIR" "$code" <<'PY'
import hashlib,json,pathlib,sys
p=pathlib.Path(sys.argv[1]); code=int(sys.argv[2])
(p/'result.json').write_text(json.dumps({'result':'PASS' if code == 0 else 'FAIL','exit_code':code,
    'openclaw_tdx_remote_attestation':'NOT_RUN','scope':'real Gateway business + mTLS + recall'},indent=2)+'\n')
(p/'SHA256SUMS').write_text(''.join(hashlib.sha256(f.read_bytes()).hexdigest()+'  '+f.name+'\n' for f in sorted(p.iterdir()) if f.is_file() and f.name!='SHA256SUMS'))
PY
    exit "$code"
}
trap finish EXIT
MARKER="${DUAL_E2E_MARKER:-ARGUS-DUAL-TDVM-E2E-$RUN_ID}"
SESSION_KEY="${DUAL_E2E_SESSION_KEY:-argus-dual-tdvm-e2e-$RUN_ID}"
FACT="ARGUS_FACT_$(python3 -c 'import secrets; print(secrets.token_hex(16).upper())')"
MESSAGE="请长期记住：项目 $MARKER 的校验码是 $FACT。之后我会在另一个会话询问此项目的校验码，请保存到记忆。"
printf '%s\n' "$MARKER" >"$EVIDENCE_DIR/marker.txt"
printf '%s\n' "$FACT" >"$EVIDENCE_DIR/fact.txt"
AGENT_TIMEOUT="${DUAL_E2E_AGENT_TIMEOUT:-180}"
CAPTURE_ATTEMPTS="${DUAL_E2E_CAPTURE_ATTEMPTS:-30}"
COMMIT_ATTEMPTS="${DUAL_E2E_COMMIT_ATTEMPTS:-60}"

fail() { printf 'Dual-TDVM OpenClaw plugin E2E: FAIL: %s\n' "$1" >&2; exit 1; }
[[ -n "${OPENVIKING_API_KEY:-}" ]] || fail 'OPENVIKING_API_KEY is required'
docker inspect "$OPENCLAW_CONTAINER" >/dev/null 2>&1 || fail 'OpenClaw container is missing'

started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf '%s\n' "$MESSAGE" >"$EVIDENCE_DIR/write-prompt.txt"
agent_output="$(docker exec -u "$OPENCLAW_USER" \
    -e OPENCLAW_CONFIG_PATH="$OPENCLAW_CONFIG" \
    "$OPENCLAW_CONTAINER" openclaw agent \
    --agent main --session-key "$SESSION_KEY" --message "$MESSAGE" \
    --timeout "$AGENT_TIMEOUT" --json)" || fail 'real OpenClaw agent turn failed'
printf '%s\n' "$agent_output" >"$EVIDENCE_DIR/write-response.json"
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
    "$CAPTURE_ATTEMPTS" "$COMMIT_ATTEMPTS" "$OPENCLAW_PLUGIN_DIR" <<'NODE'
const { execFileSync } = require("node:child_process");
(async () => {
const [configPath, expectedBase, marker, runID, captureAttemptsRaw, commitAttemptsRaw, pluginDirectory] = process.argv.slice(2);
const { pathToFileURL } = require('node:url');
const { join } = require('node:path');
const { createSpiffeTransport, requestIdentity } = await import(pathToFileURL(join(pluginDirectory, 'dist/argus-spiffe/transport.mjs')));
const { OpenVikingClient } = await import(pathToFileURL(join(pluginDirectory, 'dist/client.js')));
const transport = createSpiffeTransport();
const receipts = [];
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
  throw new Error("OpenViking plugin is not configured for the NGINX HTTPS origin");
}
const headers = {
  "X-API-Key": process.env.OPENVIKING_API_KEY,
  "X-OpenViking-Actor-Peer": plugin.peer_prefix ?? "main",
};
async function request(path, options = {}) {
  const response = await transport(`${base}${path}`, {
    ...options,
    headers: { ...headers, ...(options.headers ?? {}) },
    signal: AbortSignal.timeout(30000),
  });
  const body = await response.text();
  receipts.push(requestIdentity(response));
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
const client = new OpenVikingClient(base, process.env.OPENVIKING_API_KEY, plugin.peer_prefix ?? 'main', 30000,
  plugin.accountId ?? '', plugin.userId ?? '');
const readback = await client.getSessionContext(sessionID, 128000);
if (!JSON.stringify(readback).includes(marker)) throw new Error('The installed plugin client did not read back the marker');
const commit = result(await request(`/api/v1/sessions/${encodeURIComponent(sessionID)}/commit`, {
  method: "POST",
  headers: { "Content-Type": "application/json", "X-Argus-Request-ID": `e2e-commit-${runID}` },
  body: JSON.stringify({wait: false, keep_recent_count: 0}),
}), "commit");
const taskID = commit.task_id ?? "";
let summary;
for (let attempt = 0; attempt < commitAttempts; attempt += 1) {
  const detail = result(await request(`/api/v1/sessions/${encodeURIComponent(sessionID)}`), "session detail");
  const context = result(await request(`/api/v1/sessions/${encodeURIComponent(sessionID)}/context?token_budget=128000`), "session context");
  let extractionComplete = commit.status === 'completed' || !!commit.memories_extracted;
  if (taskID) {
    const task = result(await request(`/api/v1/tasks/${encodeURIComponent(taskID)}`), "commit task");
    if (task.status === "failed") throw new Error("OpenViking commit task failed");
    extractionComplete = task.status === 'completed';
  }
  if (extractionComplete && (detail.commit_count ?? 0) > 0 && String(context.latest_archive_overview ?? "").trim()) {
    summary = { session_id: sessionID, task_id: taskID || null, commit_count: detail.commit_count, archive: true };
    break;
  }
  await sleep(5000);
}
if (!summary) throw new Error(`commit/archive did not complete for ${sessionID}`);
transport.close();
process.stdout.write(JSON.stringify({...summary, plugin_readback: true, receipts}));
})().catch((error) => { console.error(error); process.exit(1); });
NODE
)" || fail 'OpenViking marker capture or commit/archive verification failed'
printf '%s\n' "$processing_summary" >"$EVIDENCE_DIR/processing.json"

# CLI probes run via docker exec. Only gateway logs count as proof that the
# actual OpenClaw plugin transmitted this session through the native transport.
session_id="$(printf '%s' "$processing_summary" | python3 -c 'import json,sys; print(json.load(sys.stdin)["session_id"])')"
docker logs --since "$started_at" "$OPENCLAW_CONTAINER" >"$EVIDENCE_DIR/gateway-write.log" 2>&1
python3 "$SCRIPT_DIR/../spiffe_client/verify_audit.py" "$session_id" <"$EVIDENCE_DIR/gateway-write.log" >"$EVIDENCE_DIR/write-mtls.json"

# The second session sees only the lookup key, never the expected random answer.
RECALL_SESSION_KEY="$SESSION_KEY-recall"
RECALL_QUERY="请从长期记忆中查找项目 $MARKER 的校验码。仅原样回答校验码；查不到就只回答 UNKNOWN，不要猜测。"
printf '%s\n' "$RECALL_QUERY" >"$EVIDENCE_DIR/recall-prompt.txt"
docker exec -u "$OPENCLAW_USER" -e OPENCLAW_CONFIG_PATH="$OPENCLAW_CONFIG" "$OPENCLAW_CONTAINER" \
    openclaw agent --agent main --session-key "$RECALL_SESSION_KEY" --message "$RECALL_QUERY" \
    --timeout "$AGENT_TIMEOUT" --json >"$EVIDENCE_DIR/recall-response.json" || fail 'fresh-session recall turn failed'
NEGATIVE_KEY="ARGUS-ABSENT-$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
NEGATIVE_QUERY="请从长期记忆中查找项目 $NEGATIVE_KEY 的校验码。仅原样回答校验码；查不到就只回答 UNKNOWN，不要猜测。"
printf '%s\n' "$NEGATIVE_QUERY" >"$EVIDENCE_DIR/negative-prompt.txt"
docker exec -u "$OPENCLAW_USER" -e OPENCLAW_CONFIG_PATH="$OPENCLAW_CONFIG" "$OPENCLAW_CONTAINER" \
    openclaw agent --agent main --session-key "$SESSION_KEY-negative" --message "$NEGATIVE_QUERY" \
    --timeout "$AGENT_TIMEOUT" --json >"$EVIDENCE_DIR/negative-response.json" || fail 'negative-control turn failed'
docker logs --since "$started_at" "$OPENCLAW_CONTAINER" >"$EVIDENCE_DIR/gateway.log" 2>&1
python3 "$SCRIPT_DIR/../spiffe_client/verify_business.py" "$EVIDENCE_DIR" "$RECALL_SESSION_KEY" \
    >"$EVIDENCE_DIR/recall-verification.json" || fail 'recall answer/input/mTLS evidence or negative control failed'

printf '%s\n' \
    'Real OpenClaw -> native SPIFFE mTLS -> NGINX -> OpenViking plugin E2E passed.' \
    "Marker: $MARKER" \
    "OpenClaw session key: $SESSION_KEY" \
    "OpenViking processing: $processing_summary" \
    "Evidence: $EVIDENCE_DIR" \
    'Fresh-session recall and UNKNOWN negative control passed; OpenClaw remote TDX attestation remains NOT_RUN.'
