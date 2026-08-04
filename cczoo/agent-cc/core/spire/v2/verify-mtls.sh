#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENCLAW_MTLS_CONTAINER="${V2_OPENCLAW_MTLS_CONTAINER:-argus-v2-openclaw-mtls}"
REAL_OPENCLAW_CONTAINER="${V2_REAL_OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
REAL_OPENCLAW_USER="${V2_REAL_OPENCLAW_USER:-node}"
OPENCLAW_PROXY_BIND="${V2_OPENCLAW_PROXY_BIND:-172.31.44.1}"
OPENCLAW_PROXY_PORT="${V2_OPENCLAW_PROXY_PORT:-1934}"
TDVM_MTLS_PORT="${V2_TDVM_MTLS_PORT:-1943}"
OPENCLAW_PROXY_URL="${V2_OPENCLAW_PROXY_URL:-http://$OPENCLAW_PROXY_BIND:$OPENCLAW_PROXY_PORT}"
OPENVIKING_MTLS_URL="${V2_OPENVIKING_MTLS_URL:-https://127.0.0.1:$TDVM_MTLS_PORT}"
GUARD_URL="${V2_GUARD_URL:-http://127.0.0.1:18007}"

real_openclaw_get() {
    docker exec -i -u "$REAL_OPENCLAW_USER" "$REAL_OPENCLAW_CONTAINER" \
        node - "$1" <<'NODE'
const url = process.argv[2];

async function main() {
  const response = await fetch(url, {
    headers: { "X-Argus-Request-ID": "verify-mtls-health" },
    signal: AbortSignal.timeout(10000),
  });
  const body = await response.text();
  if (!response.ok) {
    throw new Error(`${url} returned HTTP ${response.status}: ${body}`);
  }
  process.stdout.write(body);
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
NODE
}

"$SCRIPT_DIR/verify-svid.sh"

guard_health="$(curl -fsS --max-time 10 "$GUARD_URL/health")"
printf '%s' "$guard_health" | python3 -c '
import json
import sys

value = json.load(sys.stdin)
if value.get("status") != "OK" or value.get("mode") != "mock_allow":
    raise SystemExit("Argus Guard is not healthy in explicit mock_allow mode")
'

guard_payload="$(
    python3 - "$OPENVIKING_MTLS_URL" <<'PY'
import json
import sys

print(json.dumps({
    "target": {
        "service_name": "openviking-cmem",
        "target_uri": sys.argv[1],
    },
    "caller_id": "spiffe://argus.local/agent/openclaw",
}))
PY
)"
guard_response="$(
    curl -fsS --max-time 10 \
        -H 'Content-Type: application/json' \
        -d "$guard_payload" \
        "$GUARD_URL/ra/v1/verify"
)"
printf '%s' "$guard_response" | python3 -c '
import json
import sys

value = json.load(sys.stdin)
if value.get("decision") != "ALLOW":
    raise SystemExit("Argus Guard did not return ALLOW")
if value.get("verification_mode") != "mock_allow":
    raise SystemExit("Argus Guard response did not declare mock_allow")
if value.get("claims") is not None:
    raise SystemExit("mock_allow must not fabricate verified claims")
'

response="$(real_openclaw_get "$OPENCLAW_PROXY_URL/health")"
printf 'OpenClaw -> OpenViking SPIFFE mTLS response:\n%s\n' "$response"

proxy_status="$(
    curl -sS --max-time 3 \
        -o /dev/null \
        -w '%{http_code}' \
        "$OPENCLAW_PROXY_URL/health" || true
)"
if [[ "$proxy_status" != 403 ]]; then
    printf 'OpenClaw mTLS proxy did not reject the host source; HTTP status=%s.\n' \
        "${proxy_status:-unreachable}" >&2
    exit 1
fi

if curl -fsS --max-time 3 \
    "${OPENVIKING_MTLS_URL/https:/http:}/health" >/dev/null 2>&1; then
    printf 'OpenViking port 1943 unexpectedly accepted plaintext HTTP.\n' >&2
    exit 1
fi

if curl -kfsS --max-time 3 \
    "$OPENVIKING_MTLS_URL/health" >/dev/null 2>&1; then
    printf 'OpenViking port 1943 unexpectedly accepted TLS without a client SVID.\n' >&2
    exit 1
fi

docker exec "$OPENCLAW_MTLS_CONTAINER" /spire-mtls probe \
    -socket=unix:///opt/spire/run/openclaw/agent.sock \
    -target="$OPENVIKING_MTLS_URL/health" \
    -server-id=spiffe://argus.local/service/not-openviking \
    -expect-server-id-rejection \
    -timeout=5s

# Recheck the authenticated path after all negative probes so a service outage
# cannot make plaintext or missing-client checks look like authorization denial.
real_openclaw_get "$OPENCLAW_PROXY_URL/health" >/dev/null

printf '%s\n' \
    'Argus Guard: real process, explicit mock_allow decision, no fabricated claims' \
    'SPIFFE mTLS: mutual X.509-SVID authentication and exact peer ID passed' \
    'OpenClaw mTLS egress: real OpenClaw source allowed, host source rejected' \
    'Plaintext, missing client SVID, and wrong server ID: rejected' \
    'Real Quote/QGS: DEFERRED' \
    'Unbypassable same-request Guard-to-mTLS gate: DEFERRED' \
    'Envoy/service mesh: DEFERRED'
