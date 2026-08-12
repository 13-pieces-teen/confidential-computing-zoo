#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROFILE_DIR/compose.yaml"
RUNTIME_DIR="${V2_RUNTIME_DIR:-$PROFILE_DIR/runtime}"
export V2_RUNTIME_DIR="$RUNTIME_DIR"

if [[ "$RUNTIME_DIR" != /* ]]; then
    printf 'V2_RUNTIME_DIR must be an absolute host path: %s\n' "$RUNTIME_DIR" >&2
    exit 1
fi
if [[ ! -f "$RUNTIME_DIR/conf/openclaw-agent.conf" ]]; then
    printf 'Missing v2 runtime. Run %s/prepare.sh first.\n' "$SCRIPT_DIR" >&2
    exit 1
fi

docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps \
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
if value.get("status") != "OK" or value.get("mode") != "spiffe_identity":
    raise SystemExit(1)
ttl = value.get("decision_ttl_seconds")
if not isinstance(ttl, int) or not 1 <= ttl <= 300:
    raise SystemExit(1)
' >/dev/null 2>&1
}

for _ in $(seq 1 60); do
    if docker compose -f "$COMPOSE_FILE" exec -T openclaw-agent \
        /opt/spire/bin/spire-agent healthcheck \
        -socketPath /opt/spire/run/openclaw/agent.sock >/dev/null 2>&1 \
        && guard_ready; then
        printf 'OpenClaw x509pop Agent and Argus Guard are ready.\n'
        exit 0
    fi
    read -r -t 1 _ || true
done

docker compose -f "$COMPOSE_FILE" logs --tail 100 \
    openclaw-agent guard >&2
printf 'OpenClaw Agent or Argus Guard did not become healthy.\n' >&2
exit 1
