# E1: fresh admission, first rejection and lifecycle barriers

These additions prepare local/remote experiments; they are not completed TDX
results. Use the delivered build, matching policy and isolated experiment
deployment in both arms. The existing `observe` command checks current readiness
and a business probe; it does **not** request a new admission.

## Fresh attempt and stage receipts

Run on the service guest with the normal trusted operator privileges:

```sh
python3 admission_trial.py attempt --config /srv/argus-experiments/e1/trial.json \
  --output /srv/argus-experiments/e1/attempt-1 --new-subscription --timeout 180
python3 admission_trial.py observe --config /srv/argus-experiments/e1/trial.json \
  --output /srv/argus-experiments/e1/observation-1 \
  --attempt /srv/argus-experiments/e1/attempt-1/attempt.json
```

`attempt` saves its intent before issuing the fixed `systemctl restart <this
deployment's Helper unit>` command exactly once. It never calls Docker. A lost
command reply is retained; do not rerun the same output directory. The tool
requires a changed Helper invocation and saves a bounded, guest-boot-specific
journal even if no Quote, EAR or ready file was produced. The default 180-second
timeout is configurable and is part of the receipt. It is a budget, not a bound
on admission or recovery latency. Isolated unit names come from the installed
deployment, never the production default names.

`attempt.json` uses `argus.e1-attempt.v1` and contains run/group/config/deployment
hashes, target, wall-clock window, prior/new Helper invocation, `result`
(`ADMITTED`, `DENIED`, `UNKNOWN`), `accepted_nonce` (Full only), `ready_elapsed_ms`
(monotonic command-to-first-observed-ready, or null), and `complete`. Complete
means the journal was read, target remained stable and invocation changed; it
does not mean admission succeeded. The raw journal is
`admission-stage-journal.jsonl`, bound by `journal_sha256`.

Provider and plugin emit `argus admission stage {JSON}` into their normal
journals. The JSON schema is `argus.admission-stage.v1`: component, nonce and
nonce-based attempt ID, stage, status, safe reason code and target identifiers.
The parser retains each retry nonce. It reports `first_rejection_stage` only
when all preceding required stage receipts allow. An explicit later rejection
with a missing earlier stage is retained as `explicit_rejection_stage`; it does
not manufacture an earlier success. After an observed rejection, later missing
stages are `NOT_REACHED`. Missing or conflicting evidence remains `UNKNOWN`.

The stages are common current-target checks, Provider, evidence binding,
remote appraisal, final target recheck, identity delivery and ingress readiness.
Readiness/identity are observed from the new Helper publication and current
ready state. They are not a backend-read receipt. Provider pending rejection
before Quote creation does not need a nonexistent signed accepted EAR archive.

The Provider distinguishes `MUTATION_PENDING`, `HISTORY_NOT_CONFIRMED`,
`HISTORY_CAPACITY` and unavailable history using bounded reason headers from the
root-only TruCon UDS. The remote plugin labels an explicitly `contraindicated`
EAR as denial only after signature, issuer/profile, validity, policy and
REPORTDATA binding checks. Indeterminate status, HTTP errors, transport failures and invalid EARs stay UNKNOWN;
a Trustee HTTP 500 alone does not establish a historical-rule rejection. A
policy-specific claim additionally needs its existing captured policy/history
originals. Local journal association is trusted operational evidence, not a new
independent hardware-provenance proof.

Provider receipts include `timings.history_snapshot_ms`, `quote_ms`, `total_ms`,
`quote_attempts` and `history_entries`. Durations are monotonic and include the
relevant retries; total time also includes checks/waits. They do not enter
REPORTDATA, signed policy inputs or admission decisions. The raw receipts are
retained in `attempt.provider_timings` and `attempt.stages.stage_receipts` for E5.

## A real running-but-unconfirmed case

Use `case: "record_pending"` and add `barrier_receipt` to the normal E1 trial
JSON. It points to the actual reached lifecycle barrier receipt described
below. No expected verdict or manually supplied mutation status is accepted.

