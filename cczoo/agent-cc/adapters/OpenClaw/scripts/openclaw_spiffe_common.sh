#!/usr/bin/env bash
# Shared by connection and business acceptance. No global fetch modifications.
OPENCLAW_CONTAINER="${OPENCLAW_CONTAINER:-agentcc-openclaw-sbx-gateway}"
OPENCLAW_USER="${OPENCLAW_USER:-node}"
OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-/home/node/.openclaw/openclaw.json}"
OPENVIKING_SPIFFE_CONFIG="${OPENVIKING_SPIFFE_CONFIG:-/etc/argus-openclaw/client.json}"
export OPENVIKING_API_KEY="${OPENVIKING_API_KEY:-}"
oc() {
    docker exec -i -u "$OPENCLAW_USER" -e OPENCLAW_CONFIG_PATH="$OPENCLAW_CONFIG_PATH" \
        -e OPENVIKING_SPIFFE_CONFIG="$OPENVIKING_SPIFFE_CONFIG" -e OPENVIKING_API_KEY "$OPENCLAW_CONTAINER" "$@"
}
spiffe_preflight() {
    command -v docker >/dev/null
    [[ "$(docker inspect "$OPENCLAW_CONTAINER" --format '{{.State.Running}}')" == true ]] || { echo 'OpenClaw container must be running' >&2; return 1; }
    [[ -n "$OPENVIKING_API_KEY" ]] || { echo 'OPENVIKING_API_KEY must be a non-root OpenViking user key' >&2; return 1; }
    local mounts
    mounts="$(docker inspect "$OPENCLAW_CONTAINER" --format '{{json .Mounts}}')"
    # Check persistent gateway environment, not docker exec -e overrides.
    docker exec -i -u "$OPENCLAW_USER" "$OPENCLAW_CONTAINER" node - "$OPENVIKING_SPIFFE_CONFIG" "$mounts" <<'NODE'
const fs = require('node:fs');
const path = require('node:path');
const [major, minor] = process.versions.node.split('.').map(Number);
if (major < 22 || (major === 22 && minor < 17)) throw new Error('Node >=22.17.0 is required');
const expected = process.argv[2];
if ((process.env.OPENVIKING_SPIFFE_CONFIG || '/etc/argus-openclaw/client.json') !== expected) throw new Error('Persist OPENVIKING_SPIFFE_CONFIG in the gateway container environment');
const config = JSON.parse(fs.readFileSync(expected, 'utf8'));
const mounts = JSON.parse(process.argv[3]);
for (const target of [expected, config.credentialsDir]) {
  if (typeof target !== 'string' || !path.isAbsolute(target)) throw new Error('Absolute config and credentials paths required');
  const mount = mounts.filter(item => target === item.Destination || target.startsWith(item.Destination.replace(/\/$/, '') + '/'))
    .sort((a, b) => b.Destination.length - a.Destination.length)[0];
  if (!mount || mount.RW) throw new Error(`Mount ${target} read-only into the OpenClaw container`);
}
if (new URL(config.origin).protocol !== 'https:') throw new Error('HTTPS origin required');
console.log(JSON.stringify({node: process.versions.node, origin: config.origin, client_spiffe_id: config.clientSpiffeId, server_spiffe_id: config.serverSpiffeId}));
NODE
    oc node - <<'NODE'
const { execFileSync } = require('node:child_process');
const version = execFileSync('openclaw', ['--version'], {encoding:'utf8'}).trim();
const match = version.match(/(\d{4})\.(\d+)\.(\d+)/);
if (!match || Number(match[1])*10000 + Number(match[2])*100 + Number(match[3]) < 20260408) throw new Error('Pinned plugin requires OpenClaw >=2026.4.8');
console.log(JSON.stringify({openclaw: version}));
NODE
}
resolve_spiffe_plugin() {
    OPENCLAW_PLUGIN_DIR="$(oc node - "${OPENCLAW_PLUGIN_DIR:-}" <<'NODE'
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
let candidates = process.argv[2] ? [process.argv[2]] : [];
if (!candidates.length) {
  const installs = JSON.parse(execFileSync('openclaw', ['config', 'get', 'plugins.installs', '--json'], {encoding:'utf8'}));
  candidates = Object.values(installs).map(item => item.installPath).filter(Boolean);
}
const matches = candidates.filter(directory => {
  try { const pkg = JSON.parse(fs.readFileSync(path.join(directory, 'package.json'), 'utf8'));
    return pkg.name === '@openviking/openclaw-plugin' && pkg.version === '2026.6.18' && pkg.argusSpiffe?.revision === 'argus.2';
  } catch { return false; }
});
if (matches.length !== 1) throw new Error('Expected one installed Argus plugin; set OPENCLAW_PLUGIN_DIR to its container directory');
process.stdout.write(matches[0]);
NODE
)"
    [[ -n "$OPENCLAW_PLUGIN_DIR" ]]
    TARGET_URI="$(oc node - "$OPENVIKING_SPIFFE_CONFIG" <<'NODE'
process.stdout.write(new URL(JSON.parse(require('node:fs').readFileSync(process.argv[2], 'utf8')).origin).origin);
NODE
)"
}
spiffe_health() { oc node "$OPENCLAW_PLUGIN_DIR/dist/argus-spiffe/cli.mjs" health "${1:-/health}"; }
