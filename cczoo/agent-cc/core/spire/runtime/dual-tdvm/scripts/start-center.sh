#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROFILE_DIR/compose.yaml"
RUNTIME_DIR="${DUAL_RUNTIME_DIR:-$PROFILE_DIR/runtime}"
export DUAL_RUNTIME_DIR="$RUNTIME_DIR"

if [[ "$RUNTIME_DIR" != /* ]]; then
    printf 'DUAL_RUNTIME_DIR must be an absolute host path: %s\n' "$RUNTIME_DIR" >&2
    exit 1
fi
if [[ ! -f "$RUNTIME_DIR/conf/server.conf" ]]; then
    printf 'Missing dual-TDVM runtime. Run %s/prepare.sh first.\n' "$SCRIPT_DIR" >&2
    exit 1
fi

# Keep the shared control plane outside both TDVMs; guests run Agents only.
docker compose -f "$COMPOSE_FILE" up -d --force-recreate \
    mock-trustee \
    spire-server

# Do not deploy or register guests until the Server admin socket is usable.
for _ in $(seq 1 60); do
    if docker compose -f "$COMPOSE_FILE" exec -T spire-server \
        /opt/spire/bin/spire-server healthcheck \
        -socketPath /opt/spire/run/server/api.sock >/dev/null 2>&1; then
        printf '%s\n' \
            'Dual-TDVM SPIRE center is ready.' \
            'The center contains only SPIRE Server and the mock Trustee.'
        exit 0
    fi
    read -r -t 1 _ || true
done

docker compose -f "$COMPOSE_FILE" logs --tail 100 \
    mock-trustee spire-server >&2
printf 'Dual-TDVM SPIRE center did not become healthy.\n' >&2
exit 1
