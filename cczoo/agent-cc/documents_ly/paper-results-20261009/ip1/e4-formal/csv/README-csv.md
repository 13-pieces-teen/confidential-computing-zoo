# E4 CSVs — fields, objects and denominators

All three CSVs are deterministic offline tabulations of the six frozen
originals (`runs/r*/continuous/result.json`, `state.json`, `events.jsonl`).
The generator (`../../scripts/export-tools/generate-e4-csvs.py`) performs no
scoring: every cell is a verbatim field of a frozen original, except
`answer_offset_s` (= `answer_at_ms - started_at_ms`, seconds). Regenerate:

```
python3 ../../scripts/export-tools/generate-e4-csvs.py ../runs .
```

## What PASS / FAIL 5 / UNKNOWN 1 mean (actual as-run validator, continuous.py `result_for`)

- **`result: PASS`** — execution-window measurement verdict, a conjunction of
  four conditions: (1) `state.phase == 'complete'`; (2) `state.controls` has
  exactly the two slots fault+recovery with the expected status
  (`completed` on fault runs, `no_fault` on healthy runs) and returncode 0;
  (3) `result.model_mismatches` empty; (4) the window actually started.
  `evidence_scope = execution_window_not_task_or_receipt_success`: **PASS does
  not mean the task succeeded and does not mean the receipt/correlation
  succeeded.**
- **`counts`: FAIL 5 / UNKNOWN 1** — per-step task results, denominator
  `planned_tasks = 6` (the six planned steps s00–s05; `counts` counts each
  step's `task_result` once). All six runs are step-identical:
  - `s00` FAIL `ANSWER_INCORRECT`: dispatched (agent answered) but the answer
    was a memory_store failure report, not the expected decision JSON;
    committed=UNKNOWN, so the s00 proposal stays UNKNOWN.
  - `s01` UNKNOWN `CHECKPOINT_UNCONFIRMED`: its checkpoint (the s00 proposal)
    is UNKNOWN → not dispatched.
  - `s02`–`s05` FAIL `CHECKPOINT_UNCONFIRMED`: prerequisite proposal not
    committed → not dispatched.
  - FAIL 5 = s00+s02..s05; UNKNOWN 1 = s01; NOT_RUN 0.
  - `attempted_tasks: 0` is the conservative positive-attestation counter:
    only steps with positive transport evidence (request body captured) count
    as attempted; s00's attempted=UNKNOWN is not counted. Not a defect.
- **work_items (per run, one work item)** — `complete_task_result`,
  `continuation_result`, `legal_recovery_result`, `receipt_result` are all
  `UNKNOWN`: unresolved proposal `s00`, all six required proposals unapplied.
  Per the frozen WORK-ITEM.md definitions, complete_task_result may only be
  PASS when all six required proposals are effective and every step's business
  result is correct; continuation may be judged separately; receipt and legal
  recovery require independent joint analysis (fact_receipts admission
  originals + a verified read instance + independent step scoring). Exit 0 or
  `/health` responses are explicitly not acceptance.

Full narrative, per-run five-leg inputs, s00 readback fields, and the
healthy/fault common-first-failure analysis:
`../analysis/IP2-E4-FORMAL-RESULTS-ANALYSIS-20261009.md`.

## Files

- `e4-six-run-summary.csv` — 6 rows: run-level verdict, counts, digests,
  control slots, model, timestamps.
- `e4-per-step.csv` — 36 rows (6 runs × 6 steps): per-step task_result,
  reason, dispatch/commit/write/recall/tool-call/transport readback fields,
  fact_id/fact_sha256/constraint_key, answer offset.
- `e4-per-work-item.csv` — 6 rows: the four work-item axes plus proposal
  resolution fields.
