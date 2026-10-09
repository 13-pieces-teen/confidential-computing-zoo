# 2026-10-09 repair and rerun batch

This subtree appends evidence and results without changing the frozen prior E4
six-run batch.

Current contents:

- `preexisting/e2-r4/`: independently reproducible E2 r4 experiment and
  recovery receiver windows, including surrounding watermarks, coverage
  intervals, finalization records, clock bounds, source hashes, crop hashes,
  and the preserved UNKNOWN final tail.
- `preexisting/e1-offline-history-fixtures/`: seven synthetic signed fixtures
  for the production history sub-verifier, including public verification
  material and fixed archived Rekor transport. They are not online attack
  trials.

Pending online work is recorded as `NOT_RUN` until the repaired tool is
deployed and the healthy pilot, E3/E5 window, fault pilot, and gated paired
batch complete. Missing values are never encoded as zero.