```sh
python3 admission_trial.py pending --config /srv/argus-experiments/e1/trial.json \
  --output /srv/argus-experiments/e1/pending-before.json
```

This command reads the real root-only TruCon UDS and saves
`argus.e1-pending.v1`: operation/mutation ID, actual outbox state, current target,
container match, target check, chain-state HTTP status/reason and configuration
references. It saves only operation identifiers/status and hashes, never raw
Docker requests, credentials or signed bundle contents. Missing/outdated
mutation evidence cannot establish the case.

Hold a supported **start** operation before confirmation, after its actual
Docker effect. Register the genuine running target through the normal workflow
and perform a fresh attempt. `record_pending` attempts capture the exact same
mutation before and after the attempt. `compare` requires that pending interval,
container association, matching current-target check, and explicit new
subscription. Full/native outcomes are both measured; an already functioning
ingress does not demonstrate a new successful admission. Restore confirmation
and use a new attempt directory to verify legitimate new admission. If the
normal deployment rejects earlier or cannot reach this point, preserve that
result; do not redirect ingress, change policy or bypass common checks.

For E5's shared-chain check, A's pending capture and B's fresh attempt have
different target/deployment references and the same chain. Measure B's existing
traffic separately. Pending does not synchronously close B's existing ingress.

## Default-off deterministic barriers

The production path does no barrier I/O unless `ARGUS_EXPERIMENT_BARRIER_DIR`
is set. Enabling requires `ARGUS_EXPERIMENT_MODE=1`, Linux root, a root-only
directory below `/srv/argus-experiments`, root-owned parents without other
writers, and no symlinks. Set these environment variables only for the isolated
Docktap and TruCon experiment processes, preserving their normal permission and
policy boundary. Use a fresh directory for each cut and archive the service
configuration. No HTTP arm, clear-fence or force-confirm API exists.

```sh
install -d -m 700 /srv/argus-experiments/e1/cut-start
python3 lifecycle_barrier.py arm --directory /srv/argus-experiments/e1/cut-start \
  --barrier-id start-confirm --point before_confirm --operation-type start \
  --timeout-seconds 300
# Issue the normal supported operation once, then inspect the reached receipt.
python3 lifecycle_barrier.py status --directory /srv/argus-experiments/e1/cut-start \
  --barrier-id start-confirm
python3 lifecycle_barrier.py release --directory /srv/argus-experiments/e1/cut-start \
  --barrier-id start-confirm
```

The supported points are after durable reserve, after Docker forwarding, after
result persistence, after signed submission persistence, before confirmation,
and after RTMR extension/before database insertion. Exact mutation filtering is
available with `--mutation-id`; if the ID is not yet known, the barrier captures
one first matching operation. Therefore isolate other lifecycle activity and
verify the captured operation/container before admitting the run. A barrier
never clears pending state. Its exact-ID release lets the original code path
continue; reaching it is not confirmation. Timeout raises an error rather than
silently allowing the held path to proceed. The retry worker honors result/sign
barriers, so it cannot race the held proxy thread and submit the same result.

For a crash experiment, after observing the reached receipt, stop the relevant
process through the trusted host controller and retain that action receipt.
Unknown `INFLIGHT` stays blocked; known results can resume record submission,
never Docker replay. Observe actual Docker forwarding/effect separately: the
`after_forward` receipt proves the request was sent, not that Docker completed.

The RTMR/database cut is explicitly classified `MEASUREMENT_COMMIT_UNKNOWN`.
It is an observation, **not** a reconciliation journal. Do not automatically
restart/retry a killed process at this cut or release a stale receipt to claim
recovery. A retry may extend again before discovering divergent history. Keep
that run failed/unresolved and rebuild a fresh guest/chain if necessary. The
existing claim remains same-guest-boot process recovery for covered known
states; there is no VM reboot persistence, antirollback or universal crash
recovery guarantee.
