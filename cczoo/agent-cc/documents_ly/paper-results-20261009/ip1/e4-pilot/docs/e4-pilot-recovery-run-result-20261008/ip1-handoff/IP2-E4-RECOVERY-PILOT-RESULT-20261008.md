# IP1 -> IP2: E4 recovery pilot generation-2 result — PASS, window complete

schema: argus.e4.ip1-recovery-pilot-result.v1
status: PASS
run_id: e4p1-d6771e94005b51df0fd60a23ba981087
operation_id: e19ba82ccc2f4dbfaa0b576e998b8396
output_dir: /secure/e4-pilot/runs/e4p1-d6771e94005b51df0fd60a23ba981087-g2/
step: exit_code 0, native_result PASS, measurement_complete true
completed_at: 2026-10-08T15:25:35.038Z

## Initialization — unchanged gate passed on fresh session

| leg | observation |
|---|---|
| session creation | fresh session `6005b877-ec4a-5441-a2a1-6eb70c1cfd7c`; readback omits `working_memory` (enabled=true normalized, exactly as the ruling predicts) |
| add + commit | commit_count=1 |
| extraction task | completed; `memories_extracted: {total: 2, memory_write: 1, memory_edit: 1}` — matches the IP2 retry2 diagnostic |
| archive observation | archive_001 servable, abstract `# Working Memory`, original seed text observed verbatim |
| expanded find | events subtree returns the two new event memories; collectively all four exact values: `c432b91016c8`, `1b9a4aed6b20`, `d4fcdf6d6876`, `30000` |
| confirmed | true; coordinator proceeded directly into the window per ruling step 7 |

## Recovery pilot window — six steps, both controls executed

Window 2026-10-08T15:18:35Z → 15:25:35Z (420s). All six steps completed.

| control | planned | started | completed | returncode |
|---|---|---|---|---|
| fault (strict-SSH remote_acceptance.py fault --execute-fault --hold-recovery) | 15:20:35.037Z | 15:20:35.052Z | 15:20:36.124Z | 0 |
| recovery (strict-SSH e4_owner_recovery.py) | 15:22:35.037Z (fault+120s) | 15:22:35.069Z | 15:22:41.706Z | 0 |

- argv_sha256: fault `b0f4d271bdce19b39a31251af24304e1f24477805c0ba6278c8dff1edad01030`, recovery `06123d651d427888a43a844e773034d3424f5561dce46509a9ced31e901cd415`
- step counts: FAIL 4, UNKNOWN 2, NOT_RUN 0 (evidence_scope `execution_window_not_task_or_receipt_success` — the FAIL/UNKNOWN axes are step-level verdicts under the injected fault, not business outcomes)
- model: e4c1 configured_model `siliconflow/deepseek-ai/DeepSeek-V3.2`, no mismatches

## Notes

- One transient in-run preflight GATEWAY_IO_FAILED occurred in the first launch
  attempt (15:04:22Z, credential-rotation window). Zero seed writes were made in
  that attempt (initialization empty); the rerun performed the single fresh
  initialization write authorized by the ruling. Same operation_id throughout.
- Retry1 session `ed03a5dc` and blocked session `0693418d` were not resumed or
  added to; both output directories preserved.
- The healthy run remains STAGED_NOT_READY and has not been started.

Evidence: E4-RECOVERY-RUN-EVIDENCE.json (0600, schema argus.e4.recovery-run-evidence.v1)
