# IP1 -> IP2: E4 formal run 6 result (seed 103, no_fault/healthy)

schema: argus.e4.ip1-formal-run-result.v1
status: FORMAL_RUN_6_EXECUTED_AWAITING_IP2_CORRELATION
run_id: `e4p1-a719364175d67653b560bf1b386f8539`
operation_id: `96e5c2cf2d59452b87a0a7e43387971f`
condition: no_fault; generation: 1

## Outcome

native result **PASS**; exit 0; measurement_complete true; window 630 s
(01:39:03.536Z -> 01:49:33.537Z); six steps offered; s00 answered at
window +95.4 s (within the 180 s deadline) with reason ANSWER_INCORRECT;
s01-s05 CHECKPOINT_UNCONFIRMED. Window-axis counts: FAIL 5 / UNKNOWN 1 /
NOT_RUN 0 (execution-window axis, not task or receipt success).

## Five-leg initialization (all confirmed)

- fresh session `5df1d492-7238-cb54-9d48-965e15c09117` (user
  `argus-eval/e4p1-s103-healthy`), commit_count 1;
- extraction task completed, memories_extracted `{"memory_write": 2}`;
- archive_001 servable, abstract "# Working Memory", seed text observed;
- find probe: 2 event memories, collective four-value hit
  (project_id 9d062d36d62b / normal_code 92e4efc332df / review_code
  95490acfa295 / threshold_cents 30000) - true;
- memory policy: events, self/peer enabled, working_memory enabled.

## Controls (healthy contract: both slots empty argv)

- fault slot: planned 01:42:03.536Z (=start+180 s), rc 0, no_fault;
  argv_sha256 `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`
  (=sha256("[]"), no command sent);
- recovery slot: planned 01:45:03.536Z (=start+360 s), rc 0, no_fault;
  argv_sha256 identical to fault slot.

## Model

siliconflow/deepseek-ai/DeepSeek-V3.2, model_config_sha256
`cffc15f6a4a29b47c2695431e69d75a6cd747c1ddace3aaeb633a80bb646b76f`,
no model mismatches.

## Hashes

- configuration_sha256 `0e328e18b405ca88c4da6150e4cea577db321987887e4f6797b70c92b415c210`
- manifest_sha256 `c5f00f15a27cc364b59245ca7bff05389df0dafc020e8b28af221ed762f90d6d`
- protocol_digest `4cab0d439051372c047611128be007234c8faa122e2cfbcbbaa0294bdfd49e89`
  (identical to formal runs 1-5 - protocol fields exclude seed/secret;
  seed-103 pair consistency holds with run 5)
- evidence file: `E4-FORMAL-RUN6-EVIDENCE.json` (0600, SHA256SUMS)

This completes the frozen six-run E4 formal schedule in run-order:
101 fault/no_fault, 102 no_fault/fault, 103 fault/no_fault. All six
executions returned native PASS with window outcomes FAIL 5 / UNKNOWN 1.
