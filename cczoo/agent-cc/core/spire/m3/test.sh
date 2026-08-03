#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
MODULE_DIR="$REPO_ROOT/core/spire/plugins/argus-tdx-nodeattestor"
GO_CACHE_DIR="${ARGUS_GO_CACHE_DIR:-/tmp/argus-go-cache}"

"$SCRIPT_DIR/prepare.sh"

mkdir -p "$SCRIPT_DIR/runtime/m3"
case_directory="$(mktemp -d "$SCRIPT_DIR/runtime/m3/run-XXXXXXXX")"
export M4_SERVER_DATA_DIR="$case_directory/server-data"
export M4_AGENT_DATA_DIR="$case_directory/agent-data"
mkdir -p "$M4_SERVER_DATA_DIR" "$M4_AGENT_DATA_DIR/argus-tdx"
chmod 0700 "$M4_SERVER_DATA_DIR" "$M4_AGENT_DATA_DIR" "$M4_AGENT_DATA_DIR/argus-tdx"
chown 1000:1000 "$M4_SERVER_DATA_DIR"

docker run --rm \
    -e GOPROXY=off -e GOSUMDB=off -e GOMODCACHE=/gomodcache \
    -v "$MODULE_DIR:/workspace" -v "$GO_CACHE_DIR:/gomodcache" \
    -w /workspace golang:1.24-bookworm go test -mod=readonly ./...

cd "$SCRIPT_DIR"
docker compose config --quiet
docker compose up -d fake-services

docker compose run --rm --no-deps \
    --entrypoint /opt/spire/bin/spire-server spire-server \
    validate -config /opt/spire/conf/server.conf

docker compose run --rm --no-deps \
    --entrypoint /opt/spire/bin/spire-agent spire-agent \
    validate -config /opt/spire/conf/agent.conf

docker compose up -d --wait --force-recreate
"$SCRIPT_DIR/verify.sh"
