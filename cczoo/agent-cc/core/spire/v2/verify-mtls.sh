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
GUARD_URL="${V2_GUARD_URL:-http://127.0.0.1:${V2_GUARD_PORT:-18007}}"
GUARD_CONTAINER="${V2_GUARD_CONTAINER:-argus-v2-guard}"

real_openclaw_get() {
    docker exec -i -u "$REAL_OPENCLAW_USER" "$REAL_OPENCLAW_CONTAINER" \
        node - "$1" "${2:-verify-mtls}" <<'NODE'
const url = process.argv[2];
const clientRequestID = process.argv[3];

async function main() {
  const response = await fetch(url, {
    headers: { "X-Argus-Request-ID": clientRequestID },
    signal: AbortSignal.timeout(10000),
  });
  const body = await response.text();
  if (!response.ok) {
    throw new Error(`${url} returned HTTP ${response.status}: ${body}`);
  }
  const requestID = response.headers.get("x-argus-request-id") ?? "";
  const decisionID = response.headers.get("x-argus-decision-id") ?? "";
  const requestDigest = response.headers.get("x-argus-request-digest") ?? "";
  const verificationMode = response.headers.get("x-argus-verification-mode") ?? "";
  if (!/^[0-9a-f]{24}$/.test(requestID)) {
    throw new Error(`egress response has invalid request ID: ${requestID}`);
  }
  if (!/^[0-9a-f]{32}$/.test(decisionID)) {
    throw new Error(`egress response has invalid Guard decision ID: ${decisionID}`);
  }
  if (!/^sha256:[0-9a-f]{64}$/.test(requestDigest)) {
    throw new Error(`egress response has invalid request digest: ${requestDigest}`);
  }
  if (verificationMode !== "mock_allow") {
    throw new Error(`egress response has unexpected verification mode: ${verificationMode}`);
  }
  process.stdout.write(JSON.stringify({
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

"$SCRIPT_DIR/verify-svid.sh"

guard_health="$(
    curl -fsS --noproxy '127.0.0.1,localhost' --max-time 10 "$GUARD_URL/health"
)"
printf '%s' "$guard_health" | python3 -c '
import json
import sys

value = json.load(sys.stdin)
if value.get("status") != "OK" or value.get("mode") != "mock_allow":
    raise SystemExit("Argus Guard is not healthy in explicit mock_allow mode")
if value.get("authorization_context_required") is not True:
    raise SystemExit("Argus Guard does not require authorization_context")
if value.get("authorization_context_version") != "argus-authorization-v2":
    raise SystemExit("Argus Guard authorization context version is not v2")
ttl = value.get("decision_ttl_seconds")
if not isinstance(ttl, int) or not 1 <= ttl <= 300:
    raise SystemExit("Argus Guard decision TTL is invalid")
'

target_mismatch_payload="$(
    TARGET_URI="$OPENVIKING_MTLS_URL" python3 -c '
import hashlib
import json
import os
import secrets
import struct
import time

context = {
    "version": "argus-authorization-v2",
    "request_id": secrets.token_hex(12),
    "request_digest": "",
    "method": "GET",
    "path_and_query": "/health",
    "body_sha256": "sha256:" + hashlib.sha256(b"").hexdigest(),
    "caller_spiffe_id": "spiffe://argus.local/agent/openclaw",
    "target_spiffe_id": "spiffe://argus.local/service/openviking-cmem",
    "target_service": "not-openviking",
    "target_uri": os.environ["TARGET_URI"],
    "operation": "http:GET",
    "data_class": "openviking-context",
    "issued_at_unix": int(time.time()),
    "nonce": secrets.token_hex(16),
}
fields = [
    context["version"],
    context["request_id"],
    context["method"],
    context["path_and_query"],
    context["body_sha256"],
    context["caller_spiffe_id"],
    context["target_spiffe_id"],
    context["target_service"],
    context["target_uri"],
    context["operation"],
    context["data_class"],
    str(context["issued_at_unix"]),
    context["nonce"],
]
canonical = bytearray(b"argus-business-authorization-v2\0")
for field in fields:
    encoded = field.encode()
    canonical.extend(struct.pack(">I", len(encoded)))
    canonical.extend(encoded)
context["request_digest"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
print(json.dumps({
    "target": {
        "service_name": "openviking-cmem",
        "target_uri": os.environ["TARGET_URI"],
    },
    "caller_id": context["caller_spiffe_id"],
    "authorization_context": context,
}, separators=(",", ":")))
'
)"
target_mismatch_status="$(
    curl -sS --noproxy '127.0.0.1,localhost' --max-time 10 \
        -o /dev/null -w '%{http_code}' \
        -H 'Content-Type: application/json' \
        -d "$target_mismatch_payload" \
        "$GUARD_URL/ra/v1/verify"
)"
if [[ "$target_mismatch_status" != 400 ]]; then
    printf 'Argus Guard did not reject a digest-valid target mismatch; HTTP status=%s.\n' \
        "$target_mismatch_status" >&2
    exit 1
fi

guarded_started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
response_envelope="$(
    real_openclaw_get "$OPENCLAW_PROXY_URL/health" "verify-mtls-health"
)"
response="$(
    printf '%s' "$response_envelope" | python3 -c '
import json
import sys
print(json.load(sys.stdin)["body"], end="")
'
)"
guarded_request_id="$(
    printf '%s' "$response_envelope" | python3 -c '
import json
import sys
print(json.load(sys.stdin)["request_id"])
'
)"
guarded_decision_id="$(
    printf '%s' "$response_envelope" | python3 -c '
