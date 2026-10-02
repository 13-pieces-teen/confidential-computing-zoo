# Experiment tool structure

The executable command paths remain stable. Example inputs are collected in `examples/`; `config/` contains configuration fragments applied before admission. Operators copy an example to a protected local path and fill actual values. Source examples contain no credentials or generated results.

| Responsibility | Entry points | Supporting modules / evidence |
|---|---|---|
| Plan, prepare, select and resume runs | `suite.py`, `runner.py` | `step.py` binds native tool receipts; `common.py` handles journals, locks and diagnostics |
| Isolated experiment deployment | `variants.py`, `static_clients.py` | `trusted_runtime.py`, `static_runtime.py`, `static-client/`; never enable weakened modes in ordinary deployment |
| E1 admission | `admission_trial.py`, `admission_stages.py`, `lifecycle_barrier.py` | Fresh Helper subscription, correlated first-rejection receipts and default-off lifecycle cuts; [stage/barrier contract](E1-STAGE-RECEIPTS.md), [reachability record](E1-REACHABILITY.md) |
| E1 offline history diagnosis | `admission_cases.py`, `history_diagnostics.py` | Signed fixtures and production history verifier; not full fresh Quote appraisal |
| E2 fault and receive timeline | `fault_trial.py`, `timeline.py`, `fault_fixture.py`, `backend_probe.py` | Production `remote_acceptance.py`, receiver collector, fixed root-local backend health observation; [fault recipes](FAULT-FIXTURES.md) |
| E3 renewal and recovery | `lifecycle_trial.py`, `lifecycle_evidence.py` | Node snapshots, fresh subscriptions, replacement launch, Helper journal cursor and actual peer SVID serial; [recipe](examples/E3-LIFECYCLE-RECIPE.md) |
| E4 private memory and local/shared faults | Adapter `fleet_business.py`, `fleet_fault.py` | [fleet fault recipe](examples/FLEET-FAULT.md); recovery stays explicit |
| E4 continuous work item and legacy workload | `continuous.py`, `continuous_work_item.py`, `continuous_proposal.py`, `continuous_gateway.mjs`, `fact_protocol.py` | [task protocol](WORK-ITEM.md); typed original proposals and audited recall, all-stage unknown-write gate; select `work-item-v1` for six steps |
| Paired continuous conditions and joint axes | `continuous_suite.py`, `continuous_analysis.py`, `continuous_plot.py` | Existing suite/runner/step integration; [two-host workflow](CONTINUOUS.md); pair only identical scenarios and protocols |
| Complete synthetic fact receipts and legal continuation | `fact_receipts.py`, `continuous_observations.py`, receiver `facts.py` | Passive ASGI matching plus run/instance-bound admission observations; [contract](FACT-RECEIPTS.md); missing independent evidence remains UNKNOWN |
| Captured proof and policy replay | `admission_evidence.py`, production `argus-verify-admission`, `argus-replay-policy` | [archive contract](ADMISSION-ARCHIVE.md); captured hardware verdict, not fresh offline DCAP |
| E4 auxiliary LoCoMo-derived workload | `locomo.py`, `locomo_run.py`, `locomo_gateway.mjs`, `locomo_execution.py`, `locomo_suite.py` | [protocol](LOCOMO.md); fixture answers remain in the local grader; recall scores do not establish safe continuation |
| E5 HTTP/TLS load and process sampling | `load.py`, `load_fleet.py`, `resources.py` | `client_material.py`; [nonempty memory load](E5-MEMORY-LOAD.md) |
| E5 three history points and two-service pending | `cost_trials.py` | Existing E1 attempt/barriers, read-only chain snapshots and approved Helper subscription probe; [recipe](E5-COST-TRIALS.md) |
| Collect and summarize | `analysis.py`, `plot.py` (also via runner) | Separate connection/workload strata, QA scores, unknown coverage and independent run intervals |
| Source delivery | `delivery.py`, `source-manifest.json` | Source content hashes; actual binary/image manifests are created by their build tools |

## Inputs and output locations

```text
experiments/argus/
  *.py, locomo_gateway.mjs  Existing operator commands and their small local modules
  README.md                Start and evidence rules
  REMOTE-RUNBOOK.md         Two-host execution order
  STRUCTURE.md             This responsibility map
  config/                  Admitted Gateway configuration fragment
  examples/                suite, load/fault/LoCoMo/lifecycle sample inputs and recipes
  tests/                   Local fixtures; not remote paper measurements
  static-client/           Isolated experimental build overlay
```

Suggested local output directories `private/`, `evidence/` and `generated/` are ignored by Git. Explicit `/secure/...` paths in the runbook remain valid; no tool requires those suggested directory names. Generated output is collected by run ID, not mixed into `examples/`.

## Change rules

- Change admission or workload behavior in its production component first. The experiment should invoke or observe that component, not silently implement a different verifier.
- Keep fixed scenario commands small. Use the existing runner only where its mutation/query/resume contract applies; there is no global multi-host daemon.
- Preserve unknown submissions and original attempts. QA completion, correct answers, local binding rejection and actual data receipt are distinct results.
- For this delivery the user runs the owning tests and integration regressions on the two hosts. Local work records focused software tests and build checks separately. Rebuild the source manifest last; remote hosts build and verify their actual artifacts separately.

The examples previously at the experiment root (`suite*.example.json`, `fault-trial.example.json`) and `config/locomo.example.json` now live under `examples/`. Only example file locations changed; CLI flags, supplied configuration semantics and installed runtime paths are unchanged.
