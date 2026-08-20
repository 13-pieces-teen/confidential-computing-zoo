#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENCLAW_CONTAINER="${V2_REAL_OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
OPENVIKING_ORIGIN="${V2_OPENVIKING_ORIGIN:-https://openviking.argus.local:1943}"
OPENVIKING_MTLS_PORT="${V2_OPENVIKING_MTLS_PORT:-1943}"
GUARD_BASE_URL="${V2_GUARD_URL:-http://127.0.0.1:${V2_GUARD_PORT:-18007}}"
GUARD_TOKEN_FILE="${V2_GUARD_API_TOKEN_FILE:-${V2_RUNTIME_DIR:-$SCRIPT_DIR/../runtime}/secrets/openclaw-guard-api-token}"
TDVM_SSH_TARGET="${TDVM_SSH_TARGET:-tdx@127.0.0.1}"
TDVM_SSH_PORT="${TDVM_SSH_PORT:-2222}"
TDVM_SSH_IDENTITY="${TDVM_SSH_IDENTITY:-}"
TDVM_KNOWN_HOSTS="${TDVM_KNOWN_HOSTS:-/tmp/argus-openviking-v2-known-hosts}"
ssh_options=(-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -o "UserKnownHostsFile=$TDVM_KNOWN_HOSTS" -p "$TDVM_SSH_PORT")
[[ -n "$TDVM_SSH_IDENTITY" ]] && ssh_options+=(-i "$TDVM_SSH_IDENTITY")

fail() { printf 'Broker Sidecar SPIFFE mTLS verification: FAIL: %s\n' "$1" >&2; exit 1; }

[[ -r "$GUARD_TOKEN_FILE" ]] || fail "Guard API token file is not readable: $GUARD_TOKEN_FILE"
GUARD_API_TOKEN="$(tr -d '\r\n' <"$GUARD_TOKEN_FILE")"
[[ -n "$GUARD_API_TOKEN" ]] || fail 'Guard API token is empty'

bash "$SCRIPT_DIR/verify-svid.sh"

health="$(curl -fsS --noproxy '127.0.0.1,localhost' --max-time 5 "$GUARD_BASE_URL/health")"
printf '%s' "$health" | python3 -c '
import json, sys
value = json.load(sys.stdin)
if value.get("status") != "OK" or value.get("mode") != "spiffe_identity":
    raise SystemExit("Guard is not healthy in spiffe_identity mode")
'

authorize() {
    local target_id="$1"
    curl -fsS --noproxy '127.0.0.1,localhost' --max-time 5 \
        -H 'Content-Type: application/json' \
        -H "Authorization: Bearer $GUARD_API_TOKEN" \
        -d "{\"request_id\":\"verify-broker-$RANDOM\",\"caller_spiffe_id\":\"spiffe://argus.local/agent/openclaw\",\"target_spiffe_id\":\"$target_id\",\"target_service\":\"openviking-cmem\",\"target_origin\":\"$OPENVIKING_ORIGIN\",\"operation\":\"memory.read\",\"data_class\":\"sensitive\"}" \
        "$GUARD_BASE_URL/guard/v1/authorize"
}

authorize spiffe://argus.local/service/openviking-cmem \
    | python3 -c 'import json,sys; value=json.load(sys.stdin); assert value["decision"] == "ALLOW" and value["rule_id"]'
authorize spiffe://argus.local/service/not-openviking \
    | python3 -c 'import json,sys; value=json.load(sys.stdin); assert value["decision"] == "DENY" and value.get("rule_id") is None'

docker exec -i "$OPENCLAW_CONTAINER" node - "$OPENVIKING_ORIGIN" <<'NODE'
const response = await fetch(`${process.argv[2]}/health`, { signal: AbortSignal.timeout(10000) });
if (!response.ok) throw new Error(`Broker-protected OpenViking health returned HTTP ${response.status}`);
NODE

if ssh "${ssh_options[@]}" "$TDVM_SSH_TARGET" \
    curl -kfsS --noproxy '*' --max-time 3 "https://127.0.0.1:${OPENVIKING_MTLS_PORT}/health" >/dev/null 2>&1; then
    fail 'Broker Sidecar accepted TLS without a client X509-SVID'
fi

docker exec -i "$OPENCLAW_CONTAINER" node - "$OPENVIKING_ORIGIN" <<'NODE'
const response = await fetch(`${process.argv[2]}/ready`, { signal: AbortSignal.timeout(10000) });
if (!response.ok) throw new Error(`Broker-protected OpenViking readiness returned HTTP ${response.status}`);
NODE

printf '%s\n' \
    'Broker Sidecar SPIFFE mTLS verification passed.' \
    'Guard exact policy: matching request ALLOW; wrong target ID DENY.' \
    'OpenClaw process: HTTPS with its X509-SVID succeeded.' \
    'Broker Sidecar: TLS without a client certificate was rejected.'
