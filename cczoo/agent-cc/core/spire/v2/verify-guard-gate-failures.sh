#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/compose.center.yaml"
OPENCLAW_CONTAINER="${V2_REAL_OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
OPENCLAW_USER="${V2_REAL_OPENCLAW_USER:-node}"
MTLS_CONTAINER="${V2_OPENCLAW_MTLS_CONTAINER:-argus-v2-openclaw-mtls}"
PROXY_BIND="${V2_OPENCLAW_PROXY_BIND:-172.31.44.1}"
PROXY_PORT="${V2_OPENCLAW_PROXY_PORT:-1934}"
PROXY_URL="${V2_OPENCLAW_PROXY_URL:-http://$PROXY_BIND:$PROXY_PORT}"
ORIGINAL_GUARD_PORT="${V2_GUARD_PORT:-18007}"
ORIGINAL_GUARD_TIMEOUT="${V2_GUARD_TIMEOUT:-3s}"
FAULT_GUARD_PORT="${V2_GUARD_FAULT_PORT:-18017}"
FAULT_GUARD_TIMEOUT="${V2_GUARD_FAULT_TIMEOUT:-1s}"
FAULT_STATE_DIR="$(mktemp -d)"
STUB_PID=""

fail() {
    printf 'Guard gate failure matrix: FAIL: %s\n' "$1" >&2
    exit 1
}

valid_port() {
    [[ "$1" =~ ^[1-9][0-9]*$ ]] && (( "$1" <= 65535 ))
}

valid_port "$ORIGINAL_GUARD_PORT" \
    || fail "invalid V2_GUARD_PORT: $ORIGINAL_GUARD_PORT"
valid_port "$FAULT_GUARD_PORT" \
    || fail "invalid V2_GUARD_FAULT_PORT: $FAULT_GUARD_PORT"
[[ "$ORIGINAL_GUARD_PORT" != "$FAULT_GUARD_PORT" ]] \
    || fail 'fault Guard port must differ from the real Guard port'
[[ "$FAULT_GUARD_TIMEOUT" =~ ^[1-9][0-9]*(ms|s)$ ]] \
    || fail "V2_GUARD_FAULT_TIMEOUT must be a positive integer duration: $FAULT_GUARD_TIMEOUT"

recreate_egress() {
    local guard_port="$1"
    local guard_timeout="$2"
    V2_GUARD_PORT="$guard_port" \
    V2_GUARD_TIMEOUT="$guard_timeout" \
        docker compose -f "$COMPOSE_FILE" \
        --profile workload \
        up -d --force-recreate --no-deps openclaw-mtls-client >/dev/null
}

wait_for_egress_identity() {
    for _ in $(seq 1 30); do
        if docker exec "$MTLS_CONTAINER" /spire-mtls identity \
            -socket=unix:///opt/spire/run/openclaw/agent.sock \
            -expected-id=spiffe://argus.local/agent/openclaw \
            >/dev/null 2>&1; then
            return 0
        fi
        read -r -t 1 _ || true
    done
    return 1
}

stop_stub() {
    if [[ -n "$STUB_PID" ]]; then
        kill "$STUB_PID" >/dev/null 2>&1 || true
        wait "$STUB_PID" >/dev/null 2>&1 || true
        STUB_PID=""
    fi
}

restore_real_gate() {
    local original_status=$?
    trap - EXIT
    stop_stub
    if ! recreate_egress "$ORIGINAL_GUARD_PORT" "$ORIGINAL_GUARD_TIMEOUT" \
        >/dev/null 2>&1 \
        || ! wait_for_egress_identity; then
        printf '%s\n' \
            'Guard gate failure matrix: FAIL: could not restore the real Guard egress.' >&2
        original_status=1
    fi
    rm -rf -- "$FAULT_STATE_DIR"
    exit "$original_status"
}
trap restore_real_gate EXIT

