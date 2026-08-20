#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROFILE_DIR/compose.yaml"
RUNTIME_DIR="${V2_RUNTIME_DIR:-$PROFILE_DIR/runtime}"
export V2_RUNTIME_DIR="$RUNTIME_DIR"
SERVER_SOCKET="/opt/spire/run/server/api.sock"

if [[ "$RUNTIME_DIR" != /* ]]; then
    printf 'V2_RUNTIME_DIR must be an absolute host path: %s\n' "$RUNTIME_DIR" >&2
    exit 1
fi
bash "$SCRIPT_DIR/verify-mtls.sh"

entries="$(
    docker compose -f "$COMPOSE_FILE" exec -T spire-server \
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
    "spiffe://argus.local/infra/openviking-broker": None,
}
selectors = {}
additional_attributes = {}
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
    selectors[identity] = {
        "{}:{}".format(selector.get("type"), selector.get("value"))
        for selector in entry.get("selectors", [])
    }
    additional_attributes[identity] = entry.get("additional_attributes") or {}
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
if expected["spiffe://argus.local/infra/openviking-broker"] != expected[
    "spiffe://argus.local/service/openviking-cmem"
]:
    raise SystemExit("Broker and OpenViking target entries do not share the OpenViking Agent parent")

target_selectors = selectors["spiffe://argus.local/service/openviking-cmem"]
required_target = {
    "docker:label:argus.workload:openviking-cmem",
    "argus_tdx_workload:verified:true",
    "argus_tdx_workload:workload_id:openviking-cmem",
    "argus_tdx_workload:policy:openviking-cmem-v1",
}
missing_target = required_target - target_selectors
if missing_target:
    raise SystemExit("OpenViking target entry is missing selectors: {}".format(sorted(missing_target)))
if not additional_attributes["spiffe://argus.local/service/openviking-cmem"].get(
    "disable_x509_svid_prefetch", False
):
    raise SystemExit("OpenViking target entry does not disable X.509-SVID prefetch")
for prefix in ("docker:image_id:", "docker:image_config_digest:sha256:"):
    if not any(selector.startswith(prefix) for selector in target_selectors):
        raise SystemExit("OpenViking target entry is missing a {} selector".format(prefix))

broker_selectors = selectors["spiffe://argus.local/infra/openviking-broker"]
if "docker:label:argus.component:openviking-broker" not in broker_selectors:
    raise SystemExit("Broker entry is missing its dedicated Docker label selector")
for prefix in ("docker:image_id:", "docker:image_config_digest:sha256:"):
    if not any(selector.startswith(prefix) for selector in broker_selectors):
        raise SystemExit("Broker entry is missing a {} selector".format(prefix))
'

printf '%s\n' \
    'Argus SPIFFE v2 architecture validation passed.' \
    'Two independent Agents, x509pop plus argus_tdx, caller-local Guard, and Broker Sidecar SPIFFE mTLS were observed.' \
    'The OpenViking target entry requires both Docker identity and verified argus_tdx_workload selectors.'
