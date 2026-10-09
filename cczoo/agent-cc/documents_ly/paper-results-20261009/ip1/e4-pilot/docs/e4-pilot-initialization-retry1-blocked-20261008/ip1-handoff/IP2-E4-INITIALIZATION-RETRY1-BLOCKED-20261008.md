# IP1 -> IP2: E4 initialization retry1 blocked — archive observation NOT_FOUND under approved events policy

schema: argus.e4.ip1-retry1-blocked.v1
status: NOT_RUN (coordinator stopped before the measurement window, per ruling step 5)
run_id: e4p1-d6771e94005b51df0fd60a23ba981087
operation_id: bd7bb0058e134591862acb6f990a3a1a
output_dir: /secure/e4-pilot/runs/e4p1-d6771e94005b51df0fd60a23ba981087-g1/
initialization_generation: 1

## What IP1 executed (ruling steps 3-5)

1. Patched protected config with `initialization_generation: 1` (configuration() validates; controls intact).
2. New output directory with the SAME manifest run_id and recovery condition, fresh
   seed session `ed03a5dc-7b97-a888-4a10-4b3015c11813` (not 0693418d).
3. preflight PASS; then `step.py continuous run` executed initialization once.
4. Coordinator stopped before the window: phase=initializing, confirmed=false,
   steps=0, STEP-RC=1, ended 2026-10-08T14:21:35Z. No fault/recovery controls
   invoked. No further seed writes performed (single commit at 14:10:39Z).

## Gate legs that PASSED on the fresh session

| leg | observation |
|---|---|
| session creation with approved policy | POST /api/v1/sessions accepted; server readback shows the approved policy incl. `working_memory.enabled=false` |
| add + commit | OBSERVED; commit_count=1; commit reported archived:true, archive_uri `history/archive_001`, task ccb82c14-e7c9-4e1d-a81c-994f73017e7d (commit poll status "timeout" = the known single-getTask-failure break; extraction continued server-side) |
| extraction task completed | status completed at 2026-10-08T14:12:09Z; `memories_extracted: {"memory_write": 2}`; memory_diff_uri present |
| find (expanded query) | 13 memories returned; the two NEW event memories collectively contain all four exact values: `c432b91016c8` (project_id), `1b9a4aed6b20` (normal_code), `d4fcdf6d6876` (review_code), `30000` (threshold_cents) |

## Gate leg that FAILED — archive observation

`getSessionArchive(ed03a5dc, archive_001)` → `OpenViking request failed [NOT_FOUND]:
Archive archive_001 not found` — on every reconcile attempt (14:12Z→14:21Z, bounded
retry budget 600s exhausted) → each inspect returned GATEWAY_IO_FAILED →
confirmed never true → NOT_RUN.

The raw archive directory EXISTS and is intact:

- `history/archive_001/messages.jsonl` (1442B, 14:10:39Z) — contains the exact seed text verbatim (`Private project c432b91016c8: …` present).
- `history/archive_001/memory_diff.json` (3406B, 14:12:09Z) — the 2 event adds, both with full value-preserving content.

But the server-side archive OBJECT is not servable:

- context stats: `{"totalArchives": 1, "includedArchives": 0, "droppedArchives": 0, "failedArchives": 1}`
- `latest_archive_overview`: empty string
- history listing abstract for archive_001: `"Directory abstract is not ready"`

## Contrast — the blocked-run session still serves its archive

Session `0693418d-66a3-8888-4340-ef89d64583a9` (created WITHOUT explicit session
policy; server readback `memory_policy: null`, i.e. default) returns a full archive
object from the same endpoint: abstract `"# Working Memory"`, complete overview,
and the original seed message. Its archive was assembled 12:43:47Z.

## Hypothesis for IP2 adjudication

The servable archive object IS the working-memory archive (old-session abstract is
literally `# Working Memory`). The approved policy disables working memory
(`"working_memory": {"enabled": false}`); for the fresh session the server recorded
`failedArchives: 1` and never produced the archive object, so the unchanged gate's
archive-observation leg can never pass. The IP2 diagnostic exercised the
task/extraction/find legs but not the archive leg of the unchanged gate.

Possible remedies for IP2 to rule on (IP1 will not act without a new ruling):

1. Approved policy with `working_memory.enabled=true` (memory_types still
   `["events"]`) — if IP2 verifies the wm archive still yields value-preserving
   extraction, this would satisfy both the four-value and archive legs.
2. Server-side archive build fix for wm-disabled sessions.
3. Ruling-sanctioned alternative archive observation for the gate.

## Preservation

- Blocked output dir, session 0693418d, its archive/task/11 memories: untouched.
- Retry1 output dir (-g1), fresh session ed03a5dc, its archive dir (raw files),
  task ccb82c14, and the 2 event memories: preserved as the failed retry attempt.
- No seed write will be performed without a new ruling (ruling step 5).

Evidence: E4-RETRY1-EVIDENCE.json (0600, schema argus.e4.initialization-retry1-evidence.v1)
