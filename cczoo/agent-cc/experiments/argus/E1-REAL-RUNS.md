# E1 real admission recipes and evidence boundaries

Run these on the isolated **service Guest** after the full/native experiment
packages have been built, installed, and inspected with the existing variant
workflow. Run the same recipe separately for `full_argus` and
`native_spire_guarded`. Keep their generated identities, entries, directories and
units isolated. A native success is a valid observation, not a failed experiment.

The collector below calls the installed production `workload.py` verification
and the installed `argus-workload check`. It records current target, readiness,
verified mTLS peer/serial and correlated Workload EAR acceptance where the arm
has that mechanism. It **does not** create a `PASS` from a declared expected
decision, initiate attestation, rewrite identities, or bypass production checks.
The existing deployment commands below initiate the actual lifecycle operations.

## Required configuration

Create a protected operator configuration (example path
`/etc/argus-experiments/e1.json`):

```json
{
  "schema": "argus.e1-trial.v1",
  "run_id": "e1-full-seed-01",
  "case": "same_image_new_instance",
  "group": "full_argus",
  "deployment": "/etc/argus-exp-full/environment.json"
}
```

Supported cases are `legal`, `same_image_new_instance`, `unrelated_activity` and
`config_mismatch`. The unrelated case additionally requires the absolute
`unrelated_deployment` path. That file must describe a different workload with
separate run/record directories, use the same TC API/measured control path, and
have no conflicting published port. It is used for **launch only**; do not start
an additional SPIRE Agent or Helper for it.

All paths are actual operator references; replace the illustrative paths once
with the generated deployment paths. The configuration has no arbitrary command,
shell, claimed verdict or secret field. Preflight requires existing files,
the matching installed experiment arm and a valid source/binary manifest.
Use one immutable E1 configuration and a fresh output directory per trial.

```sh
umask 077
E1_CONFIG=/etc/argus-experiments/e1.json
E1_DIR=/var/lib/argus-evidence/e1-full-seed-01
mkdir -p "$E1_DIR"
DEPLOYMENT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["deployment"])' "$E1_CONFIG")
WORKLOAD_SCRIPT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["paths"]["install_dir"] + "/scripts/workload.py")' "$DEPLOYMENT")
python3 admission_trial.py preflight --config "$E1_CONFIG" --output "$E1_DIR/preflight.json"
```

The following commands are deliberately explicit. A failed `launch` with a known
operation ID uses `resume-launch --config ... [--launch-id ...]`; never repeat an
unknown creation. Provide the existing protected `TC_API_IDENTITY_TOKEN` through
the established shell/session environment, never in the E1 JSON or command argv.

## 1. Legal admission

With a clean trial deployment and an approved image/configuration:

```sh
python3 admission_trial.py observe --config "$E1_CONFIG" --output "$E1_DIR/before"
python3 "$WORKLOAD_SCRIPT" launch --config "$DEPLOYMENT"
python3 "$WORKLOAD_SCRIPT" register --config "$DEPLOYMENT"
python3 "$WORKLOAD_SCRIPT" start --config "$DEPLOYMENT"
python3 admission_trial.py observe --config "$E1_CONFIG" --output "$E1_DIR/after"
python3 admission_trial.py compare --before "$E1_DIR/before/observation.json" \
  --after "$E1_DIR/after/observation.json" --output "$E1_DIR/comparison.json"
```

`ADMITTED` requires stable current readiness, a successful current-target check,
production verification and the expected current SVID/peer. A nonzero subprocess
exit, missing socket or unavailable server is `UNKNOWN`, not a policy denial.
The tool's hardware provenance remains explicitly unestablished: retain the
normal remote acceptance report and build/host manifest separately.

## 2. Same image: old binding rejected, new instance independently admitted

Start with an admitted instance using the same production sequence above, then
record `before`. Do not reuse a preceding trial's before directory.

```sh
python3 admission_trial.py observe --config "$E1_CONFIG" --output "$E1_DIR/before"
OLD_CONTAINER=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["container_id"])' "$E1_DIR/before/target.json")
python3 "$WORKLOAD_SCRIPT" stop --config "$DEPLOYMENT"
docker stop --time 2 "$OLD_CONTAINER"
python3 "$WORKLOAD_SCRIPT" launch --config "$DEPLOYMENT"
python3 admission_trial.py observe --config "$E1_CONFIG" --reference "$E1_DIR/before" --output "$E1_DIR/unregistered-new"
python3 "$WORKLOAD_SCRIPT" register --config "$DEPLOYMENT"
python3 "$WORKLOAD_SCRIPT" start --config "$DEPLOYMENT"
python3 admission_trial.py observe --config "$E1_CONFIG" --reference "$E1_DIR/before" --output "$E1_DIR/after"
python3 admission_trial.py compare --before "$E1_DIR/before/observation.json" \
  --after "$E1_DIR/after/observation.json" --output "$E1_DIR/comparison.json"
```

Use the deployment's existing Docker/Docktap environment so controlled stop
events follow its measured path; do not redirect the command to an uninstrumented
daemon. `stop` stops the identity/ingress units and removes their registration; it
does not delete Agent data. The saved target artifact is only checked read-only,
never installed as a new registration. The comparison requires unchanged image
digest, changed launch/container IDs, independent successful new admission and
rejection of the saved old process binding.

