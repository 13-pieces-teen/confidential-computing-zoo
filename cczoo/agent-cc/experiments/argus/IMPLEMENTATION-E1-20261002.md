# E1 implementation record — 2026-10-02

Implemented in the existing working tree; no commit, remote deployment or real
TDX experiment was made. Pre-existing outbox changes were retained.

- Added `admission_trial.py attempt --new-subscription` for an explicit fixed
  Helper restart and fresh invocation/journal receipt. Existing `observe`
  remains an observation of current readiness, not a new admission.
- Added nonce-scoped Provider/plugin stage receipts, canonical first-rejection
  classification and UNKNOWN/NOT_REACHED behavior. Journal parsing uses the
  installed isolated unit names, target, guest boot and bounded time window.
- Added safe TruCon readiness reason headers, preserving existing HTTP status
  and body behavior. Provider history/Quote/total timings are monotonic, include
  retries, and remain outside the signed evidence/policy contract.
- Added `record_pending` collection through the real root-only TruCon UDS,
  exact mutation/container association and before/after pending interval for a
  fresh attempt. Capture omits credentials, raw requests and signed bundle
  contents. Native outcomes are measured rather than assumed.
- Added default-off root-only lifecycle barriers at reserve/forward/result/
  signed submission/confirmation and RTMR-to-database boundary. Result/sign
  barriers also apply to the retry worker. Publication is atomic and
  create-only; timeout never silently continues. No arbitrary command,
  outcome-reset or force-confirm endpoint was added.
- Added local crash-cut tests preserving known/unknown states, no Docker
  replay, exact signed retry, and confirmation gating. The RTMR/database cut
  remains explicitly unresolved (`MEASUREMENT_COMMIT_UNKNOWN`); this change
  does not implement measurement reconciliation or broader crash recovery.

Usage and schemas: [E1-STAGE-RECEIPTS.md](E1-STAGE-RECEIPTS.md).

## Verification

The existing `tmp/argus-extensions-venv` with `PYTHONPATH=core/tlog` was used;
no dependencies were installed.

- Core lifecycle/TruCon client/proxy response/snapshot/barrier files: **133
  passed**, XML at `tmp/e1-core-20261002.xml`.
- E1 admission/stage/case/archive files: **35 passed**, XML at
  `tmp/e1-admission-20261002.xml`.
- Go WorkloadAttestor, Trustee and evidence-client packages: **passed**.
- Rust Provider changes were statically reviewed, including the existing
  `workload::Target = BTreeMap<String, String>` type and the bounded root-UDS
  decoder. **Not compiled/tested**: no Cargo/Rust compiler is installed on this
  host. Build and run its Rust tests on the Linux build host before deployment.
- Native Linux ownership/UDS/systemd, real Docker/TDX cuts, fresh admission,
  backend receipt, receiver timing and task/model results remain **NOT_RUN**.

The local fixtures fake Docker/RTMR/Rekor and journal/systemd inputs. They verify
the implementation's contracts and conservative classifications; they are not
paper performance or real-hardware security results.
