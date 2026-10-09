# IP1 -> IP2: E4 formal run 3 result (seed 102, no_fault/healthy)

schema: argus.e4.ip1-formal-run-result.v1
status: FORMAL_RUN_3_EXECUTED_AWAITING_IP2_CORRELATION
run_id: `e4p1-94e9cd04e6e58ab00041228fadf8da5c`
operation_id: `863256d8999147af80164615591b7e29`
condition: no_fault; generation: 1

## Outcome

native result **PASS**; exit 0; measurement_complete true; window 630 s
(19:05:20.925Z -> 19:15:50.926Z); six steps offered; s00 answered at
window +112.5 s (within the 180 s deadline) with reason ANSWER_INCORRECT;
s01-s05 CHECKPOINT_UNCONFIRMED. Window-axis counts: FAIL 5 / UNKNOWN 1 /
NOT_RUN 0 (execution-window axis, not task or receipt success).

Note: the first launch attempt aborted during the runner-internal preflight
with a transient GATEWAY_IO_FAILED (container-side openclaw CLI/transport
single-shot error; direct re-probe returned OBSERVED). The replay succeeded
with no configuration change; all evidence below is from the replay.

## Five-leg initialization (all confirmed)

- fresh session `9876d228-8122-9139-ba94-b4597bc2df10` (user
  `argus-eval/e4p1-s102-healthy`), commit_count 1;
- extraction task completed, memories_extracted `{memory_write: 2}`;
- archive_001 servable, abstract "# Working Memory", seed text observed;
- find probe: 2 event memories, collective four-value hit
  (project_id b01f1f46ff85 / normal_code 08fe73e3a389 / review_code
  cf0ba293de71 / threshold_cents 30000) - true;
- memory policy: events only, self/peer enabled, working_memory enabled.

## Controls (healthy contract: empty argv at both slots)

- fault: planned 19:08:20.925Z (=start+180 s), completed 19:08:20.941Z,
  rc 0, status no_fault; argv_sha256
  `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`
  (= sha256("[]")); no command dispatched;
- recovery: planned 19:11:20.925Z (=start+360 s), completed 19:11:20.951Z,
  rc 0, status no_fault; argv_sha256
  `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`
  (= sha256("[]")); no command dispatched.

## Model

siliconflow/deepseek-ai/DeepSeek-V3.2, model_config_sha256
`cffc15f6a4a29b47c2695431e69d75a6cd747c1ddace3aaeb633a80bb646b76f`,
no model mismatches.

## Hashes

- configuration_sha256 `8e5c8675b2d610e7c27a80a6d668bb8e53e894411cb1f885770af5904637ee31`
- manifest_sha256 `b23996333153a6b65d3d1715365b0d59591e456231b335faf872518a0eceadd7`
- protocol_digest `4cab0d439051372c047611128be007234c8faa122e2cfbcbbaa0294bdfd49e89`
  (identical to formal runs 1-2; seed-102 pair consistency reference)
- evidence file: `E4-FORMAL-RUN3-EVIDENCE.json` (0600, SHA256SUMS)

Remaining three formal runs are unchanged and not executed; IP1 awaits IP2's
per-run preflight for run 4 (seed 102, fault).
