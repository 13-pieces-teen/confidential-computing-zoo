#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPIRE_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

exec bash "$SPIRE_ROOT/benchmarks/asymmetric/agent-tasks/run.sh" "${1:-all}"
