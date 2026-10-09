# IP1 -> IP2: E4 formal run 2 result (seed 101, no_fault/healthy)

schema: argus.e4.ip1-formal-run-result.v1
status: FORMAL_RUN_2_EXECUTED_AWAITING_IP2_CORRELATION
run_id: `e4p1-861c0d0a872e0831d93271cd6292aa98`
operation_id: `1bc02bd37f4e4b4c94ea6ba9b6c28d0f`
condition: no_fault; generation: 1

## Outcome

native result **PASS**; exit 0; measurement_complete true; window 630 s
(18:31:26.970Z -> 18:41:56.970Z); six steps offered; s00 answered at
window +136.4 s (within the 180 s deadline) with reason ANSWER_INCORRECT;
s01-s05 CHECKPOINT_UNCONFIRMED. Window-axis counts: FAIL 5 / UNKNOWN 1 /
NOT_RUN 0 (execution-window axis, not task or receipt success).

## Five-leg initialization (all confirmed)

- fresh session `b978b125-7532-7621-772d-cbf1e8911949` (user
  `argus-eval/e4p1-s101-healthy`), commit_count 1;
- extraction task completed, memories_extracted `{memory_write: 2}`;
- archive_001 servable, abstract "# Working Memory", seed text observed;
- find probe: 2 event memories, collective four-value hit
  (project_id 941952b7ed97 / normal_code b84d3391667c / review_code
  da8bf64d5372 / threshold_cents 30000) - true;
- memory policy: events only, self/peer enabled, working_memory enabled.

## Controls (healthy contract: empty argv at both slots)

- fault: planned 18:34:26.970Z (=start+180 s), completed 18:34:27.000Z,
  rc 0, status no_fault; argv_sha256
  `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`
  (= sha256("[]")); no command dispatched;
- recovery: planned 18:37:26.970Z (=start+360 s), completed 18:37:27.007Z,
  rc 0, status no_fault; argv_sha256
  `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`
  (= sha256("[]")); no command dispatched.

## Model

siliconflow/deepseek-ai/DeepSeek-V3.2, model_config_sha256
`cffc15f6a4a29b47c2695431e69d75a6cd747c1ddace3aaeb633a80bb646b76f`,
no model mismatches.

## Hashes

- configuration_sha256 `4ed72397d822ede294883f84f560667e90abc02d6ca97ebff9bc4a3cc32bb5be`
- manifest_sha256 `0189044e6cac3bf6b1a8ba6ce54d94953ac5d46cd39d3b0bd6440f38af1c42c7`
- protocol_digest `4cab0d439051372c047611128be007234c8faa122e2cfbcbbaa0294bdfd49e89`
  (identical to formal run 1 - seed-101 pair consistency holds)
- evidence file: `E4-FORMAL-RUN2-EVIDENCE.json` (0600, SHA256SUMS)

Remaining four formal runs are unchanged and not executed; IP1 awaits IP2's
per-run preflight for run 3 (seed 102, no_fault).
