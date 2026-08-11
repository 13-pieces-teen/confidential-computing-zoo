# E8 multi-OpenClaw real-LLM Agent-task benchmark

This directory implements the executable part of
[`Argus-Multi-OpenClaw-Real-LLM-Agent-Task-Evaluation-Plan.md`](../../../../../documents_ly/Argus-Multi-OpenClaw-Real-LLM-Agent-Task-Evaluation-Plan.md).

It runs only on the remote Linux validation host. The local checkout contains
the harness and remote tests; it does not provide local model, OpenViking, or
TD Guest fixtures.

## Scope

Each task performs the current real business path:

1. one OpenClaw calls the model already configured on the remote host;
2. the OpenViking context-engine plugin captures the assistant response;
3. the worker validates a unique response marker in the assistant message;
4. the worker commits that session and waits for the returned task;
5. completion requires a larger `commit_count` and a changed archive overview.

The benchmark uses a closed loop: each OpenClaw runs at most one task at a
time. It measures completed Agent tasks/minute, success rate, end-to-end and
stage latency, per-unit fairness, resources, and observed attestation/Trustee
counter deltas. It does not measure raw HTTP QPS.

The first profile deliberately shares the current x509pop SPIRE Agent,
Workload API, caller SPIFFE ID, and Guard across all OpenClaw containers. The
RA/Trustee profile remains Mock. Results must not be described as independent
trusted-Agent or production capacity.

## Remote prerequisites

- The target branch is checked out on the remote Linux host.
- The checkout is clean and every harness change has been committed.
- The asymmetric runtime and current real OpenClaw/OpenViking E2E are healthy.
- `V2_RUNTIME_DIR` is the absolute active runtime directory.
- `OPENVIKING_API_KEY` is available in the invoking shell.
- The current OpenClaw uses a named config volume.
- The current OpenClaw model/provider/plugin configuration is the intended
  test profile.

The runner reads the current config, writes only non-secret fields to the
manifest, and clones the named config volume for each unit. It removes old
sessions, cache, logs, lock/PID files, workspace state, and runtime identity
from each clone. It does not modify or stop the source OpenClaw instance.

## Commands

Run the harness tests on the remote host:

```bash
bash core/spire/runtime/asymmetric/scripts/remote-agent-task-benchmark.sh unit
```

Validate the active runtime and execute the existing single-task real E2E:

```bash
V2_RUNTIME_DIR=/var/lib/argus-spire-asymmetric/run-001 \
OPENVIKING_API_KEY=... \
  bash core/spire/runtime/asymmetric/scripts/remote-agent-task-benchmark.sh preflight
```

Run only the low-cost Pilot:

```bash
V2_RUNTIME_DIR=/var/lib/argus-spire-asymmetric/run-001 \
OPENVIKING_API_KEY=... \
  bash core/spire/runtime/asymmetric/scripts/remote-agent-task-benchmark.sh pilot
```

After reviewing the separate Pilot result, run only the formal
`C1/C2/C4/C8` matrix:

```bash
V2_RUNTIME_DIR=/var/lib/argus-spire-asymmetric/run-001 \
OPENVIKING_API_KEY=... \
E8_RESULT_ROOT=/var/lib/argus-spire-asymmetric/agent-tasks \
  bash core/spire/runtime/asymmetric/scripts/remote-agent-task-benchmark.sh all
```

Formal cases can also run separately with `c1`, `c2`, `c4`, or `c8`.
Regenerate a report from an existing run with:

```bash
E8_RUN_DIR=/var/lib/argus-spire-asymmetric/agent-tasks/run-... \
  bash core/spire/runtime/asymmetric/scripts/remote-agent-task-benchmark.sh report
```

## Defaults

| Setting | Default |
|---|---:|
| Agent timeout | 180 s from T0 |
| Capture timeout | 60 s after T1 |
| Commit request timeout | 300 s |
| Archive timeout | 300 s after T3 |
| Capture poll | 1 s |
| Archive poll | 2 s |
| Resource collection | 5 s |

Override these with `E8_AGENT_TIMEOUT_MS`, `E8_CAPTURE_TIMEOUT_MS`,
`E8_COMMIT_TIMEOUT_MS`, `E8_ARCHIVE_TIMEOUT_MS`, `E8_CAPTURE_POLL_MS`,
`E8_ARCHIVE_POLL_MS`, and `E8_COLLECT_INTERVAL_SECONDS`.

Preflight runs the existing real OpenClaw plugin E2E by default. Set
`E8_PREFLIGHT_RUN_REAL_E2E=0` only when a separate, same-commit E2E receipt is
already being used for diagnosis; formal result claims still require real E2E
evidence.

## Evidence

```text
run-<UTC>/
  manifest.json
  source-revision.json
  config-profile.json
  prompts.json
  spire-metrics-{before,after}.prom
  guard-metrics-{before,after}.prom
  cases/<case>/
    metadata.json
    tasks.jsonl
    resources.jsonl
    units/<unit>/{tasks.jsonl,worker.stderr.log,container.log,launcher.log}
  summary.json
  report.md
  SHA256SUMS.txt
```

Worker failures remain task receipts; a case is structurally valid when every
planned task has a final receipt even if some tasks fail. `summary.json` marks
the C1/C2/C4/C8 formal matrix incomplete until all four non-empty cases exist,
and reports both completed-only E2E latency and all-outcome finalization
latency. Case containers are stopped after evidence capture, while their
run-scoped containers and volumes are retained for explicit later inspection
or cleanup.