start_stub() {
    local mode="$1"
    stop_stub
    python3 - "$FAULT_GUARD_PORT" "$mode" >"$FAULT_STATE_DIR/$mode.log" 2>&1 <<'PY' &
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

port = int(sys.argv[1])
mode = sys.argv[2]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        sys.stderr.write((format % args) + "\n")

    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        body = b'{"status":"OK"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/ra/v1/verify":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            request = json.loads(raw)
            authorization = request["authorization_context"]
            request_digest = authorization["request_digest"]
        except Exception:
            self.send_error(400)
            return

        if mode == "timeout":
            time.sleep(5)
        if mode == "http_503":
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if mode == "malformed":
            body = b'{"decision":'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        decision = "DENY" if mode in {"deny", "deny_missing_receipt"} else "ALLOW"
        if mode == "digest_mismatch":
            request_digest = "sha256:" + ("0" * 64)
        expires_at_unix = int(time.time()) + 15
        if mode == "expired":
            expires_at_unix = int(time.time()) - 1
        response = {
            "decision": decision,
            "reason": "fault injection" if decision == "DENY" else None,
            "claims": None,
            "verification_mode": "mock_allow",
            "decision_id": "a" * 32,
            "request_digest": request_digest,
            "expires_at_unix": expires_at_unix,
        }
        if mode in {"missing_receipt", "deny_missing_receipt"}:
            response.pop("decision_id")
            response.pop("request_digest")
            response.pop("expires_at_unix")
        body = json.dumps(response, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass


ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
PY
    STUB_PID=$!

    for _ in $(seq 1 20); do
        if curl -fsS --noproxy '127.0.0.1,localhost' --max-time 1 \
            "http://127.0.0.1:$FAULT_GUARD_PORT/health" >/dev/null 2>&1; then
            return 0
        fi
        read -r -t 1 _ || true
    done
    return 1
}

openclaw_fault_request() {
    local scenario="$1"
    docker exec -i -u "$OPENCLAW_USER" "$OPENCLAW_CONTAINER" \
        node - "$PROXY_URL" "$scenario" <<'NODE'
const proxyURL = process.argv[2].replace(/\/+$/, "");
const scenario = process.argv[3];

async function main() {
  const recovery = scenario === "recovery";
  const requestURL = recovery
    ? `${proxyURL}/health`
    : `${proxyURL}/api/v1/guard-gate-negative`;
  const options = {
    headers: {
      "Content-Type": "application/json",
      "X-Argus-Request-ID": `guard-failure-${scenario}`,
    },
    signal: AbortSignal.timeout(10000),
  };
  if (!recovery) {
    options.method = "POST";
    options.body = JSON.stringify({ marker: `MUST-NOT-FORWARD-${scenario}` });
  }
  const response = await fetch(requestURL, options);
  const body = await response.text();
  const requestID = response.headers.get("x-argus-request-id") ?? "";
  const decisionID = response.headers.get("x-argus-decision-id") ?? "";
  const requestDigest = response.headers.get("x-argus-request-digest") ?? "";
  const verificationMode = response.headers.get("x-argus-verification-mode") ?? "";
  process.stdout.write(JSON.stringify({
    status: response.status,
    body,
    request_id: requestID,
    decision_id: decisionID,
    request_digest: requestDigest,
    verification_mode: verificationMode,
  }));
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
NODE
}

run_scenario() {
    local mode="$1"
    local expected_status="$2"
    local expected_body="$3"
    local expected_decision="$4"
    local started_at response request_id

    start_stub "$mode" || fail "fault Guard stub did not start for $mode"
    recreate_egress "$FAULT_GUARD_PORT" "$FAULT_GUARD_TIMEOUT"
    wait_for_egress_identity || fail "mTLS egress did not restart for $mode"
    started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    response="$(openclaw_fault_request "$mode")" \
        || fail "real OpenClaw could not execute the $mode request"

    RESPONSE_JSON="$response" python3 - \
        "$expected_status" "$expected_body" "$expected_decision" <<'PY'
import json
import os
import re
import sys

value = json.loads(os.environ["RESPONSE_JSON"])
expected_status = int(sys.argv[1])
expected_body = sys.argv[2]
expected_decision = sys.argv[3]
if value.get("status") != expected_status:
    raise SystemExit(
        f"unexpected HTTP status: {value.get('status')}, expected {expected_status}"
    )
if value.get("body") != expected_body:
    raise SystemExit(
        f"unexpected response body: {value.get('body')!r}, expected {expected_body!r}"
    )
if not re.fullmatch(r"[0-9a-f]{24}", value.get("request_id", "")):
    raise SystemExit("failure response has no generated internal request ID")
receipt = (
    value.get("decision_id", ""),
    value.get("request_digest", ""),
    value.get("verification_mode", ""),
)
if expected_decision == "guard_denied":
    if not re.fullmatch(r"[0-9a-f]{32}", receipt[0]):
        raise SystemExit("Guard DENY response has no valid decision ID")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", receipt[1]):
        raise SystemExit("Guard DENY response has no valid request digest")
    if receipt[2] != "mock_allow":
        raise SystemExit("Guard DENY response has the wrong verification mode")
elif any(receipt):
    raise SystemExit("invalid Guard response leaked a decision receipt")
PY
    request_id="$(
        printf '%s' "$response" | python3 -c '
import json
import sys
print(json.load(sys.stdin)["request_id"])
'
    )"

    scenario_log=""
    for _ in 1 2 3 4 5; do
        logs="$(docker logs --since "$started_at" "$MTLS_CONTAINER" 2>&1)"
        scenario_log="$(
            printf '%s\n' "$logs" \
                | grep -F "request_id=$request_id " \
                | grep -F "client_request_id=guard-failure-$mode " \
                | grep -F "decision=$expected_decision" \
                | grep -F 'request_digest=sha256:' \
                | tail -n 1 || true
        )"
        [[ -n "$scenario_log" ]] && break
        read -r -t 1 _ || true
    done
    [[ -n "$scenario_log" ]] \
        || fail "mTLS egress did not log $expected_decision for $mode"
    if [[ "$expected_decision" == guard_denied ]] \
        && ! printf '%s\n' "$scenario_log" \
            | grep -F 'guard_decision_id=' \
            | grep -F 'request_digest=sha256:' \
            | grep -F 'verification_mode=mock_allow' >/dev/null; then
        fail 'mTLS egress did not preserve the valid Guard DENY receipt'
    fi
    if printf '%s\n' "$logs" \
        | grep -F "request_id=$request_id " \
        | grep -F 'decision=forwarded_mtls' >/dev/null; then
        fail "$mode request was forwarded after Guard failure"
    fi
    printf 'Guard gate scenario passed: mode=%s request_id=%s\n' \
        "$mode" "$request_id"
}

