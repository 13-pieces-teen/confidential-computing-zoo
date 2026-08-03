#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_SOCKET="/opt/spire/run/server/api.sock"

"$SCRIPT_DIR/verify-mtls.sh"

entries="$(
    docker compose -f "$SCRIPT_DIR/compose.center.yaml" exec -T spire-server \
        /opt/spire/bin/spire-server entry show \
        -socketPath "$SERVER_SOCKET" \
        -output json
)"
printf '%s' "$entries" | python3 -c '
import json
import sys

entries = json.load(sys.stdin).get("entries", [])
expected = {
    "spiffe://argus.local/agent/openclaw": None,
    "spiffe://argus.local/service/openviking-cmem": None,
}
for entry in entries:
    identity = entry.get("spiffe_id")
    if isinstance(identity, dict):
        identity = "spiffe://{}{}".format(identity["trust_domain"], identity["path"])
    if identity not in expected:
        continue
    parent = entry.get("parent_id")
    if isinstance(parent, dict):
        parent = "spiffe://{}{}".format(parent["trust_domain"], parent["path"])
    expected[identity] = str(parent)
if any(parent is None for parent in expected.values()):
    raise SystemExit("one or more v2 workload registration entries are missing")
if expected["spiffe://argus.local/agent/openclaw"] == expected[
    "spiffe://argus.local/service/openviking-cmem"
]:
    raise SystemExit("v2 workload entries share one parent")
if "/spire/agent/x509pop/" not in expected[
    "spiffe://argus.local/agent/openclaw"
]:
    raise SystemExit("OpenClaw workload is not parented by the x509pop Agent")
if "/spire/agent/argus_tdx/" not in expected[
    "spiffe://argus.local/service/openviking-cmem"
]:
    raise SystemExit("OpenViking workload is not parented by the argus_tdx Agent")
'

printf '%s\n' \
    'Argus SPIFFE v2 architecture validation passed.' \
    'Two independent Agents, two Workload APIs, x509pop plus argus_tdx, split mocks, and SPIFFE mTLS were observed.'
