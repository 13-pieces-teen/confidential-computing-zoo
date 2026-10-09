# ARGUS paper experiments — IP1 results export (2026-10-09)

This directory is the IP1-side data/documentation export of the ARGUS
confidential-computing experiment campaign, prepared for offline local
analysis. Export date 2026-10-09. **No experiment was re-run, no service was
restarted, and no frozen result was modified to produce this export.**

Scope: E4 formal six-run batch (complete, frozen, IP2 CONFIRMED) in full
detail; E1/E2/E3/E5 existing client data, workload results, resource
sampling, control-side originals and handoff documents as they exist on the
IP1 host. Held-back and never-existing items: `MISSING.md`.

## Completed experiments

| phase | status | evidence here |
|---|---|---|
| E1 (native, preseed + full A-window diagnosis) | executed; root cause UNKNOWN, field frozen pending authorized bounded diagnostics | `e1/` |
| E2 (helper-freeze fault trial; r1/retry1/r3 NOT_RUN diagnoses, r4 PASS) | executed | `e2/` |
| E3/E5 (probe header rerun; round4 401, round6 403, round7 200; IP2 receiver closure) | executed | `e3-e5/` |
| E4 pilot pair (recovery/healthy, digest 8d88ab18) | executed | `e4-pilot/docs/` |
| E4 formal six-run frozen batch (digest 4cab0d43, IP2 CONFIRMED 2026-10-09) | executed, closed | `e4-formal/` |

## Actual running versions

- Validator/acceptance code: repository commit
  `3cf25a87a2ba1ab4f6ff4ac0821ca515570fa5c7` + working-tree modifications
  for the eight as-run files (copies, sha256s and the diff patch in
  `scripts/as-run/`, provenance in `scripts/as-run/AS-RUN-PROVENANCE.md`).
- E4 profile `argus-recovery-work-item-v1`, frozen schedule (release 90 s /
  deadline 180 s / QA 180 s / stop 10 s; fault @+180 s, recovery @+360 s;
  window 630 s), model `siliconflow/deepseek-ai/DeepSeek-V3.2`
  (model_config_sha256 `cffc15f6…`). Details: `e4-formal/protocol/README-protocol.md`.

## Layout

```
README.md REDACTION.md MISSING.md MANIFEST.tsv SHA256SUMS
e4-formal/   protocol/  runs/r1..r6/  csv/  analysis/   <- six-run detail
e1/          control-evidence/ probes/ correlation/ originals-supplement/ sampling/ scripts/
e2/          configs/ scripts/ runs/ handoff/
e3-e5/       probe rounds + receiver closure
e4-pilot/    docs
infra/       channel/trust-bundle/node-policy/live-gate-fix/recovery-check records
scripts/     as-run/ (validators + diff + provenance)  export-tools/ (CSV generator)
```

## Offline analysis commands

```bash
# verify every exported file (run from this directory)
sha256sum -c SHA256SUMS

# re-tabulate the three E4 CSVs from the frozen run copies
python3 scripts/export-tools/generate-e4-csvs.py e4-formal/runs e4-formal/csv

# per-step / per-work-item / six-run views (PASS|FAIL5|UNKNOWN1 semantics in
# e4-formal/csv/README-csv.md)
column -s, -t < e4-formal/csv/e4-per-step.csv | less -S
column -s, -t < e4-formal/csv/e4-six-run-summary.csv | less -S

# cross-check the redacted protocol configs against their provenance hashes
sha256sum e4-formal/protocol/full-*.json   # seed hashes preserved as REDACTED:R1:sha256=…
```

The generator needs only Python 3.9+ standard library; the frozen run copies
are self-contained under `e4-formal/runs/`.

## Correlation fields kept / redaction

Run/request/attempt ids, timestamps, verdict fields, fact ids, control
argv_sha256 values and digests are preserved verbatim. Redaction rules R1–R3
(seed redaction with sha256, excluded credential files, allowlisted public
material) are documented in `REDACTION.md`; originals-to-export mapping in
`MANIFEST.tsv`.

## paper-minimal-20261009 (minimal supplement batch, added 2026-10-09)

Minimal supplement batch per materials at commit
`b471a34b73e4f8490fa633b7ea3bcfb510756d39` (branch
`docs/argus-minimal-experiments-20261009`, read-only). IP1-side client
originals only; the IP2-side observation/collect and joint SUMMARY live with
IP2. Batch id `argus-paper-minimal-20261009t0718z-01`, S1 run id
`…-rotation`.

- `existing-evidence-index.json` — S0 recovery of P0 authorization negatives
  (7/7) and real Agent-recall originals with paths and SHAs; S2/S3 SKIP
  rulings with citations.
- `s0-reality/` — the three pre-window probe rounds (identity/search legs) on
  the live paper02/full service.
- `rotation/…-rotation/` — complete frozen S1 client output: requests.jsonl
  (155 success / 25 unknown rotation-race, all non-empty), load-result.json,
  per-client c1/ originals, frozen load-config.json, body.json,
  clock-measurement.json, load-fleet log + tool SHAs. End-side coverage
  insufficient ~105 s: frozen, recorded, not re-run.

No secrets are exported (api-key files and private fixtures stay in protected
directories on the host). Mapping in `MANIFEST.tsv`.
