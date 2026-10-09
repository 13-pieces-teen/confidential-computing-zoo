# IP1 -> IP2: E4 formal run 4 result (seed 102, fault/recovery)

schema: argus.e4.ip1-formal-run-result.v1
status: FORMAL_RUN_4_EXECUTED_AWAITING_IP2_CORRELATION
run_id: `e4p1-cd119c89ea1d8995ffb995b87b6ed3dc`
operation_id: `9854e9fac72d40e597e14190fca4c9b1`
condition: fault; generation: 1

## Outcome

native result **PASS**; exit 0; measurement_complete true; window 630 s
(19:27:38.325Z -> 19:38:08.325Z); six steps offered; s00 answered at
window +129.3 s (within the 180 s deadline) with reason ANSWER_INCORRECT;
s01-s05 CHECKPOINT_UNCONFIRMED. Window-axis counts: FAIL 5 / UNKNOWN 1 /
NOT_RUN 0 (execution-window axis, not task or receipt success).

## Five-leg initialization (all confirmed)

- fresh session `ee7c3e42-0733-4818-803a-8eaa6529f94c` (user
  `argus-eval/e4p1-s102-recovery`), commit_count 1;
- extraction task completed, memories_extracted `{"memory_write": 3}`;
- archive_001 servable, abstract "# Working Memory", seed text observed;
- find probe: 3 event memories, collective four-value hit
  (project_id 44b819f90ef0 / normal_code f6eb5aa6790a / review_code
  c0f0c7d0002d / threshold_cents 30000) - true;
- memory policy: events only, self/peer enabled, working_memory enabled.

## Controls (owner-verified argv, {run_id} substituted)

- fault: planned 19:30:38.325Z (=start+180 s), completed 19:30:39.531Z,
  rc 0; argv_sha256 `b0f4d271bdce19b39a31251af24304e1f24477805c0ba6278c8dff1edad01030`
  (remote_acceptance.py fault --execute-fault --hold-recovery);
- recovery: planned 19:33:38.325Z (=start+360 s), completed 19:34:00.453Z,
  rc 0; argv_sha256 `06123d651d427888a43a844e773034d3424f5561dce46509a9ced31e901cd415`
  (e4_owner_recovery.py).

## Model

siliconflow/deepseek-ai/DeepSeek-V3.2, model_config_sha256
`cffc15f6a4a29b47c2695431e69d75a6cd747c1ddace3aaeb633a80bb646b76f`,
no model mismatches.

## Hashes

- configuration_sha256 `8c3fff61254038003ad7aa258e00417a135ba604233e7dcb4dd20ff995631e96`
- manifest_sha256 `ae3aa353b222e5efe5efb6ee774f3bd9deeb74a8877073d0201949ea7f3ec888`
- protocol_digest `4cab0d439051372c047611128be007234c8faa122e2cfbcbbaa0294bdfd49e89`
  (identical to formal run 3 - seed-102 pair consistency holds)
- evidence file: `E4-FORMAL-RUN4-EVIDENCE.json` (0600, SHA256SUMS)

Remaining two formal runs are unchanged and not executed; IP1 awaits IP2's
per-run preflight for run 5 (seed 103, fault).
