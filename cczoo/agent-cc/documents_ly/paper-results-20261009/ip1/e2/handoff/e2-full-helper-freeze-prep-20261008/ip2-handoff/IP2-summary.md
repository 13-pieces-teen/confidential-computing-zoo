# Single-run record

- Experiment/scenario/group/repeat/run_id: E2 / helper-freeze / full_argus / r1 / `paper-20261004t145835z`
- Local record: IP2
- Delivery revision: `12b365ed8d3c73965e69c8fbe3fcb46075fefa6c`
- Instance: `launch-8708838`, container `bf089150...`, target paper02/full
- Clock uncertainty: 20 ms
- Commands/configuration: see `E2-SERVER-FIELDS.json`; no secrets included

| Step | Local/remote handoff | Raw result | Evidence |
|---|---|---|---|
| Preparation | IP2 | PASS | preflight archive and server fields |
| Observation and fault | IP1 next | NOT_RUN | fault/lifecycle files do not yet exist |
| Collection and analysis | IP1 next | NOT_RUN | waiting for coordinator |
| Recovery/end state | IP2 after explicit collection-complete notice | NOT_RUN | no hold or fault exists yet |

## Result

- Tool result: preparation only; E2 scientific result is `NOT_RUN`.
- Failures/UNKNOWN: none at preparation; SSH alias must be selected from IP1's existing strict host configuration.
- Current service: healthy and available.
- Next action: IP1 runs exactly one `fault_trial.py run` using the delivered server fields.
