#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="${V2_RUNTIME_DIR:-$SCRIPT_DIR/runtime}"
export V2_RUNTIME_DIR="$RUNTIME_DIR"

if [[ "$RUNTIME_DIR" != /* ]]; then
    printf 'V2_RUNTIME_DIR must be an absolute host path: %s\n' "$RUNTIME_DIR" >&2
    exit 1
fi
V2_MTLS_RUNTIME_IMAGE="${V2_MTLS_RUNTIME_IMAGE:-$(
    docker image inspect argus-spire-v2-mtls:local --format '{{.Id}}'
)}"
export V2_MTLS_RUNTIME_IMAGE

docker compose -f "$SCRIPT_DIR/compose.center.yaml" \
    --profile workload \
    up -d --force-recreate --no-deps openclaw-mtls-client

for _ in $(seq 1 30); do
    if docker compose -f "$SCRIPT_DIR/compose.center.yaml" \
        --profile workload \
        exec -T openclaw-mtls-client \
        /spire-mtls identity \
        -socket=unix:///opt/spire/run/openclaw/agent.sock \
        -expected-id=spiffe://argus.local/agent/openclaw \
        >/dev/null 2>&1; then
        printf 'OpenClaw SPIFFE mTLS workload is ready.\n'
        exit 0
    fi
    read -r -t 1 _ || true
done

docker compose -f "$SCRIPT_DIR/compose.center.yaml" \
    --profile workload \
    logs --tail 80 openclaw-mtls-client >&2
printf 'OpenClaw workload did not receive its X.509-SVID.\n' >&2
exit 1
