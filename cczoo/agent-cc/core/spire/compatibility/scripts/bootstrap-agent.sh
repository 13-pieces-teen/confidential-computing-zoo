#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
V2_DIR="$(cd "$SCRIPT_DIR/../../runtime/asymmetric/scripts" && pwd)"

bash "$V2_DIR/prepare.sh"
bash "$V2_DIR/start-server.sh"
exec bash "$V2_DIR/start-openclaw-agent.sh"
