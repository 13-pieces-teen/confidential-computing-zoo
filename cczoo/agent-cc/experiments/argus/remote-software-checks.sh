#!/usr/bin/env bash
# Run on the two test hosts, not as a substitute for real TDX/Agent acceptance.
set -euo pipefail
ARGUS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ARGUS_ROOT"
role="${1:?usage: bash remote-software-checks.sh client|server|analysis}"
case "$role" in
  client)
    bash adapters/OpenClaw/spiffe_client/test-client.sh
    python3 -m pytest experiments/argus/tests/test_continuous.py \
      experiments/argus/tests/test_continuous_analysis.py \
      experiments/argus/tests/test_continuous_suite.py \
      experiments/argus/tests/test_step.py experiments/argus/tests/test_runner.py \
      experiments/argus/tests/test_runner_evidence.py -q -rs
    ;;
  server)
    bash core/spire/workload/scripts/build.sh
    python3 -m unittest discover -s adapters/OpenViking/receiver_audit/tests -v
    python3 -m pytest experiments/argus/tests/test_fact_receipts.py \
      experiments/argus/tests/test_admission_evidence.py \
      experiments/argus/tests/test_admission_trial.py \
      experiments/argus/tests/test_history_diagnostics.py \
      experiments/argus/tests/test_lifecycle_evidence.py \
      experiments/argus/tests/test_lifecycle_trial.py \
      experiments/argus/tests/test_quote_counters.py \
      experiments/argus/tests/test_connection_facts.py \
      experiments/argus/tests/test_inflight_gate.py -q -rs
    ;;
  analysis)
    python3 -m pytest experiments/argus/tests -q -rs
    ;;
  *) printf 'Unknown role: %s\n' "$role" >&2; exit 2 ;;
esac
printf 'SOFTWARE_CHECK_COMMANDS_COMPLETED role=%s; remote acceptance is separate\n' "$role"
