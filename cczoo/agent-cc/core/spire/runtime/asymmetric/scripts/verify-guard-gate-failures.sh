#!/usr/bin/env bash
set -euo pipefail

OPENCLAW_CONTAINER="${V2_REAL_OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
OPENCLAW_IMAGE="${V2_OPENCLAW_WORKLOAD_IMAGE:-openclaw-sbx:latest}"
CONTROL_NETWORK="${V2_OPENCLAW_CONTROL_NETWORK:-argus-spire-v2-center_center}"
OPENVIKING_ORIGIN="${V2_OPENVIKING_ORIGIN:-https://openviking.argus.local:1943}"
GUARD_CONTAINER="${V2_GUARD_CONTAINER:-argus-v2-guard}"
FAULT_CONTAINER="${V2_GUARD_FAULT_CONTAINER:-argus-guard-fault-stub}"

fail() { printf 'caller-local Guard failure matrix: FAIL: %s\n' "$1" >&2; exit 1; }

restore() {
    local status=$?
    trap - EXIT
    docker rm -f "$FAULT_CONTAINER" >/dev/null 2>&1 || true
    docker start "$GUARD_CONTAINER" >/dev/null 2>&1 || true
    exit "$status"
}
trap restore EXIT

fault_request() {
    local guard_url="$1"
    local timeout_ms="$2"
    local target_id="${3:-spiffe://argus.local/service/openviking-cmem}"
    docker exec -i \
        -e "ARGUS_GUARD_URL=$guard_url" \
        -e "ARGUS_GUARD_TIMEOUT_MS=$timeout_ms" \
        -e "ARGUS_TARGET_SPIFFE_ID=$target_id" \
        "$OPENCLAW_CONTAINER" node - "$OPENVIKING_ORIGIN" <<'NODE'
try {
  const response = await fetch(`${process.argv[2]}/api/v1/guard-gate-must-not-forward`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ marker: "MUST-NOT-FORWARD" }),
    signal: AbortSignal.timeout(10000),
  });
  console.error(`request unexpectedly reached OpenViking: HTTP ${response.status}`);
  process.exit(2);
} catch (error) {
  if (!String(error?.message ?? error).includes("Argus Guard")) {
    console.error(error);
    process.exit(3);
  }
}
NODE
}

start_stub() {
    local mode="$1"
    docker rm -f "$FAULT_CONTAINER" >/dev/null 2>&1 || true
    docker run -d --rm \
        --name "$FAULT_CONTAINER" \
        --network "$CONTROL_NETWORK" \
        --entrypoint node \
        -e "FAULT_MODE=$mode" \
        "$OPENCLAW_IMAGE" -e '
const http = require("node:http");
http.createServer((request, response) => {
  let raw = "";
  request.on("data", (chunk) => { raw += chunk; });
  request.on("end", () => {
    if (process.env.FAULT_MODE === "timeout") return setTimeout(() => response.end(), 5000);
    if (process.env.FAULT_MODE === "http_503") { response.writeHead(503); return response.end(); }
    if (process.env.FAULT_MODE === "malformed") { response.writeHead(200, {"content-type":"application/json"}); return response.end("{\"decision\":"); }
    const input = raw ? JSON.parse(raw) : {};
    response.writeHead(200, {"content-type":"application/json"});
    response.end(JSON.stringify({request_id:input.request_id,decision:"DENY",reason:"fault injection",decision_id:"fault-deny",expires_at_unix:Math.floor(Date.now()/1000)+15,policy_id:"fault",rule_id:null}));
  });
}).listen(8007, "0.0.0.0");
' >/dev/null
    for _ in $(seq 1 20); do
        if [[ "$(docker inspect "$FAULT_CONTAINER" --format '{{.State.Running}}' 2>/dev/null || true)" == true ]] \
            && docker exec "$FAULT_CONTAINER" node -e \
                'fetch("http://127.0.0.1:8007/health").then(() => process.exit(0), () => process.exit(1))' \
                >/dev/null 2>&1; then
            return
        fi
        read -r -t 1 _ || true
    done
    fail "fault stub did not start for $mode"
}

for mode in deny malformed http_503 timeout; do
    start_stub "$mode"
    fault_request "http://$FAULT_CONTAINER:8007/guard/v1/authorize" 500 \
        || fail "scenario $mode did not fail closed before OpenViking"
done
docker rm -f "$FAULT_CONTAINER" >/dev/null 2>&1 || true

fault_request http://guard:8007/guard/v1/authorize 2000 \
    spiffe://argus.local/service/not-openviking \
    || fail 'real Guard DENY did not fail closed'

docker stop "$GUARD_CONTAINER" >/dev/null
fault_request http://guard:8007/guard/v1/authorize 500 \
    || fail 'Guard outage did not fail closed'
docker start "$GUARD_CONTAINER" >/dev/null
for _ in $(seq 1 30); do
    if docker exec "$OPENCLAW_CONTAINER" node - <<'NODE' >/dev/null 2>&1
const response = await fetch(process.env.ARGUS_GUARD_URL.replace(/\/guard\/v1\/authorize$/, "/health"));
if (!response.ok) process.exit(1);
NODE
    then
        break
    fi
    read -r -t 1 _ || true
done
docker exec "$OPENCLAW_CONTAINER" node - "$OPENVIKING_ORIGIN" <<'NODE'
const response = await fetch(`${process.argv[2]}/health`, { signal: AbortSignal.timeout(10000) });
if (!response.ok) throw new Error(`recovery returned HTTP ${response.status}`);
NODE

trap - EXIT
docker rm -f "$FAULT_CONTAINER" >/dev/null 2>&1 || true
printf '%s\n' \
    'Native Guard failure matrix passed.' \
    'DENY, malformed response, HTTP 503, timeout, and Guard outage all failed before the OpenViking fetch.' \
    'The real Guard and Broker Sidecar SPIFFE mTLS path recovered successfully.'
