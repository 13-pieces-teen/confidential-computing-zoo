# Experiment tool structure

The executable command paths remain stable. Example inputs are collected in `examples/`; `config/` contains configuration fragments applied before admission. Operators copy an example to a protected local path and fill actual values. Source examples contain no credentials or generated results.

| Responsibility | Entry points | Supporting modules / evidence |
|---|---|---|
| Plan, prepare, select and resume runs | `suite.py`, `runner.py` | `step.py` binds native tool receipts; `common.py` handles journals, locks and diagnostics |
| Isolated experiment deployment | `variants.py`, `static_clients.py` | `trusted_runtime.py`, `static_runtime.py`, `static-client/`; never enable weakened modes in ordinary deployment |
| E1 admission | `admission_trial.py` | Production verify and target binding; [fixed recipes](E1-REAL-RUNS.md) |
| E1 offline history diagnosis | `admission_cases.py`, `history_diagnostics.py` | Signed fixtures and production history verifier; not full fresh Quote appraisal |
| E2 fault and receive timeline | `fault_trial.py`, `timeline.py`, `fault_fixture.py` | Production `remote_acceptance.py`, receiver collector, `milestone.py`; [fault recipes](FAULT-FIXTURES.md) |
| E3 renewal and recovery | `lifecycle_trial.py`, `lifecycle_evidence.py` | Existing Node snapshots and create-event stream; [recipe](examples/E3-LIFECYCLE-RECIPE.md) |
| E4 private memory and local/shared faults | Adapter `fleet_business.py`, `fleet_fault.py` | [fleet fault recipe](examples/FLEET-FAULT.md); recovery stays explicit |
| E4 LoCoMo-derived workload | `locomo.py`, `locomo_run.py`, `locomo_gateway.mjs` | [protocol](LOCOMO.md); fixture answers remain in the local grader |
| E5 HTTP/TLS load and process sampling | `load.py`, `load_fleet.py`, `resources.py` | `client_material.py`; [nonempty memory load](E5-MEMORY-LOAD.md) |
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
- Run the owning tests after code changes and the relevant cross-component regressions after integration. Rebuild the source manifest last; the remote hosts must build and verify their actual artifacts separately.

The examples previously at the experiment root (`suite*.example.json`, `fault-trial.example.json`) and `config/locomo.example.json` now live under `examples/`. Only example file locations changed; CLI flags, supplied configuration semantics and installed runtime paths are unchanged.
