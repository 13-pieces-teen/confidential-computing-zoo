#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENVIKING_URL="${OPENVIKING_URL:-http://127.0.0.1:2933}"
OPENCLAW_CONTAINER="${OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
OPENCLAW_USER="${OPENCLAW_USER:-node}"
TDVM_SSH_TARGET="${TDVM_SSH_TARGET:-tdx@127.0.0.1}"
TDVM_SSH_PORT="${TDVM_SSH_PORT:-2222}"
TDVM_SSH_IDENTITY="${TDVM_SSH_IDENTITY:-}"
RUN_MOCK_V2_MATRIX="${RUN_MOCK_V2_MATRIX:-1}"

ssh_options=(-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -p "$TDVM_SSH_PORT")
if [[ -n "$TDVM_SSH_IDENTITY" ]]; then
    ssh_options+=(-i "$TDVM_SSH_IDENTITY")
fi

curl -fsS --max-time 10 "$OPENVIKING_URL/health" >/dev/null
curl -fsS --max-time 10 "$OPENVIKING_URL/ready" >/dev/null

ssh "${ssh_options[@]}" "$TDVM_SSH_TARGET" \
    'test -c /dev/tdx_guest && test "$(cat /sys/module/tdx_guest/initstate)" = live && curl -fsS --max-time 10 http://127.0.0.1:1933/health >/dev/null'

docker inspect "$OPENCLAW_CONTAINER" >/dev/null
docker exec -i -u "$OPENCLAW_USER" "$OPENCLAW_CONTAINER" node - "$OPENVIKING_URL" <<'NODE'
const config = require('/home/node/.openclaw/openclaw.json');

async function main() {
  const expectedUrl = process.argv[2].replace(/\/+$/, '');
  const plugin = config.plugins?.entries?.openviking?.config;
  if (!plugin || plugin.mode !== 'remote' || plugin.baseUrl?.replace(/\/+$/, '') !== expectedUrl) {
    throw new Error(`OpenViking plugin is not configured for ${expectedUrl}`);
  }
  if (!plugin.apiKey) throw new Error('OpenViking plugin API key is missing');

  const response = await fetch(`${expectedUrl}/api/v1/sessions?limit=1`, {
    headers: { 'X-API-Key': plugin.apiKey },
  });
  if (!response.ok) throw new Error(`OpenViking sessions returned HTTP ${response.status}`);
  const payload = await response.json();
  if (!payload || typeof payload !== 'object' || !('result' in payload)) {
    throw new Error('OpenViking sessions response is malformed');
  }
  console.log(`OpenClaw -> OpenViking at ${expectedUrl}: HTTP ${response.status}`);
  console.log(`Response keys: ${Object.keys(payload).sort().join(',')}`);
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
NODE

if [[ "$RUN_MOCK_V2_MATRIX" == "1" ]]; then
    "$SCRIPT_DIR/test-failures.sh"
fi

printf 'v2 architecture validation passed\n'
printf 'Business path: real OpenClaw -> real OpenViking v0.4.8 at %s\n' "$OPENVIKING_URL"
printf 'TD VM placement: Guest TDX device/module and Guest-local health passed\n'
printf 'Attestation path: mock Evidence Provider and mock Trustee\n'
printf 'Real Quote/QGS acceptance: deferred\n'
