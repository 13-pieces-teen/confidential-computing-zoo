# Missing / held-back materials (not exported, not regenerated)

Nothing in this list was re-run or fabricated to fill a gap; items are either
held on the server by the export review (R2), live on IP2, or never existed.

## Held on server (export review decision; originals unchanged)

| item | server location | why held | impact on local recomputation |
|---|---|---|---|
| E4 `private-fixture.json` ×6 (task input ground truth: facts, expected decision JSONs, constraint values) | `/secure/e4-formal/runs/e4p1-*/private-fixture.json` | contains expected answers/business fixture | per-step CSV keeps `fact_id`, `fact_sha256`, `constraint_key`; scoring re-run would need the file via protected channel |
| E2 configs `e2-config.json`, `-r3`, `-r4` | `/root/argus-e2-ip1/` | probe cert/key/bundle paths + api-key env references (no values) | E2 setup reconstruction incomplete; handoff docs summarize parameters |
| IP2 server-field files `E2-*-FIELDS.json`, `E2-R4-RECOVERY-FIELDS.json` | `/root/argus-ip1-handoff/e2-*/` | IP2 server config records incl. ssh host public key | E2 correlation metadata only; md closures keep verdicts and hashes |
| E2 `receiver.jsonl` (receiver runtime record) | `/root/argus-e2-ip1/output-r4/collection-*/` | held by export review | E2 request-level receiver replay impossible locally; trace.jsonl (IP1 side) is exported |
| E2 `credentials/` (ready.json + generation dirs), `payloads/` | `/root/argus-e2-ip1/` | SPIRE credential cache / request payloads | E2 probe auth chain not reproducible from this export |
| IP2-delivered API-key files `paper02-business-api-key`, `paper02-user-api-key` | `/root/argus-ip1-handoff/probe-round4-401-20261008/ip2-handoff/`, `probe-round6-403-20261008/ip2-handoff/` | credential material | none: probe round reports keep statuses/rcs |
| OIDC tokens (consumed) and Sigstore mint machinery | `/root/.argus-paper-oidc/`, `/tmp/oidc-mint.sh`, per-run preflight packages | credential material | none for result analysis |
| E4 pilot run directories (only the pilot md docs are exported) | `/secure/e4-pilot/runs/` | pilot scope (docs-only export) | pilot formal pair is documented in `e4-pilot/docs/` |
| `evidence-at-ready.tar.gz` | `e1-20261004-full-a3-ready/ip1-handoff/` | archive superseded by its extracted members, which ARE exported (`e1/probes/a3-ready/*.jsonl`) | none |

## Lives on IP2 (needs protected-channel transfer if ever needed)

- IP2 receiver originals and per-run correlations for E1 (receiver-side
  admission records), E3/E5 (receiver closure already exported as md from
  IP2), and any IP2-side raw collector DBs. The exported IP2 closure md files
  state the correlation sha256s and counts.
- E1 native profile materials: the E1 preseed checklist recorded 5 missing
  items on the native side (per IP1 preseed readiness receipt); those items
  were never produced, so they cannot be exported by IP1.

## Never existed (by design)

- `joint-result` files: the runner does not emit a joint-result artifact; the
  E4 WORK-ITEM joint-analysis axes (`legal_recovery_result`,
  `receipt_result`) remain UNKNOWN in the frozen originals. Explanation:
  `e4-formal/analysis/IP2-E4-FORMAL-RESULTS-ANALYSIS-20261009.md` §2.3.
- Per-run model answer texts (s00 replies): the frozen originals record only
  `task_result/reason` and readback fields, not the reply body; the reply
  wording was a dispatch-period container observation (containers rotate per
  run by contract). See the analysis document §3 note.
- Any per-step/per-task CSV produced at run time — the CSVs in
  `e4-formal/csv/` are export-time tabulations of the frozen originals
  (generator shipped in `scripts/export-tools/`).
