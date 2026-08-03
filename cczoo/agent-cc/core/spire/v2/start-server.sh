#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "$SCRIPT_DIR/runtime/conf/server.conf" ]]; then
    printf 'Missing v2 runtime. Run %s/prepare.sh first.\n' "$SCRIPT_DIR" >&2
    exit 1
fi

docker compose -f "$SCRIPT_DIR/compose.center.yaml" up -d \
    mock-trustee \
    spire-server

for _ in $(seq 1 60); do
    if docker compose -f "$SCRIPT_DIR/compose.center.yaml" exec -T spire-server \
        /opt/spire/bin/spire-server healthcheck \
        -socketPath /opt/spire/run/server/api.sock >/dev/null 2>&1; then
        printf 'SPIRE Server and independent mock Trustee are ready.\n'
        exit 0
    fi
    read -r -t 1 _ || true
done

docker compose -f "$SCRIPT_DIR/compose.center.yaml" logs --tail 100 \
    mock-trustee spire-server >&2
printf 'SPIRE Server did not become healthy.\n' >&2
exit 1
