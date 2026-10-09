# E1 results and coverage

## Real online admission reused

The completed Full A3 archive is reused without redeployment or rerun.

- environment: original IP2 TDX guest, same boot
  `c379b316-287e-4c6b-821b-63ae5a51c45e`
- installed source revision: `5a8e39eadcb5617dc50638242323aa6ed41f4ae4`
- SPIRE: 1.15.3
- target: `launch-9295b78`, container `6fe3c315...d2142ab`,
  PID `1716593`
- new admission: `ADMITTED`
- five stages: current target, Provider evidence, evidence binding, remote
  appraisal and final target all `ALLOW`
- accepted nonce:
  `VAlcCOENN1fWQTUfqoFt-iR8fdUNt4sJjNDJAvm2YzE`
- identity delivery and ingress readiness: `ALLOW`
- legal business access: 18 rounds / 54 legs, all HTTP 200
- IP1/IP2 leg correlation: 54/54, maximum corrected time delta 610 ms

This is a real online legal-admission result. It is not a controlled-v2
software-backend result and does not use commit `8cdc1900` as deployment proof.

## Offline historical-rule cases completed

`admission_cases.py` was executed on 2026-10-09 using the production
`LogVerifier` cryptographic path, archived Rekor transport, and fresh ephemeral
test keys. All fixture expectations passed:

| Case | Production log-rule decision | Fixture result | Live admission |
|---|---|---|---|
| legal | ALLOW | PASS | NOT_RUN |
| unrelated activity | ALLOW | PASS | NOT_RUN |
| same image / new unlogged instance | DENY | PASS | NOT_RUN |
| hidden stop | DENY | PASS | NOT_RUN |
| configuration mismatch in log-only fixture | ALLOW | PASS | NOT_RUN |
| old evidence in log-only fixture | ALLOW | PASS | NOT_RUN |
| instance mismatch | DENY | PASS | NOT_RUN |

The ALLOW results for configuration mismatch and old evidence are expected at
the log sub-verifier boundary: fresh Quote/REPORTDATA, challenge freshness and
approved configuration policy are explicitly outside this offline tool.

## Online abnormal coverage still missing

No online negative result is claimed for:

- wrong nonce or stale Quote/REPORTDATA replay
- old evidence against a fresh challenge
- hidden stop presented through a production submission interface
- mismatched captured Quote/EAR
- current-facts-only controlled verifier comparison

The production tree has no bounded online driver for those interfaces.
`fault_fixture.py` supports an isolated `/srv/argus-experiments` deployment,
not the current paper02 `/srv/openviking/...` production path. Extending it
would be new backend/environment work and was not done. The former Docker
stop/replay route is also excluded because it produced an unknown INFLIGHT
record. These cases remain `NOT_RUN`, not zero and not policy denial.

## E1 conclusion

E1 is **partially complete**:

- legal online admission and legal business continuation: complete with originals
- production log-rule offline coverage: complete with originals
- online abnormal controlled comparison: NOT_RUN because the required safe
  production injection interfaces do not exist

