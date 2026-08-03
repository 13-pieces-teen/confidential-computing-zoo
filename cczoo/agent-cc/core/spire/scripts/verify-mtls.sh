#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENVIKING_PROXY_URL="${OPENVIKING_PROXY_URL:-http://127.0.0.1:1934}"
OPENVIKING_MTLS_URL="${OPENVIKING_MTLS_URL:-http://127.0.0.1:1943}"

"$SCRIPT_DIR/verify-svid.sh"

echo
echo "OpenClaw -> OpenViking mTLS health:"
health="$(curl -fsS --max-time 10 "$OPENVIKING_PROXY_URL/health")"
printf '%s\n' "$health"
printf '%s' "$health" | python3 -c '
import json
import sys

response = json.load(sys.stdin)
if response.get("status") != "ok":
    raise SystemExit("OpenViking health status is not ok")
'

if curl -fsS --max-time 3 "$OPENVIKING_MTLS_URL/health" >/dev/null 2>&1; then
    echo "OpenViking mTLS port unexpectedly accepted plaintext HTTP." >&2
    exit 1
fi

echo "SVID identity checks and OpenClaw -> OpenViking mTLS health passed."
