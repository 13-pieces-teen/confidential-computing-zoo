#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENCLAW_CONTAINER="${V2_REAL_OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
OPENVIKING_CONTAINER="${V2_REAL_OPENVIKING_CONTAINER:-agentcc-openviking-service}"
OPENVIKING_ORIGIN="${V2_OPENVIKING_ORIGIN:-https://openviking.argus.local:1943}"
GUARD_BASE_URL="${V2_GUARD_URL:-http://127.0.0.1:${V2_GUARD_PORT:-18007}}"
GUARD_TOKEN_FILE="${V2_GUARD_API_TOKEN_FILE:-${V2_RUNTIME_DIR:-$SCRIPT_DIR/../runtime}/secrets/openclaw-guard-api-token}"
TDVM_SSH_TARGET="${TDVM_SSH_TARGET:-tdx@127.0.0.1}"
TDVM_SSH_PORT="${TDVM_SSH_PORT:-2222}"
TDVM_SSH_IDENTITY="${TDVM_SSH_IDENTITY:-}"
TDVM_KNOWN_HOSTS="${TDVM_KNOWN_HOSTS:-/tmp/argus-openviking-v2-known-hosts}"
ssh_options=(-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -o "UserKnownHostsFile=$TDVM_KNOWN_HOSTS" -p "$TDVM_SSH_PORT")
[[ -n "$TDVM_SSH_IDENTITY" ]] && ssh_options+=(-i "$TDVM_SSH_IDENTITY")

fail() { printf 'native SPIFFE mTLS verification: FAIL: %s\n' "$1" >&2; exit 1; }

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
        -d "{\"request_id\":\"verify-native-$RANDOM\",\"caller_spiffe_id\":\"spiffe://argus.local/agent/openclaw\",\"target_spiffe_id\":\"$target_id\",\"target_service\":\"openviking-cmem\",\"target_origin\":\"$OPENVIKING_ORIGIN\",\"operation\":\"memory.read\",\"data_class\":\"sensitive\"}" \
        "$GUARD_BASE_URL/guard/v1/authorize"
}

authorize spiffe://argus.local/service/openviking-cmem \
    | python3 -c 'import json,sys; value=json.load(sys.stdin); assert value["decision"] == "ALLOW" and value["rule_id"]'
authorize spiffe://argus.local/service/not-openviking \
    | python3 -c 'import json,sys; value=json.load(sys.stdin); assert value["decision"] == "DENY" and value.get("rule_id") is None'

docker exec -i "$OPENCLAW_CONTAINER" node - "$OPENVIKING_ORIGIN" <<'NODE'
const response = await fetch(`${process.argv[2]}/health`, { signal: AbortSignal.timeout(10000) });
if (!response.ok) throw new Error(`native OpenViking health returned HTTP ${response.status}`);
NODE

if ssh "${ssh_options[@]}" "$TDVM_SSH_TARGET" \
    curl -kfsS --noproxy '*' --max-time 3 https://127.0.0.1:1943/health >/dev/null 2>&1; then
    fail 'OpenViking accepted TLS without a client X509-SVID'
fi

ssh "${ssh_options[@]}" "$TDVM_SSH_TARGET" \
    sudo -n /usr/local/bin/docker exec -i "$OPENVIKING_CONTAINER" python3 - <<'PY'
import socket, ssl

context = ssl.create_default_context(cafile="/run/argus-svid/bundle.pem")
context.check_hostname = False
context.load_cert_chain("/run/argus-svid/svid.pem", "/run/argus-svid/svid-key.pem")
with socket.create_connection(("127.0.0.1", 1943), timeout=5) as raw:
    with context.wrap_socket(raw, server_hostname="openviking.argus.local") as connection:
        connection.sendall(b"GET /health HTTP/1.1\r\nHost: openviking.argus.local\r\nConnection: close\r\n\r\n")
        if connection.recv(1):
            raise SystemExit("OpenViking accepted its own workload SVID as an OpenClaw client")
PY

docker exec -i "$OPENCLAW_CONTAINER" node - "$OPENVIKING_ORIGIN" <<'NODE'
const response = await fetch(`${process.argv[2]}/ready`, { signal: AbortSignal.timeout(10000) });
if (!response.ok) throw new Error(`native OpenViking readiness returned HTTP ${response.status}`);
NODE

printf '%s\n' \
    'Native SPIFFE mTLS verification passed.' \
    'Guard exact policy: matching request ALLOW; wrong target ID DENY.' \
    'OpenClaw process: direct HTTPS with its X509-SVID succeeded.' \
    'OpenViking TLS: missing client certificate and wrong client SPIFFE ID were rejected.'
