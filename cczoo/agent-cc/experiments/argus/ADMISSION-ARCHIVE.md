# Admission originals and history diagnostics

This is optional experiment evidence, disabled in normal deployment. It changes
neither the approved policy nor `CanReattest=false`. `TCB UpToDate` is not added.
The archive does not replace remote TDX verification or issue a new identity.

## Build and enable capture

The normal Linux `core/spire/workload/scripts/build.sh` now builds and installs
`argus-verify-admission` and `argus-replay-policy`. The former calls the existing
production Go EAR verifier; the latter uses Trustee v0.21's pinned `regorus`
engine. To compile only these tools (without running tests):

```bash
cd cczoo/agent-cc
OUT=/absolute/path/to/experiment-tools
mkdir -p "$OUT"
(cd core/spire/plugins/argus-tdx-workloadattestor && \
  go build -trimpath -o "$OUT/argus-verify-admission" ./cmd/argus-verify-admission)
(cd core/spire/workload/trustee-contract && \
  cargo build --locked --release --bin argus-replay-policy)
install -m 0755 core/spire/workload/trustee-contract/target/release/argus-replay-policy "$OUT/"
```

Rebuild the WorkloadAttestor and patched Trustee AS from this version, and install
the updated `verify_trucon.py` using the existing Trustee installation procedure.
An old binary with only these environment variables cannot produce the archive.

On IP2, create a private local directory and add the following environment to the
**actual SPIRE Agent unit which runs the WorkloadAttestor plugin**:

```ini
[Service]
Environment=ARGUS_ADMISSION_EVIDENCE_DIR=/var/log/argus-experiment/admission-plugin
```

On IP1 (the machine running the patched Trustee AS), create another private local
directory and add to its existing service/container environment:

```text
ARGUS_TRUCON_EVIDENCE_DIR=/var/log/argus-experiment/admission-trustee
```

Use `install -d -m 0700` for both directories, owned by their service account.
Mount the Trustee capture directory when AS runs in a container. Restart during
experiment setup and obtain a new Workload subscription/appraisal. Normal SVID
renewal within an existing subscription does not create another appraisal archive.
Do not clear SPIRE Agent identity data. Remove these environment overrides after
the experiment. Use a local filesystem and record capture mode in both arms.

The plugin queues best-effort writes without waiting for the recorder. The
short-lived Trustee history verifier forks an optional Linux writer after
computing its verdict: the parent never waits, the child closes inherited pipes
and descriptors, uses one nonblocking writer slot per directory, a five-second
budget and a 64 MiB archive limit. The AS exports policy input asynchronously.
The verifier logs the scheduled nonce; missing output remains visible as missing
evidence. Other platforms do not enable the optional Python writer. Capture can
add measurement overhead, so compare arms with the same capture setting. A crash,
queue overflow, filesystem failure or absent companion file leaves incomplete
evidence; the collector never interprets this as an admission denial or success.

## Collect one nonce

Each side creates `<directory>/<workload-nonce>/`. Copy the Trustee nonce directory
to the collection host through the existing protected transfer path. It must be
the nonce from the current plugin appraisal, not simply the most recent directory.

The plugin records `evidence.json`, exact `request.json`, `quote.bin`, `ear.jwt`
when received, `capture.json` and the existing post-appraisal `local-check.json`.
The Trustee records its authenticated `history-request.json`, actually fetched
`rekor.json`, original trust config and public trust material, `history-result.json`,
`capture.json` and authenticated `policy-input.json` from the AS hook.
The exporter includes no HTTP authorization header, API key or business payload.
These files still describe the deployment and stay in the private evidence area.

Prepare `verification.json` from the **actual approved** IP1 policy and trust
configuration. Hash their exact bytes; do not substitute the repository's default
policy template or inferred trust-vector expectations:

```json
{
  "ear_public_key_path": "/absolute/approved/ear-public-key.pem",
  "ear_expected_issuer": "<existing approved issuer>",
  "ear_expected_profile": "<existing approved EAR profile>",
  "policy_id": "<existing approved workload policy ID>",
  "approved_policy_artifact": {
    "path": "/absolute/approved/workload.rego",
    "sha256": "<sha256 of actual policy bytes>"
  },
  "history_config_sha256": "<sha256 of actual ARGUS_TRUCON_CONFIG file>"
}
```

Collect a current `admission_trial.py observe` directory when an observed SVID and
business admission is required. EAR acceptance alone is insufficient for that
claim. Then, from `cczoo/agent-cc`:

```bash
python3 experiments/argus/admission_evidence.py collect \
  --plugin-capture "$PLUGIN_CAPTURE/$NONCE" \
  --trustee-capture "$TRANSFERRED_TRUSTEE_CAPTURE/$NONCE" \
  --config "$PRIVATE/verification.json" \
  --observation "$E1_OBSERVATION_DIR" \
  --output "$EVIDENCE/admission-$NONCE"

python3 experiments/argus/admission_evidence.py verify \
  --bundle "$EVIDENCE/admission-$NONCE" \
  --verifier-bin "$OUT/argus-verify-admission" \
  --policy-bin "$OUT/argus-replay-policy" \
  --output "$EVIDENCE/admission-$NONCE-verification.json"
```

`--observation` is optional for log/policy diagnosis. Capture without all original
files is retained but verification is `UNKNOWN`. Failed cryptographic checks or
changed originals are `FAIL`. Missing tools are `UNKNOWN`, not a policy rejection.
The Python environment needs the same pinned requirements as `history_diagnostics.py`.

## Meaning of verification

`argus.admission-verification.v1` includes `bundle_manifest_sha256`, `nonce`,
`captured_at_ms`, full target fields and separate `ear/history/policy/local_target/
observation` checks. It reuses:

