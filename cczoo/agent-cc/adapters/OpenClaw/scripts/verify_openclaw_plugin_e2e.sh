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
MODE="new"
if [[ "${1:-}" == --resume && $# == 2 ]]; then
    MODE="resume"
    EVIDENCE_DIR="$2"
    [[ -f "$EVIDENCE_DIR/run.json" && -f "$EVIDENCE_DIR/processing-events.jsonl" ]] || { echo 'Resume requires run.json and processing-events.jsonl' >&2; exit 1; }
    mapfile -t run_values < <(python3 - "$EVIDENCE_DIR/run.json" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))
for k in ('run_id','marker','session_key','agent_id','started_at'): print(r[k])
PY
)
    RUN_ID="${run_values[0]}"; MARKER="${run_values[1]}"; SESSION_KEY="${run_values[2]}"
    AGENT_ID="${run_values[3]}"; started_at="${run_values[4]}"
else
    [[ $# == 0 ]] || { echo 'Usage: verify_openclaw_plugin_e2e.sh [--resume EVIDENCE_DIR]' >&2; exit 1; }
    RUN_ID="${DUAL_E2E_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$RANDOM}"
    [[ "$RUN_ID" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo 'Invalid RUN_ID' >&2; exit 1; }
    EVIDENCE_DIR="${DUAL_E2E_EVIDENCE_DIR:-$SCRIPT_DIR/../spiffe_client/evidence/$RUN_ID}"
    mkdir -p "$(dirname "$EVIDENCE_DIR")"
    mkdir "$EVIDENCE_DIR"
    MARKER="${DUAL_E2E_MARKER:-ARGUS-DUAL-TDVM-E2E-$RUN_ID}"
    SESSION_KEY="${DUAL_E2E_SESSION_KEY:-argus-dual-tdvm-e2e-$RUN_ID}"
    AGENT_ID="${DUAL_E2E_AGENT_ID:-main}"
    started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    python3 - "$EVIDENCE_DIR" "$RUN_ID" "$MARKER" "$SESSION_KEY" "$AGENT_ID" "$started_at" <<'PY'
import json,pathlib,secrets,sys
p=pathlib.Path(sys.argv[1]); keys=('run_id','marker','session_key','agent_id','started_at')
(p/'run.json').write_text(json.dumps(dict(zip(keys,sys.argv[2:])),indent=2)+'\n')
(p/'fact.txt').write_text('ARGUS_FACT_'+secrets.token_hex(16).upper()+'\n')
(p/'marker.txt').write_text(sys.argv[3]+'\n')
PY
fi
FACT="$(cat "$EVIDENCE_DIR/fact.txt")"
finish() {
    local code=$?
    trap - EXIT
    docker logs --since "$started_at" "$OPENCLAW_CONTAINER" >"$EVIDENCE_DIR/gateway.log" 2>&1 || true
    python3 "$SCRIPT_DIR/../spiffe_client/business_evidence.py" finish "$EVIDENCE_DIR" "$code" || code=1
    exit "$code"
}
trap finish EXIT
AGENT_TIMEOUT="${DUAL_E2E_AGENT_TIMEOUT:-180}"
CAPTURE_ATTEMPTS="${DUAL_E2E_CAPTURE_ATTEMPTS:-30}"
COMMIT_ATTEMPTS="${DUAL_E2E_COMMIT_ATTEMPTS:-60}"

fail() { printf 'Dual-TDVM OpenClaw plugin E2E: FAIL: %s\n' "$1" >&2; exit 1; }
[[ -n "${OPENVIKING_API_KEY:-}" ]] || fail 'OPENVIKING_API_KEY is required'
docker inspect "$OPENCLAW_CONTAINER" >/dev/null 2>&1 || fail 'OpenClaw container is missing'

oc node - "$OPENVIKING_SPIFFE_CONFIG" <<'NODE' >"$EVIDENCE_DIR/identity.json"
const c=JSON.parse(require('node:fs').readFileSync(process.argv[2],'utf8'));
console.log(JSON.stringify({client_spiffe_id:c.clientSpiffeId,server_spiffe_id:c.serverSpiffeId}));
NODE
CLIENT_ID="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["client_spiffe_id"])' "$EVIDENCE_DIR/identity.json")"
SERVER_ID="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["server_spiffe_id"])' "$EVIDENCE_DIR/identity.json")"
if [[ "$MODE" == new ]]; then
MESSAGE="请长期记住：项目 $MARKER 的校验码是 $FACT。之后我会在另一个会话询问此项目的校验码，请保存到记忆。"
printf '%s\n' "$MESSAGE" >"$EVIDENCE_DIR/write-prompt.txt"
agent_output="$(docker exec -u "$OPENCLAW_USER" \
    -e OPENCLAW_CONFIG_PATH="$OPENCLAW_CONFIG" \
    "$OPENCLAW_CONTAINER" openclaw agent \
    --agent "$AGENT_ID" --session-key "$SESSION_KEY" --message "$MESSAGE" \
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

fi
python3 "$SCRIPT_DIR/../spiffe_client/business_evidence.py" resume "$EVIDENCE_DIR" >"$EVIDENCE_DIR/resume.json"
processing_code=0
oc node "$OPENCLAW_PLUGIN_DIR/dist/argus-spiffe/business-cli.mjs" \
    "$OPENCLAW_CONFIG" "$EXPECTED_BASE_URL" "$MARKER" "$AGENT_ID" "$SESSION_KEY" \
    "$CAPTURE_ATTEMPTS" "$COMMIT_ATTEMPTS" "$MODE" \
    <"$EVIDENCE_DIR/resume.json" | tee -a "$EVIDENCE_DIR/processing-events.jsonl" || processing_code=$?
python3 "$SCRIPT_DIR/../spiffe_client/business_evidence.py" resume "$EVIDENCE_DIR" >"$EVIDENCE_DIR/processing.json"
[[ "$processing_code" == 0 ]] || fail 'processing failed; inspect result.json; --resume never repeats commit POST'
processing_summary="$(cat "$EVIDENCE_DIR/processing.json")"

# CLI probes run via docker exec. Only gateway logs count as proof that the
# actual OpenClaw plugin transmitted this session through the native transport.
session_id="$(printf '%s' "$processing_summary" | python3 -c 'import json,sys; print(json.load(sys.stdin)["session_id"])')"
docker logs --since "$started_at" "$OPENCLAW_CONTAINER" >"$EVIDENCE_DIR/gateway-write.log" 2>&1
python3 "$SCRIPT_DIR/../spiffe_client/verify_audit.py" "$session_id" --client-id "$CLIENT_ID" --server-id "$SERVER_ID" <"$EVIDENCE_DIR/gateway-write.log" >"$EVIDENCE_DIR/write-mtls.json"

# The second session sees only the lookup key, never the expected random answer.
ATTEMPT_ID="$(date -u +%Y%m%dT%H%M%SZ)-$RANDOM"
RECALL_SESSION_KEY="$SESSION_KEY-recall-$ATTEMPT_ID"
printf '%s\n' "$RECALL_SESSION_KEY" >"$EVIDENCE_DIR/recall-session-key.txt"
RECALL_QUERY="请从长期记忆中查找项目 $MARKER 的校验码。仅原样回答校验码；查不到就只回答 UNKNOWN，不要猜测。"
printf '%s\n' "$RECALL_QUERY" >"$EVIDENCE_DIR/recall-prompt.txt"
docker exec -u "$OPENCLAW_USER" -e OPENCLAW_CONFIG_PATH="$OPENCLAW_CONFIG" "$OPENCLAW_CONTAINER" \
    openclaw agent --agent "$AGENT_ID" --session-key "$RECALL_SESSION_KEY" --message "$RECALL_QUERY" \
    --timeout "$AGENT_TIMEOUT" --json >"$EVIDENCE_DIR/recall-response.json" || fail 'fresh-session recall turn failed'
NEGATIVE_KEY="ARGUS-ABSENT-$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
NEGATIVE_QUERY="请从长期记忆中查找项目 $NEGATIVE_KEY 的校验码。仅原样回答校验码；查不到就只回答 UNKNOWN，不要猜测。"
printf '%s\n' "$NEGATIVE_QUERY" >"$EVIDENCE_DIR/negative-prompt.txt"
docker exec -u "$OPENCLAW_USER" -e OPENCLAW_CONFIG_PATH="$OPENCLAW_CONFIG" "$OPENCLAW_CONTAINER" \
    openclaw agent --agent "$AGENT_ID" --session-key "$SESSION_KEY-negative-$ATTEMPT_ID" --message "$NEGATIVE_QUERY" \
    --timeout "$AGENT_TIMEOUT" --json >"$EVIDENCE_DIR/negative-response.json" || fail 'negative-control turn failed'
docker logs --since "$started_at" "$OPENCLAW_CONTAINER" >"$EVIDENCE_DIR/gateway.log" 2>&1
python3 "$SCRIPT_DIR/../spiffe_client/verify_business.py" "$EVIDENCE_DIR" "$RECALL_SESSION_KEY" --client-id "$CLIENT_ID" --server-id "$SERVER_ID" \
    >"$EVIDENCE_DIR/recall-verification.json" || fail 'recall answer/input/mTLS evidence or negative control failed'

printf '%s\n' \
    'Real OpenClaw -> native SPIFFE mTLS -> NGINX -> OpenViking plugin E2E passed.' \
    "Marker: $MARKER" \
    "OpenClaw session key: $SESSION_KEY" \
    "OpenViking processing: $processing_summary" \
    "Evidence: $EVIDENCE_DIR" \
    'Fresh-session recall and UNKNOWN negative control passed; OpenClaw remote TDX attestation remains NOT_RUN.'