docker inspect "$OPENCLAW_CONTAINER" >/dev/null 2>&1 \
    || fail "real OpenClaw container does not exist: $OPENCLAW_CONTAINER"
docker inspect "$MTLS_CONTAINER" >/dev/null 2>&1 \
    || fail "mTLS egress container does not exist: $MTLS_CONTAINER"

run_scenario deny 403 'Argus Guard denied request' guard_denied
run_scenario deny_missing_receipt 503 'Argus Guard unavailable' guard_error
run_scenario http_503 503 'Argus Guard unavailable' guard_error
run_scenario timeout 503 'Argus Guard unavailable' guard_error
run_scenario malformed 503 'Argus Guard unavailable' guard_error
run_scenario missing_receipt 503 'Argus Guard unavailable' guard_error
run_scenario digest_mismatch 503 'Argus Guard unavailable' guard_error
run_scenario expired 503 'Argus Guard unavailable' guard_error

stop_stub
recreate_egress "$ORIGINAL_GUARD_PORT" "$ORIGINAL_GUARD_TIMEOUT"
wait_for_egress_identity || fail 'mTLS egress did not recover after Guard fault matrix'
recovery_response="$(openclaw_fault_request recovery)" \
    || fail 'real Guard did not answer after fault-matrix recovery'
RECOVERY_JSON="$recovery_response" python3 - <<'PY'
import json
import os
import re

value = json.loads(os.environ["RECOVERY_JSON"])
if value.get("status") != 200:
    raise SystemExit(
        f"recovered real Guard path returned HTTP {value.get('status')}, expected 200"
    )
if not re.fullmatch(r"[0-9a-f]{24}", value.get("request_id", "")):
    raise SystemExit("recovered path has no generated request ID")
if not re.fullmatch(r"[0-9a-f]{32}", value.get("decision_id", "")):
    raise SystemExit("recovered path has no Guard decision ID")
if not re.fullmatch(r"sha256:[0-9a-f]{64}", value.get("request_digest", "")):
    raise SystemExit("recovered path has no Guard request digest")
if value.get("verification_mode") != "mock_allow":
    raise SystemExit("recovered path did not use the real mock_allow Guard")
PY
trap - EXIT
rm -rf -- "$FAULT_STATE_DIR"

printf '%s\n' \
    'Guard gate failure matrix passed.' \
    'Valid DENY was preserved; malformed DENY, HTTP 503, timeout, malformed JSON, missing receipt, digest mismatch, and expiry all failed closed.' \
    'No rejected request produced a forwarded_mtls log.' \
    'The OpenClaw mTLS egress was restored and completed a real Guard-gated request.'