- production EAR signature, issuer, profile, policy, REPORTDATA and validity checks
  **at the recorded capture time**;
- production `LogVerifier` with archived Rekor transport, retained DSSE/Rekor
  signatures, owner/predecessor/RTMR/target checks and original public trust bytes;
- the actual approved Rego policy and captured AS input, comparing its claims to
  the signed EAR trustworthiness vector.

This is a recheck of a captured verification context with trusted exporter
provenance. Raw Quote equality is checked across the original request/export;
the tool does **not** independently rerun DCAP, authenticate a new challenge,
prove the current process is alive, or turn a historical acceptance into a live
admission. Outputs keep `offline_dcap=NOT_RUN` and `fresh_admission=NOT_RUN`.
An unavailable signed trust vector leaves policy comparison `UNKNOWN` rather than
inventing expected hardware/configuration values.

With a matching original E1 observation, `admission.status=OBSERVED_ADMITTED`
also identifies the SVID serial and Helper invocation. `observed_at_ms` is the
observation completion time: it conservatively establishes admission by that time,
not the earlier EAR issue time. Without this association status remains
`NOT_ESTABLISHED`, even if the archive recheck passes. Cross-host timestamp
comparisons still need the experiment's declared clock uncertainty.
The admission object carries the observation's `run_id` and `target_id`; receipt
analysis must require the current run and exact process instance to match. Pin the
bundle's `manifest.json` SHA-256 in the experiment context just like other evidence
references. Public verification keys and policy bytes come from the approved,
protected capture; a freely replaceable bundle is not its own external trust root.

## Fixed approved measurement

Freeze an independent legal reference **before** the unrelated-activity case:
Run this freeze command on the plugin host (IP2), so its timestamp and the later
plugin capture share the same host clock. Do not use a cross-host clock comparison
to establish this ordering.

```bash
python3 experiments/argus/history_diagnostics.py approve-reference \
  --bundle "$EVIDENCE/reference-admission" \
  --verifier-bin "$OUT/argus-verify-admission" \
  --policy-bin "$OUT/argus-replay-policy" \
  --output "$EVIDENCE/fixed-approved-reference.json"
```

It requires reverified originals plus observed admission and records target,
reference nonce, Quote/policy/manifest hashes, RTMR2 and the freeze time. Collect
the later admission bundle using the same commands above, then:

```bash
python3 experiments/argus/history_diagnostics.py replay-bundle \
  --bundle "$EVIDENCE/later-admission" \
  --approved-reference "$EVIDENCE/fixed-approved-reference.json" \
  --verifier-bin "$OUT/argus-verify-admission" \
  --policy-bin "$OUT/argus-replay-policy" \
  --output "$EVIDENCE/fixed-approved-diagnostic.json"
```

Compare the same target before/after real allowed unrelated measured activity.
`replay-bundle` also requires identical approved policy bytes; retaining only the
same policy ID is insufficient for this comparison.
Expected diagnostic: fixed-reference equality changes ALLOW→DENY, while complete
history replay remains ALLOW. This is an offline policy diagnostic. Legacy
`--fixed-rtmr` remains for existing callers but is explicitly
`UNAPPROVED_EXPLORATORY`; an arbitrary current RTMR is not a paper baseline.

## Current-facts-only reachability milestone

No current-facts-only runtime group or production history bypass is added.
First complete [E1-REACHABILITY.md](E1-REACHABILITY.md) on the existing Full
deployment, recording allowed interfaces, actor authority, common checks, the
actual namespace/route and the receiving process. Investigate these exact cases:

| Case | Common controls / expected observation |
|---|---|
| Normal approved launch | Common current facts and Full history allow. |
| Allowed unrelated measured activity | Current target unchanged; fresh Full history can still allow. |
| Replacement with saved old target | Common local checks reject; no independent historical benefit claimed. |
| Approved new launch and fresh registration | Legitimate new instance can independently qualify. |
| Deleted log references / invented stop event | If the common collector cannot produce it, label verifier-input diagnosis only. |
| Recorded stop→start of the same container, followed by fresh local registration | Candidate for a real policy distinction, pending remote reachability evidence. |

For the last candidate: retain the approved image/configuration/data, stop the old
Helper/entry, perform the real stop and start through the measured Docktap path,
wait for complete upload, and use the normal registration operation to observe
the new PID/starttime. The container retains its original launch label. Collect
the complete Provider snapshot and new remote appraisal. Do not fabricate target
files, suppress the stop event or change the common collector. Full's current
policy rejects a successful stop after that launch; current-only interpretation
would not impose that historical condition. This is an authorized lifecycle
experiment, not proof that an attacker can invoke those operations.

Proceed to an isolated current-facts-only build only if the same current target,
registration and collection path actually reach the remote decision point, with
all differing historical predicates documented. If shared local checks already
reject, stop at the diagnostic and report common protection. No E4 extension or
online-vulnerability claim follows from synthetic history mutations. Do not
weaken Full to manufacture a difference.

Connection-time appraisal and invocation-lease mechanisms likewise remain
`proposed_not_run`. Existing E2 new/reused/in-flight connection lanes measure
transport behavior; they are not those alternative appraisal mechanisms or
reproductions of aDNS/ACLE-MCP.

## Remote tests (not executed by the code-delivery task)

Run production Go tests, Trustee contract/capture tests and
`tests/test_admission_evidence.py` / `tests/test_history_diagnostics.py` on the
remote Linux environment. Include missing/truncated exports, corrupted EAR and
history, nonce/target mismatch, capture write failure, fixed-reference mismatch,
and successful same-context replay. Record test results separately from real TDX
admission and the reachability milestone above.
