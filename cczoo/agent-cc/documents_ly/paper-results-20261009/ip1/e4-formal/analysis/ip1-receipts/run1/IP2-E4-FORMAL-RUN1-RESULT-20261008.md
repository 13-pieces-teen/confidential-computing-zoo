# IP1 -> IP2: E4 formal run 1 result (seed 101, fault/recovery)

schema: argus.e4.ip1-formal-run-result.v1
status: FORMAL_RUN_1_EXECUTED_AWAITING_IP2_CORRELATION
run_id: `e4p1-eeacfeaf71560d84a0c8168b7a2c20b2`
operation_id: `d2f6dfcef50045d0a8067c6cf418074f`
condition: fault; generation: 1

## Outcome

native result **PASS**; exit 0; measurement_complete true; window 630 s
(17:56:00.976Z -> 18:06:30.976Z); six steps offered; s00 answered at
window +121.1 s (within the 180 s deadline) with reason ANSWER_INCORRECT;
s01-s05 CHECKPOINT_UNCONFIRMED. Window-axis counts: FAIL 5 / UNKNOWN 1 /
NOT_RUN 0 (execution-window axis, not task or receipt success).

## Five-leg initialization (all confirmed)

- fresh session `e74eb5f0-ed93-cc83-664d-05565d0fbbc6` (user
  `argus-eval/e4p1-s101-recovery`), commit_count 1;
- extraction task completed, memories_extracted `{memory_write: 4}`;
- archive_001 servable, abstract "# Working Memory", seed text observed;
- find probe: 4 event memories, collective four-value hit
  (project_id 67cc1bdcc876 / normal_code 58615e5aa5a7 / review_code
  02c076b5de9e / threshold_cents 30000) - true;
- memory policy: events only, self/peer enabled, working_memory enabled.

## Controls (owner-verified argv, {run_id} substituted)

- fault: planned 17:59:00.976Z (=start+180 s), completed 17:59:02.292Z,
  rc 0; argv_sha256 `b0f4d271bdce19b39a31251af24304e1f24477805c0ba6278c8dff1edad01030`
  (remote_acceptance.py fault --execute-fault --hold-recovery);
- recovery: planned 18:02:00.976Z (=start+360 s), completed 18:02:09.136Z,
  rc 0; argv_sha256 `06123d651d427888a43a844e773034d3424f5561dce46509a9ced31e901cd415`
  (e4_owner_recovery.py).

## Model

siliconflow/deepseek-ai/DeepSeek-V3.2, model_config_sha256
`cffc15f6a4a29b47c2695431e69d75a6cd747c1ddace3aaeb633a80bb646b76f`,
no model mismatches.

## Hashes

- configuration_sha256 `8e9fde5be04feafaf570d75df86becccb0f43fc829cc39b57bbc39b8879bd0f5`
- manifest_sha256 `3b349b6374df11a7c7d29e6b95f4d2807d053f398fefafc4888a8a4745b68b28`
- protocol_digest `4cab0d439051372c047611128be007234c8faa122e2cfbcbbaa0294bdfd49e89`
- evidence file: `E4-FORMAL-RUN1-EVIDENCE.json` (0600, SHA256SUMS)

Remaining five formal runs are unchanged and not executed; IP1 awaits IP2's
per-run preflight for run 2 (seed 101, no_fault).
