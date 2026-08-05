#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="${V2_RUNTIME_DIR:-$SCRIPT_DIR/runtime}"
export V2_RUNTIME_DIR="$RUNTIME_DIR"

if [[ "$RUNTIME_DIR" != /* ]]; then
    printf 'V2_RUNTIME_DIR must be an absolute host path: %s\n' "$RUNTIME_DIR" >&2
    exit 1
fi
if [[ ! -f "$RUNTIME_DIR/conf/openclaw-agent.conf" ]]; then
    printf 'Missing v2 runtime. Run %s/prepare.sh first.\n' "$SCRIPT_DIR" >&2
    exit 1
fi

docker compose -f "$SCRIPT_DIR/compose.center.yaml" up -d --force-recreate --no-deps \
    openclaw-agent \
    guard

guard_ready() {
    local health
    health="$(
        curl -fsS --noproxy '127.0.0.1,localhost' --max-time 2 \
            http://127.0.0.1:${V2_GUARD_PORT:-18007}/health
    )" || return 1
    printf '%s' "$health" | python3 -c '
import json
import sys

value = json.load(sys.stdin)
if value.get("status") != "OK" or value.get("mode") != "mock_allow":
    raise SystemExit(1)
if value.get("authorization_context_required") is not True:
    raise SystemExit(1)
if value.get("authorization_context_version") != "argus-authorization-v2":
    raise SystemExit(1)
ttl = value.get("decision_ttl_seconds")
if not isinstance(ttl, int) or not 1 <= ttl <= 300:
    raise SystemExit(1)
' >/dev/null 2>&1
}

for _ in $(seq 1 60); do
    if docker compose -f "$SCRIPT_DIR/compose.center.yaml" exec -T openclaw-agent \
        /opt/spire/bin/spire-agent healthcheck \
        -socketPath /opt/spire/run/openclaw/agent.sock >/dev/null 2>&1 \
        && guard_ready; then
        printf 'OpenClaw x509pop Agent and Argus Guard are ready.\n'
        exit 0
    fi
    read -r -t 1 _ || true
done

docker compose -f "$SCRIPT_DIR/compose.center.yaml" logs --tail 100 \
    openclaw-agent guard >&2
printf 'OpenClaw Agent or Argus Guard did not become healthy.\n' >&2
exit 1
