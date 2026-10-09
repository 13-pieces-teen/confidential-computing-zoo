# IP2 S0 preflight — argus-paper-minimal-20261009t0718z-01

Read-only, 2026-10-09 ~07:16–07:20Z. Nothing was pulled, checked out, merged,
restarted, admitted, or faulted. No production component was modified.

## Materials

- Pinned commit `b471a34b73e4f8490fa633b7ea3bcfb510756d39` fetched shallow into
  `/tmp/argus-paper-minimal` (blobless, sparse) over the machine's normal SSH
  credential; HTTPS to github.com was down at fetch time, SSH worked.
- The seven listed files exported to the batch dir `materials/` and all pass
  `SHA256SUMS` verification. `MATERIAL-COMMIT.txt` records the pinned SHA.

## Current Full service — read-only status

- Active units: `ax-paper02-full-agent`, `ax-paper02-full-authz`,
  `ax-paper02-full-helper`, `ax-paper02-full-nginx`,
  `argus-paper-20261004t145835z-docktap|tc-api|trucon`.
- Old paper01/argus units are inactive or failed and were not touched.
- Container `fb2a2294a5f088d905b70c9d3d24f41ad2ceaceeda00151d49a68db5bc1686f4`
  (`openviking-cmem-audit:latest`, started 2026-10-09T05:43:16Z, healthy).
- launch-state.json: run_id `e8ca6f0e-f1e2-40d2-9797-d6e7ceb7447e`, launch
  `launch-a4ba2ba`, stage complete, config_sha256
  `43b6c0e96821640ae0fbee65d5bad24789cf852647f8af608e56d42776120b02`.
- Processes (started 13:43–13:45 +08:00): spire-agent 1280689, evidence
  provider 1280687 (socket `/run/ax-paper02-full/evidence-provider.sock`),
  spiffe-helper 1281816 (invocation `76bc43416ab64049871e818f3bcd15d3`),
  nginx 1282598, authz 1280686.
- Provider quote-counter interface: AVAILABLE; canonical probe via
  `lifecycle_evidence.py quote-snapshot` saved as
  `s0/provider-quote-probe.json` (node 0/0/0, workload 2/2/0 at probe time).
- Helper journal shows target SVID publications every ~141 s; expected
  several rotations inside the 600 s window; no TTL/refresh changes made.
- No stale sampler/observer/collector processes found (checked lifecycle_*,
  load_fleet, node_attestation_observe, collector, fault_trial, continuous).

## Clock

- 3 samples via trustee TLS forward Date header: mean IP1−IP2 = −497.7 ms,
  RTT ~130 ms; conservative IP2↔IP1-control bound 1200 ms.
- Client Guest↔IP2 measurement requested from IP1; combined bound for
  collect = |guest↔IP1| + error + 1200 ms. Old 2026-10-08 skew not reused.

## Existing originals found (see existing-evidence-index.json)

- E2 r4: local receiver originals under
  `/var/lib/argus-receiver/paper-20261004t145835z/e2-full-helper-freeze-20261008-r4/`
  (26 files hashed in `e2-r4-local-sha256.txt`, incl. fault.jsonl,
  lifecycle.jsonl, publish-hold records, recovery dir) plus epoch
  `binding.json` and 122 MB `receiver.jsonl` (hashed, not copied). Prior
  export already on results branch `codex/argus-results-ip2-20261009`
  (`.../ip2/e2/`, commit 5780d828).
- Seven historical rule construct cases: all 7 already exported on the
  results branch at
  `.../ip2/batches/20261009-repair-and-rerun/preexisting/e1-offline-history-fixtures/cases/`
  (legal, unrelated_activity, same_image_new_instance, hidden_stop,
  config_mismatch, old_evidence, instance_mismatch) with VERSIONS.json
  (analyzer source hashes, git 3cf25a87, private_keys_exported=false).
  Local construct tool: `experiments/argus/admission_cases.py` (CASES tuple =
  the same seven), production LogVerifier history sub-verifier; no re-run
  performed.
- P0 originals: channel directory
  `/root/argus-ip1-handoff/p0-20261002t1521z/` (P0_FINAL_STATUS.txt,
  p0d-siliconflow-step5-6 recall material, business keys, stage receipts).
  IP1 owns the P0 authorization/Agent-recall finding; IP2 supplements the
  server-side associations from this channel dir and the results-branch
  e1/online exports.

## Gates

- S2 authorization matrix: pending IP1's P0-original verdict.
- S3 single Agent recall: pending IP1's fact/marker freeze.
- S1 requires IP1's 3-probe non-empty confirmation before CLIENT_ACTIVE.
