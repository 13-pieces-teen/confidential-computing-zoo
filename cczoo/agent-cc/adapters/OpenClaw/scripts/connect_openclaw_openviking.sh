#!/usr/bin/env bash
# Copyright (c) 2026 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/openclaw_spiffe_common.sh"
PHASE="${1:-connect}"
OPENCLAW_RESTART_GATEWAY="${OPENCLAW_RESTART_GATEWAY:-0}"
OPENVIKING_REQUIRE_READY="${OPENVIKING_REQUIRE_READY:-0}"
OPENCLAW_PLUGIN_ARCHIVE="${OPENCLAW_PLUGIN_ARCHIVE:-$SCRIPT_DIR/../spiffe_client/dist/openviking-openclaw-plugin-2026.6.18-argus.2.tgz}"
WAIT_ATTEMPTS="${WAIT_ATTEMPTS:-60}"
WAIT_INTERVAL="${WAIT_INTERVAL:-2}"
# Restart changes the attested process instance; it needs a new PID registration.
[[ "$OPENCLAW_RESTART_GATEWAY" == 0 ]] || { echo 'Restart requires a new OpenClaw PID registration; use the documented lifecycle sequence' >&2; exit 1; }
[[ "$PHASE" == install || "$PHASE" == connect ]] || { echo 'Usage: connect_openclaw_openviking.sh [install|connect]' >&2; exit 1; }
[[ "$OPENVIKING_REQUIRE_READY" == 0 || "$OPENVIKING_REQUIRE_READY" == 1 ]] || { echo 'OPENVIKING_REQUIRE_READY must be 0 or 1' >&2; exit 1; }
spiffe_preflight
if [[ "$PHASE" == install ]]; then
    digest="$(python3 - "$OPENCLAW_PLUGIN_ARCHIVE" <<'PY'
import hashlib, json, pathlib, sys
p = pathlib.Path(sys.argv[1])
receipt = json.loads(p.with_suffix('.json').read_text())
digest = hashlib.sha256(p.read_bytes()).hexdigest()
if digest != receipt['sha256'] or receipt['customization'] != 'argus.2':
    raise SystemExit('Plugin artifact digest/revision mismatch; rebuild with build_plugin.py')
print(digest)
PY
)"
    remote_archive="/tmp/argus-openviking-$digest.tgz"
    trap 'docker exec "$OPENCLAW_CONTAINER" rm -f -- "$remote_archive" >/dev/null 2>&1 || true' EXIT
    docker cp "$OPENCLAW_PLUGIN_ARCHIVE" "$OPENCLAW_CONTAINER:$remote_archive" >/dev/null
    oc openclaw plugins install "$remote_archive" --force
fi
resolve_spiffe_plugin
if [[ "$PHASE" == install ]]; then
    printf '%s\n' 'Pinned plugin installed. Start/restart the gateway, register its actual PID, and start credential delivery before running connect.'
    exit 0
fi
ready=0
for ((attempt=1; attempt<=WAIT_ATTEMPTS; attempt++)); do
    if spiffe_health && { [[ "$OPENVIKING_REQUIRE_READY" == 0 ]] || spiffe_health /ready; }; then ready=1; break; fi
    sleep "$WAIT_INTERVAL"
done
[[ "$ready" == 1 ]] || { echo 'OpenViking SPIFFE mTLS health check failed' >&2; exit 1; }
oc openclaw openviking setup --base-url "$TARGET_URI" --api-key "$OPENVIKING_API_KEY" --json
oc openclaw openviking status --json
printf '%s\n' 'CLI mTLS and plugin configuration ready. Complete the gateway restart + PID re-registration sequence before business acceptance.'