import json
import sys
print(json.load(sys.stdin)["decision_id"])
'
)"
guarded_request_digest="$(
    printf '%s' "$response_envelope" | python3 -c '
import json
import sys
print(json.load(sys.stdin)["request_digest"])
'
)"
printf 'OpenClaw -> OpenViking SPIFFE mTLS response:\n%s\n' "$response"

guarded_egress_log=""
guarded_guard_log=""
for _ in 1 2 3 4 5; do
    egress_logs="$(
        docker logs --since "$guarded_started_at" "$OPENCLAW_MTLS_CONTAINER" 2>&1
    )"
    guard_logs="$(
        docker logs --since "$guarded_started_at" "$GUARD_CONTAINER" 2>&1
    )"
    guarded_egress_log="$(
        printf '%s\n' "$egress_logs" \
            | grep -F "request_id=$guarded_request_id " \
            | grep -F 'client_request_id=verify-mtls-health ' \
            | grep -F 'decision=forwarded_mtls' \
            | grep -F "guard_decision_id=$guarded_decision_id " \
            | grep -F "request_digest=$guarded_request_digest " \
            | grep -F 'verification_mode=mock_allow' \
            | tail -n 1 || true
    )"
    guarded_guard_log="$(
        printf '%s\n' "$guard_logs" \
            | grep -F "request_id=$guarded_request_id" \
            | grep -F "request_digest=$guarded_request_digest" \
            | grep -F "decision_id=$guarded_decision_id" \
            | grep -F 'Guard returned mock ALLOW' \
            | tail -n 1 || true
    )"
    if [[ -n "$guarded_egress_log" && -n "$guarded_guard_log" ]]; then
        break
    fi
    read -r -t 1 _ || true
done
if [[ -z "$guarded_egress_log" || -z "$guarded_guard_log" ]]; then
    printf 'Same-request Guard-to-mTLS causal logs were not found.\n' >&2
    exit 1
fi

if ! docker inspect "$REAL_OPENCLAW_CONTAINER" | python3 -c '
import json
import os
import sys

containers = json.load(sys.stdin)
if len(containers) != 1:
    raise SystemExit("expected one real OpenClaw container inspection result")

blocked = []
for mount in containers[0].get("Mounts") or []:
    destination = os.path.normpath(str(mount.get("Destination") or ""))
    if (
        destination in {"/run/spire", "/opt/spire/run"}
        or destination.startswith("/run/spire/")
        or destination.startswith("/opt/spire/run/")
        or os.path.basename(destination) == "agent.sock"
    ):
        blocked.append(destination)

if blocked:
    print(
        "real OpenClaw container has prohibited Workload API mount(s): "
        + ", ".join(sorted(set(blocked))),
        file=sys.stderr,
    )
    raise SystemExit(1)
