# IP1 -> IP2: E4 recovery run initialization blocked — extraction dropped rule values

schema: argus.e4.ip1-finding.v1
status: BLOCKED_AT_INITIALIZATION
reported_at: 2026-10-08T13:16Z
run_id: e4p1-d6771e94005b51df0fd60a23ba981087
condition: fault (recovery pilot, manifest order 1)
coordinator_state: STALLED in phase initializing — no window started, no step
  dispatched, no fault/recovery control fired, healthy run untouched
  (STAGED_NOT_READY as instructed)

## Finding 1 (IP1-side, fixed): coordinator reconcile raced the extraction

- 12:38:05Z run start; seed written to session
  0693418d-66a3-8888-4340-ef89d64583a9; commit POST archived Phase 1.
- The plugin's commit wait-poll (DEFAULT_PHASE2_POLL_TIMEOUT_MS=300 s) bailed
  early with status "timeout" after a single transient getTask read failure
  (plugin behavior: one failed poll read breaks the loop).
- The immediate reconcile then requested the archive before Phase 2 made it
  servable: GET /archives/archive_001 -> 404 -> GATEWAY_IO_FAILED -> run
  aborted 12:38:54Z. The extraction task
  942702c6-88ee-46dc-b786-1598f182d135 completed later, at 12:43:47Z.
- Fix applied to continuous.py (IP1 coordinator only): reconcile now
  re-queries the same seed session every 15 s (budget 600 s) while the task
  is non-terminal; reconcile never adds or commits messages. Seed's docker
  exec budget raised to cover the full 300 s poll. Unit tests 17/17 pass.
- This alone does NOT unblock: after completion the confirm still fails
  (Finding 2).

## Finding 2 (IP2-side middleware, blocking): extraction dropped rule values

- The seeded rule text (private, /secure) explicitly requires:
  "后续 memory_recall 必须能回读全部原始提案，不能只保留摘要或 Decision".
- The completed extraction wrote 11 memories under
  viking://user/e4p1-recovery/memories/ (memory_diff: 11 adds; cases 1,
  preferences 1, trajectories 4, experiences 5; task result
  memories_extracted={memory_write:2}).
- None of the 11 stored contents contains any of the four rule values:
  project_id c432b91016c8 / normal_code 1b9a4aed6b20 /
  review_code d4fcdf6d6876 / threshold_cents 30000. The values were
  rephrased into prose (e.g. the 30000 budget and all route codes are
  absent), so the step recall path (find over viking://user/memories)
  cannot return the exact codes the work-item decisions require.
- find("Private project c432b91016c8") returns exactly these 11 memories;
  none contains the rule values. The initialization confirm
  (extraction completed + archive holds the seed text + rule values
  retrievable via find) can therefore never pass.
- Evidence: E4-INITIALIZATION-EVIDENCE.json (session/task/commit/find
  summary, memory_diff sha256) and EXTRACTION-DIFF-SUMMARY.json (per-add
  uri tail, memory_type, value-hit booleans, lengths), both 0600.

## Questions for IP2

1. Is the E4 recovery profile's extraction prompt/model the intended
   value-preserving configuration? The observed output contradicts the
   seeded rule text's verbatim-recall requirement.
2. If the extraction configuration can be corrected server-side, IP1
   awaits an updated READY/preflight. IP1 can then re-run: reconcile will
   re-query the existing seed session without any new write, or IP1 can
   re-seed a fresh session if IP2 prefers.
3. If this extraction behavior is the intended final configuration,
   authorize IP1 to relax the initialization confirm (accept extraction
   completion + find OBSERVED without the raw-value requirement). Steps
   will then honestly measure the resulting recall quality.

No E4 task, fault or recovery control has been executed; only the one seed
session and its extraction (an IP1-initiated seed, the first E4 model
request) exist.
