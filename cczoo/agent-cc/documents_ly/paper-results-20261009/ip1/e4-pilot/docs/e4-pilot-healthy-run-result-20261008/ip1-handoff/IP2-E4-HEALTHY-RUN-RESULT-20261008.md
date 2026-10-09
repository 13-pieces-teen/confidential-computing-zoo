# IP1 -> IP2: E4 healthy/no-fault pilot result — PASS, no controls sent

schema: argus.e4.ip1-healthy-run-result.v1
status: PASS
condition: no_fault
run_id: e4p1-3d00004a2a939c240ce5883134fbdbae
operation_id: f09728c153934b2ebbdb1982049f9bc0
output_dir: /secure/e4-pilot/runs/e4p1-3d00004a2a939c240ce5883134fbdbae
step: exit_code 0, native_result PASS, measurement_complete true
window: 2026-10-08T17:00:55.102Z -> 17:07:55.103Z (420s)
protocol_digest: 8d88ab185faacfe31cc5d6c6c1ed3f467a4f9e5cd330edcbf4d4121d16a5be84
  (identical to the recovery pilot's — the pair shares schedule, client labels,
  control times)

## Initialization — five-leg gate, all confirmed on a fresh healthy session

| leg | observation |
|---|---|
| session creation | fresh session `ea4fce10-2a76-4df0-6f74-a2839f452abe` under fresh user `argus-eval/e4p1-healthy` |
| add + commit | commit_count=1 |
| extraction task | completed; `memories_extracted: {total: 2, memory_write: 2}` |
| archive observation | archive_001 servable, abstract `# Working Memory`, original seed text observed verbatim, context `failedArchives=0` |
| expanded find | events subtree returns the two event memories (行程工作项规则确认.md, 行程规则数据提供.md); collectively all four exact values: `3810aa3987aa`, `d98daf87bdc7`, `5152cc660efb`, `30000` |

The client-side commit wait-poll reported status `timeout` (the known benign
single-read bail); the server-side extraction task reached `completed` and the
reconcile observed the servable archive — same pattern as recovery generation-2.

## Window — six steps offered, no controls sent

All six steps were offered on schedule (s00@0s … s05@300s). This round sent
**no fault and no recovery control**: both control slots ran with empty argv
(`argv_sha256` = sha256 of the empty array `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`,
status `no_fault`, returncode 0, at the planned 120s/240s marks).

Step axes (evidence_scope `execution_window_not_task_or_receipt_success`):
s00 dispatched, answered at window+129.8s vs its 120s deadline →
`DEADLINE_MISSED` → FAIL; s01 UNKNOWN (predecessor s00 committed=UNKNOWN);
s02–s05 FAIL (prerequisite_uncommitted, never dispatched). Counts:
FAIL 5, UNKNOWN 1, NOT_RUN 0. IP2 receiver correlation determines the
business-level outcomes.

## Notes

- First preflight attempt failed with GATEWAY_PROCESS_UNAVAILABLE (runner
  environment lacked DOCKER_HOST) and a second launch lacked the runner env
  guard; both aborted before any business write (zero writes), rerun under the
  same operation_id completed cleanly.
- Gateway identity switched to `e4p1-healthy` with IP2's delivered key before
  preflight; the recovery identity is preserved as
  `openclaw.json.e4p1-recovery.bak` inside the gateway container.
- model: e4c1 configured_model `siliconflow/deepseek-ai/DeepSeek-V3.2`, no
  mismatches.

Evidence: E4-HEALTHY-RUN-EVIDENCE.json (0600, schema argus.e4.healthy-run-evidence.v1)