This experiment establishes local instance binding and independent readmission.
It does not by itself demonstrate submission of a forged old Quote to Trustee,
nor is service shutdown timing an E1 result (use E2 for that). Keep the
`unregistered-new` observation so the intermediate unavailable state is visible.

## 3. Unrelated measured activity

Record the admitted target, create a different workload through the same TC API,
then re-admit **the unchanged target**. Merely sending another normal request
would reuse its previous appraisal and would not test history tolerance.

```sh
python3 admission_trial.py observe --config "$E1_CONFIG" --output "$E1_DIR/before"
OTHER_DEPLOYMENT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["unrelated_deployment"])' "$E1_CONFIG")
OTHER_SCRIPT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["paths"]["install_dir"] + "/scripts/workload.py")' "$OTHER_DEPLOYMENT")
python3 "$OTHER_SCRIPT" launch --config "$OTHER_DEPLOYMENT"
python3 "$WORKLOAD_SCRIPT" stop --config "$DEPLOYMENT"
python3 "$WORKLOAD_SCRIPT" register --config "$DEPLOYMENT"
python3 "$WORKLOAD_SCRIPT" start --config "$DEPLOYMENT"
python3 admission_trial.py observe --config "$E1_CONFIG" --reference "$E1_DIR/before" --output "$E1_DIR/after"
python3 admission_trial.py compare --before "$E1_DIR/before/observation.json" \
  --after "$E1_DIR/after/observation.json" --output "$E1_DIR/comparison.json"
```

The comparison requires the exact same target tuple, a newly completed unrelated
launch record and successful admission on both sides. Full Argus additionally
requires a different accepted Workload challenge nonce. Native intentionally has
no Workload EAR; it is not assigned a failure for lacking that mechanism.
Capture/replay of the changed cumulative RTMR requires the context artifacts
described below; the lifecycle observation alone does not reconstruct it.

## 4. Approved configuration mismatch

Use the existing `fault_fixture.py inject --event config-change` recipe in
`FAULT-FIXTURES.md`, with protected original/replacement configuration copies and
their hashes. Record `before` while admitted and `after` while the replacement
bytes are present. This mutation must update the bound inode, not atomically
replace the host path while leaving the container's old bind-mounted inode.

```sh
python3 admission_trial.py observe --config "$E1_CONFIG" --output "$E1_DIR/before"
# Execute the explicit, configured config-change injection in FAULT-FIXTURES.md.
python3 admission_trial.py observe --config "$E1_CONFIG" --reference "$E1_DIR/before" --output "$E1_DIR/after"
python3 admission_trial.py compare --before "$E1_DIR/before/observation.json" \
  --after "$E1_DIR/after/observation.json" --output "$E1_DIR/comparison.json"
# Then restore the bound file and explicitly release the recovery hold as documented.
```

The tool records actual configuration digests and the production local checker
result. Ordinary full/native preflight or the common Helper may reject this
configuration before any remote appraisal. Report that actual stage; do not
attribute a common local rejection exclusively to Argus's remote verifier.
Do not update approved policy to match the bad configuration during this case.

## 5. Evidence mismatch, hidden stop and offline rules

The current production CLI does **not** export all raw Quote, structured Trustee
request, signed EAR and appraisal-policy bytes for each acceptance/denial. Its
correlated EAR hash/log is useful operational evidence but not an independently
re-verifiable archive. Therefore a complete live wrong-nonce/REPORTDATA replay
driver is not claimed to exist here; these outer negative cases remain `NOT_RUN`.

Existing local signed fixtures are executable:

```sh
python3 admission_cases.py --output /var/lib/argus-evidence/e1-signed-fixtures
```

They use actual production log-verifier cryptography with ephemeral test keys.
Their `fixture_result` must stay separate from live admission. The fixed RTMR,
target-launch-only and full-history rules remain offline diagnostics.

When an operator already has a genuine captured verifier request and its original
verification context with hashed Quote/REPORTDATA/policy-result artifacts, use:

```sh
python3 history_diagnostics.py capture --config /protected/history-trust.json \
  --request /protected/original-verifier-request.json \
  --context /protected/original-verification-context.json \
  --output /var/lib/argus-evidence/e1-history-archive.json
python3 history_diagnostics.py replay --config /protected/history-trust.json \
  --archive /var/lib/argus-evidence/e1-history-archive.json \
  --fixed-rtmr "$CAPTURED_BASELINE_RTMR2" \
  --output /var/lib/argus-evidence/e1-history-replay.json
```

The input artifacts must exist; do not generate a context containing a manually
asserted `PASS`. Archive validation binds the original nonce, request and artifact
hashes. Never disable signatures or freshness checks to call an offline replay a
live admission. The remaining minimal outer-recording gap is a protected exporter
at the existing provider/appraisal boundaries, followed by a driver invoking the
same production verification path with those captured bytes. No new protocol,
new policy or generic experimental runner is needed to describe this gap.

## Result interpretation

`comparison.json` reports `case_setup_observed`, `declared_property_observed`,
actual admission on each side and the exact evidence scope. `OBSERVED` means the
specified property was observed with matching setup; otherwise it is `UNKNOWN`.
It is not a universal security `PASS`. Keep raw phase observations, intermediate
failures, SHA256 associations and incomplete runs in the paper's denominators.

Present local signed fixtures, real installed-runtime observations and complete
remote/hardware acceptance as separate evidence classes. Before remote execution
all four recipes, actual policy denials and hardware results remain `NOT_RUN`.
