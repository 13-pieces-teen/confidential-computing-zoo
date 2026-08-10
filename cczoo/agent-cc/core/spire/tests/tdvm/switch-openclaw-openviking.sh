#!/usr/bin/env bash
set -euo pipefail

TARGET_URI="${1:-}"
OPENCLAW_CONTAINER="${OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
OPENCLAW_USER="${OPENCLAW_USER:-node}"
OPENCLAW_CONFIG="${OPENCLAW_CONFIG:-/home/node/.openclaw/openclaw.json}"
BACKUP_ID="$(date -u +%Y%m%dT%H%M%SZ)"

if [[ ! "$TARGET_URI" =~ ^https?://[^/]+(:[0-9]+)?$ ]]; then
    printf 'Usage: %s http[s]://host:port\n' "$0" >&2
    exit 1
fi
TARGET_URI="${TARGET_URI%/}"

curl -fsS --max-time 10 "$TARGET_URI/health" >/dev/null
curl -fsS --max-time 10 "$TARGET_URI/ready" >/dev/null
docker inspect "$OPENCLAW_CONTAINER" >/dev/null

docker exec -i -u "$OPENCLAW_USER" "$OPENCLAW_CONTAINER" \
    node - "$OPENCLAW_CONFIG" "$TARGET_URI" "$BACKUP_ID" <<'NODE'
const fs = require('node:fs');

const [, , configPath, targetUri, backupId] = process.argv;
const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
const plugin = config.plugins?.entries?.openviking?.config;
if (!plugin || plugin.mode !== 'remote') {
  throw new Error('OpenViking remote plugin configuration is missing');
}
if (!plugin.apiKey) {
  throw new Error('OpenViking plugin API key is missing');
}

const backupPath = `${configPath}.argus-backup-${backupId}`;
const temporaryPath = `${configPath}.argus-tmp-${process.pid}`;
const mode = fs.statSync(configPath).mode;
fs.copyFileSync(configPath, backupPath, fs.constants.COPYFILE_EXCL);
plugin.baseUrl = targetUri;
fs.writeFileSync(temporaryPath, `${JSON.stringify(config, null, 2)}\n`, { mode });
fs.renameSync(temporaryPath, configPath);
console.log(`OpenClaw OpenViking URL updated: ${targetUri}`);
console.log(`Previous configuration retained: ${backupPath}`);
NODE

docker restart "$OPENCLAW_CONTAINER" >/dev/null
for _ in $(seq 1 60); do
    if docker inspect "$OPENCLAW_CONTAINER" --format '{{.State.Running}}' 2>/dev/null | grep -qx true; then
        break
    fi
    read -r -t 1 _ || true
done

docker exec -i -u "$OPENCLAW_USER" "$OPENCLAW_CONTAINER" \
    node - "$OPENCLAW_CONFIG" "$TARGET_URI" <<'NODE'
const config = require(process.argv[2]);
const targetUri = process.argv[3];
const plugin = config.plugins?.entries?.openviking?.config;

async function verify() {
  if (plugin?.baseUrl?.replace(/\/+$/, '') !== targetUri || !plugin.apiKey) {
    throw new Error('OpenClaw configuration did not persist the target URL and API key');
  }
  const response = await fetch(`${targetUri}/api/v1/sessions?limit=1`, {
    headers: { 'X-API-Key': plugin.apiKey },
  });
  if (!response.ok) throw new Error(`OpenViking sessions returned HTTP ${response.status}`);
  const payload = await response.json();
  if (!payload || typeof payload !== 'object' || !('result' in payload)) {
    throw new Error('OpenViking sessions response is malformed');
  }
  console.log(`OpenClaw -> OpenViking sessions: HTTP ${response.status}`);
}

verify().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
NODE