'; then
    printf 'Real OpenClaw Workload API isolation check failed.\n' >&2
    exit 1
fi

host_probe_request_id="verify-mtls-host-source-$(date -u +%Y%m%dT%H%M%SZ)-$$"
host_probe_started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
host_probe_headers="$(mktemp)"
host_probe_body_file="$(mktemp)"
cleanup_host_probe() {
    rm -f -- "$host_probe_headers" "$host_probe_body_file"
}
trap cleanup_host_probe EXIT
if ! proxy_status="$(
    curl -sS --noproxy "$OPENCLAW_PROXY_BIND" --max-time 3 \
        -H "X-Argus-Request-ID: $host_probe_request_id" \
        -D "$host_probe_headers" \
        -o "$host_probe_body_file" \
        -w '%{http_code}' \
        "$OPENCLAW_PROXY_URL/health"
)"; then
    printf 'Direct host-source probe could not reach the OpenClaw mTLS proxy.\n' >&2
    exit 1
fi
proxy_body="$(<"$host_probe_body_file")"
if [[ "$proxy_status" != 403 ]]; then
    printf 'OpenClaw mTLS proxy did not reject the host source; HTTP status=%s.\n' \
        "$proxy_status" >&2
    exit 1
fi
if [[ "$proxy_body" != 'OpenClaw egress source rejected' ]]; then
    printf 'Host-source HTTP 403 did not come from the OpenClaw mTLS proxy; body=%s.\n' \
        "${proxy_body:-empty}" >&2
    exit 1
fi
host_probe_internal_id="$(
    awk '
BEGIN { IGNORECASE = 1 }
tolower($1) == "x-argus-request-id:" {
    gsub("\r", "", $2)
    value = $2
}
END { print value }
' "$host_probe_headers"
)"
if [[ ! "$host_probe_internal_id" =~ ^[0-9a-f]{24}$ ]]; then
    printf 'Host-source rejection did not return a generated internal request ID.\n' >&2
    exit 1
fi
host_probe_log=""
for _ in 1 2 3 4 5; do
    if ! host_probe_logs="$(
        docker logs --since "$host_probe_started_at" "$OPENCLAW_MTLS_CONTAINER" 2>&1
    )"; then
        printf 'Could not read OpenClaw mTLS proxy logs for the host-source probe.\n' >&2
        exit 1
    fi
    host_probe_log="$(
        printf '%s\n' "$host_probe_logs" \
            | grep -F "request_id=$host_probe_internal_id " \
            | grep -F "client_request_id=$host_probe_request_id " \
            | grep -F 'method=GET path=/health status=403 ' \
            | grep -F 'decision=source_rejected' \
            | tail -n 1 || true
    )"
    [[ -z "$host_probe_log" ]] || break
    read -r -t 1 _ || true
done
if [[ -z "$host_probe_log" ]]; then
    printf 'OpenClaw mTLS proxy did not log the matching source_rejected request ID.\n' >&2
    exit 1
fi

if curl -fsS --noproxy '127.0.0.1,localhost' --max-time 3 \
    "${OPENVIKING_MTLS_URL/https:/http:}/health" >/dev/null 2>&1; then
    printf 'OpenViking port 1943 unexpectedly accepted plaintext HTTP.\n' >&2
    exit 1
fi

if curl -kfsS --noproxy '127.0.0.1,localhost' --max-time 3 \
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
real_openclaw_get "$OPENCLAW_PROXY_URL/health" "verify-mtls-post-negative" >/dev/null

printf '%s\n' \
    'Argus Guard: real caller-side PDP, explicit mock_allow decision, authorization_context required, no fabricated claims' \
    'Authorization context v2: digest-valid target mismatch rejected' \
    'Guard-to-mTLS gate: same request ID, decision ID, and request digest observed before forwarding' \
    'SPIFFE mTLS: mutual X.509-SVID authentication and exact peer ID passed' \
    'OpenClaw mTLS egress: real OpenClaw source allowed, direct host source rejected with matching proxy log' \
    'Real OpenClaw isolation: no SPIRE Workload API mount' \
    'Plaintext, missing client SVID, and wrong server ID: rejected' \
    'Real Quote/QGS: DEFERRED' \
    'Envoy/service mesh: DEFERRED'
