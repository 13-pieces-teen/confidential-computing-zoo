# E4 frozen formal results by evidence boundary

Environment: original IP2 TDX guest, boot
`c379b316-287e-4c6b-821b-63ae5a51c45e`, paper02 Full Argus,
SPIRE 1.15.3, OpenViking image config
`sha256:321458f051d5048dfbaa8f4a54d8d10d8b3cf6592087571d38ec4fb071c403e3`,
model `siliconflow/deepseek-ai/DeepSeek-V3.2`.

The six runs are three paired seeds, not six independent samples. Request
counts and receiver intervals are coverage records, not sample counts.

| Seed | Condition | Actual injection | Ingress/recovery observation | Admission/readiness | Application-read evidence | Task output |
|---|---|---|---|---|---|---|
| 101 | fault | owner-verified `helper-freeze` at +180 s; recovery at +360 s | recovery owner restored Helper, NGINX and 1943 | exact-run normal launch/register/start was READY before window | 34/34 request enter/end; 2507 COMPLETE intervals | native PASS; actual FAIL5/UNKNOWN1 |
| 101 | healthy | no command; both argv arrays empty | no closure/recovery action | exact-run READY | 38/38; 2507 COMPLETE | native PASS; actual FAIL5/UNKNOWN1 |
| 102 | healthy | no command; both argv arrays empty | no closure/recovery action | exact-run READY | 39/39; 2507 COMPLETE | native PASS; actual FAIL5/UNKNOWN1 |
| 102 | fault | owner-verified `helper-freeze` at +180 s; recovery at +360 s | matching owner released hold and restored Helper, NGINX and 1943 | exact-run READY | 34/34; 2507 COMPLETE | native PASS; actual FAIL5/UNKNOWN1 |
| 103 | fault | owner-verified `helper-freeze` at +180 s; recovery at +360 s | matching owner released hold and restored Helper, NGINX and 1943 | exact-run READY | 34/34; 2507 COMPLETE | native PASS; actual FAIL5/UNKNOWN1 |
| 103 | healthy | no command; both argv arrays empty | no closure/recovery action | exact-run READY | 37/37; 2507 COMPLETE | native PASS; actual FAIL5/UNKNOWN1 |

## Required separate verdicts

| Question | Result | Scope |
|---|---|---|
| Evidence closed | **YES, 6/6 runs** | result-package checksums, exact-run receiver binding, matched requests, complete window intervals, finalized collector |
| Complete scheduled window | **YES, 6/6 runs** | all six scheduled steps reached terminal records; this is execution completeness |
| Complete correct task | **NO, 0/6 runs** | s00 was `ANSWER_INCORRECT`; s01-s05 were `CHECKPOINT_UNCONFIRMED` |
| Correct continuation after first answer | **NO, 0/6 runs** | no run established the five required continuation checkpoints |
| Legal recovery | **YES, 3/3 fault runs** | frozen owner-verified recovery command, matching hold owner, restored Helper/NGINX/1943 and post-recovery receiver traffic |
| Healthy control integrity | **YES, 3/3 healthy runs** | both control argv arrays were empty; no fault/recovery command was dispatched |

`native PASS` means the runner completed and measurement closed. It is not
reported as task success. The paired experimental sample size is three seeds.

