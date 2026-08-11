#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPIRE_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

[[ $# -eq 1 ]] || {
    echo 'usage: remote-agent-task-benchmark.sh [unit|preflight|pilot|c1|c2|c4|c8|all|report]' >&2
    exit 2
}
exec bash "$SPIRE_ROOT/benchmarks/asymmetric/agent-tasks/run.sh" "$1"
