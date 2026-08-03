#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "$SCRIPT_DIR/runtime/conf/openclaw-agent.conf" ]]; then
    printf 'Missing v2 runtime. Run %s/prepare.sh first.\n' "$SCRIPT_DIR" >&2
    exit 1
fi

docker compose -f "$SCRIPT_DIR/compose.center.yaml" up -d \
    openclaw-agent \
    guard

for _ in $(seq 1 60); do
    if docker compose -f "$SCRIPT_DIR/compose.center.yaml" exec -T openclaw-agent \
        /opt/spire/bin/spire-agent healthcheck \
        -socketPath /opt/spire/run/openclaw/agent.sock >/dev/null 2>&1 \
        && curl -fsS --max-time 2 http://127.0.0.1:${V2_GUARD_PORT:-18007}/health \
            >/dev/null 2>&1; then
        printf 'OpenClaw x509pop Agent and Argus Guard are ready.\n'
        exit 0
    fi
    read -r -t 1 _ || true
done

docker compose -f "$SCRIPT_DIR/compose.center.yaml" logs --tail 100 \
    openclaw-agent guard >&2
printf 'OpenClaw Agent or Argus Guard did not become healthy.\n' >&2
exit 1
