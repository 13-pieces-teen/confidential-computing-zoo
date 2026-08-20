# Argus asymmetric SPIFFE benchmark

This directory implements E3-E7 from
[历史非对称 Profile 评估方案](../../../../documents_ly/archive/pre-dual-tdvm-broker-sidecar/Argus-Asymmetric-Attestation-SPIFFE-Evaluation-Plan.md)。

The benchmark runs only on the remote Linux validation host after the formal
asymmetric runtime has passed its functional validation. It does not start a
new OpenViking identity, repeat the attestation failure matrix, or claim real
Quote/QGS/production Trustee performance.

The E3-E7 runner has not yet been adapted to the Broker Sidecar ownership
model: its collector still expects an OpenViking-local SVID status file. Run
only its unit tests for now; do not use E3-E7 as a Broker Sidecar acceptance
result until the collector measures the Sidecar without exporting target key
material.

The separate [`agent-tasks/`](./agent-tasks/) harness implements E8: real LLM
generation from `1/2/4/8` OpenClaw containers into one OpenViking, measured in
completed Agent tasks/minute rather than raw QPS.

## What is measured

| Action | Measurement |
|---|---|
| `e3` | Rust Guard decision throughput, latency, and resources |
| `e4` | Guarded new connections, formal guarded keep-alive, and diagnostic mTLS-only baseline |
| `e5` | Full OpenClaw -> Guard -> SPIFFE mTLS -> OpenViking QPS/concurrency steps |
| `e6` | Full path under load across at least three observed SVID TTL periods |
| `e7` | Derived business-request, attestation, Trustee, and SVID-rotation ratios |
| `all` | E3-E6 followed by the E7 report |

The formal data-plane result is always the `guarded` profile, which uses the
same OpenClaw preload and rotating workload SVID as the application. The
`diagnostic-mtls-only` profile is never promoted to a business result.

## Remote prerequisites

1. Build/restart the branch's Guard and OpenClaw images with the normal
   asymmetric `prepare.sh` and startup flow.
2. Complete `remote-test.sh all`.
3. Export the same absolute `V2_RUNTIME_DIR`, TDVM SSH variables, and target
   origin used by the validated runtime.
4. Ensure the current user can write the benchmark result root.

The benchmark does not require an OpenViking API key because its capacity path
uses the deterministic `/health` endpoint through the real Guard and
SPIFFE mTLS transport. The model-backed `commit/archive` path remains a
separate low-rate functional acceptance check.

## Commands

Run tool unit tests on the remote host:

```bash
bash core/spire/runtime/asymmetric/scripts/remote-benchmark.sh unit
```

Verify the already-running environment without collecting a result:

```bash
V2_RUNTIME_DIR=/var/lib/argus-spire-asymmetric/run-001 \
  bash core/spire/runtime/asymmetric/scripts/remote-benchmark.sh preflight
```

Run the complete current evaluation:

```bash
V2_RUNTIME_DIR=/var/lib/argus-spire-asymmetric/run-001 \
ARGUS_BENCHMARK_RESULT_ROOT=/var/lib/argus-spire-asymmetric/benchmarks \
  bash core/spire/runtime/asymmetric/scripts/remote-benchmark.sh all
```

Each experiment can also run independently as `e3`, `e4`, `e5`, or `e6`.
To add later experiments to the same explicit run directory, export
`ARGUS_BENCHMARK_RUN_DIR`. Existing case directories are never overwritten.

Regenerate E7 and the reports from an existing run:

```bash
ARGUS_BENCHMARK_RUN_DIR=/var/lib/argus-spire-asymmetric/benchmarks/run-... \
  bash core/spire/runtime/asymmetric/scripts/remote-benchmark.sh e7
```

## Tunable load parameters

| Variable | Default |
|---|---|
| `E3_REQUESTS_PER_STEP` | `0` (duration-bounded) |
| `E3_STEP_DURATION_SECONDS` | `30` |
| `E3_RECORD_EVERY` | `10` requests |
| `E3_CONCURRENCY_STEPS` | `1,4,8,16,32` |
| `E4_REQUESTS_PER_PROFILE` | `2000` |
| `E4_CONCURRENCY` | `8` |
| `E5_QPS_STEPS` | `10,25,50,100` |
| `E5_STEP_DURATION_SECONDS` | `60` |
| `E5_CONCURRENCY` | `32` |
| `E6_QPS` | `10` |
| `E6_CONCURRENCY` | `8` |
| `E6_DURATION_SECONDS` | three current SVID validity periods plus 60 seconds |
| `ARGUS_BENCHMARK_COLLECT_INTERVAL_SECONDS` | `5` |
| `ARGUS_BENCHMARK_WARMUP_REQUESTS` | `20` |
| `ARGUS_BENCHMARK_COOLDOWN_SECONDS` | `2` |

These are exploratory defaults, not production SLOs. Increase the QPS range
only after inspecting the previous step's latency, errors, CPU, RSS, file
descriptors, and connection counts.

## Result layout

```text
run-<UTC>/
  manifest.json
  spire-metrics-before.prom
  spire-metrics-after.prom
  guard-metrics-before.prom
  guard-metrics-after.prom
  cases/<case>/
    metadata.json
    requests.jsonl
    resources.jsonl
    load-generator.stderr.log
    collector.stderr.log
  summary.json
  report.md
```

`requests.jsonl` contains per-request and optional G0-G3 transport events.
`resources.jsonl` contains host, host-container, TD Guest-container, SPIRE,
Guard, connection, and SVID samples on a shared run timeline. Missing samples
remain `null`/`N/A`; target values are not substituted for observations.

## Evidence boundary

Results from this directory establish only the mock-stage software data-path
overhead, resource curve, stable capacity on the named host, SVID-rotation
continuity, and attestation-call amortization. They do not establish real TDX
Quote latency, QGS behavior, Trustee collateral/TCB verification, multi-client
capacity, or production acceptance.
