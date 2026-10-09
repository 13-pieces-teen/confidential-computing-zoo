# IP1 -> IP2: E4 formal run 5 result (seed 103, fault/recovery)

schema: argus.e4.ip1-formal-run-result.v1
status: FORMAL_RUN_5_EXECUTED_AWAITING_IP2_CORRELATION
run_id: `e4p1-7998d97419ea05161cc33525d2b1b3c3`
operation_id: `6bd58b2c32524239922cf4c50e0c46af`
condition: fault; generation: 1

## Outcome

native result **PASS**; exit 0; measurement_complete true; window 630 s
(20:00:08.747Z -> 20:10:38.747Z); six steps offered; s00 answered at
window +145.6 s (within the 180 s deadline) with reason ANSWER_INCORRECT;
s01-s05 CHECKPOINT_UNCONFIRMED. Window-axis counts: FAIL 5 / UNKNOWN 1 /
NOT_RUN 0 (execution-window axis, not task or receipt success).

## Five-leg initialization (all confirmed)

- fresh session `89b0b7eb-0a73-6583-8f04-48f251690048` (user
  `argus-eval/e4p1-s103-recovery`), commit_count 1;
- extraction task completed, memories_extracted `{"memory_write": 3}`;
- archive_001 servable, abstract "# Working Memory", seed text observed;
- find probe: 3 event memories, collective four-value hit
  (project_id fe96eba346f1 / normal_code 97430d8d4850 / review_code
  1926e1d86446 / threshold_cents 30000) - true;
- memory policy: events, self/peer enabled, working_memory enabled.

## Controls (owner-verified argv, {run_id} substituted)

- fault: planned 20:03:08.747Z (=start+180 s), completed 20:03:09.914Z,
  rc 0; argv_sha256 `b0f4d271bdce19b39a31251af24304e1f24477805c0ba6278c8dff1edad01030`
  (remote_acceptance.py fault --execute-fault --hold-recovery);
- recovery: planned 20:06:08.747Z (=start+360 s), completed 20:06:21.121Z,
  rc 0; argv_sha256 `06123d651d427888a43a844e773034d3424f5561dce46509a9ced31e901cd415`
  (e4_owner_recovery.py).

## Model

siliconflow/deepseek-ai/DeepSeek-V3.2, model_config_sha256
`cffc15f6a4a29b47c2695431e69d75a6cd747c1ddace3aaeb633a80bb646b76f`,
no model mismatches.

## Hashes

- configuration_sha256 `13d380b60f175fd0be89817189a1263dd3d95a1ffee86d9261d49e3dd30aebb9`
- manifest_sha256 `a95b1ec619de0806d72cbae1c7fca3c7ae7418a804e22abdcb517296c71d4e8d`
- protocol_digest `4cab0d439051372c047611128be007234c8faa122e2cfbcbbaa0294bdfd49e89`
  (identical to formal runs 1-4 - protocol fields exclude seed/secret;
  seed-103 pair consistency expected with run 6)
- evidence file: `E4-FORMAL-RUN5-EVIDENCE.json` (0600, SHA256SUMS)

## Execution transparency note

The first launch attempt (~19:53Z) hit a transient gateway dispatch failure
that left the seed session uncreated server-side (GET session returned 404);
the runner's reconcile cannot re-seed. IP1 stopped the run, reset the
unconfirmed initialization entry (backup kept:
`continuous/state.json.bak-e4p1-s103-recovery-initfail`), verified gateway
health with a direct preflight probe (OBSERVED), and replayed the same
run/operation IDs. The successful replay seeded, confirmed and executed the
full window; the `preflight_failed` event of the aborted attempt remains in
events.jsonl as an execution-history record. One further transient
preflight failure occurred during the first replay attempt and self-cleared
on the second replay.

Remaining formal run 6 (seed 103, no_fault) is unchanged and not executed;
IP1 awaits IP2's per-run preflight for run 6.
