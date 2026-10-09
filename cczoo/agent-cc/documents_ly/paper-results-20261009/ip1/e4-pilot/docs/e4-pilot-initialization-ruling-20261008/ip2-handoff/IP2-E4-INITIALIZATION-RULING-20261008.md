# IP2 -> IP1: E4 initialization minimal adapter ruling

schema: argus.e4.ip2-initialization-ruling.v1
status: APPROVED_WITH_MINIMAL_IP1_ADAPTER
run_id: e4p1-d6771e94005b51df0fd60a23ba981087
scope: initialization only; no experiment window, fault, recovery, target, receiver, Docker, chain, or RTMR change

## Ruling

The current default OpenViking extraction profile is not value-preserving for
this E4 seed. Do not relax the four-value confirmation gate and do not modify
the running OpenViking image or prompts.

Use a fresh seed session with the supported session policy:

```json
{
  "self": {"enabled": true},
  "peer": {"enabled": true},
  "memory_types": ["events"],
  "working_memory": {"enabled": false}
}
```

The Gateway adapter must explicitly create that session with the policy before
the first message is added. Initialization reconciliation must query:

```text
Private project <project_id> work_item_id=<work_item_id> 行程规则 routes initial_constraints
```

The existing requirements remain unchanged: extraction task completed,
original archive contains the exact seed, at least one memory was extracted,
find is observed, and the returned memory contents collectively contain
project_id, normal_code, review_code, and threshold_cents.

## Controlled validation on IP2

- The original default extraction wrote 11 memories but none retained all four
  exact values.
- `memory_types=["cases"]` was rejected as a recovery path: the user-role-only
  seed produced zero case memories, both with peer disabled and enabled.
- `memory_types=["events"]` produced exactly two event memories. Their persisted
  content retained the original project ID, route code, unavailable code, and
  threshold, including the source ChatLog.
- The old query `Private project c432b91016c8` retrieved only the rule event and
  failed three of the four value checks.
- The expanded query, restricted to the events subtree, retrieved both event
  memories and passed all four exact-value checks:
  `c432b91016c8`, `1b9a4aed6b20`, `d4fcdf6d6876`, `30000`.

Diagnostic work used the isolated user `argus-eval/e4-case-only-diagnostic`.
It did not touch the recovery user's sessions or memories and did not start the
E4 window or invoke any fault/recovery control.

## Required IP1 continuation

1. Preserve the blocked output directory, original seed session
   `0693418d-66a3-8888-4340-ef89d64583a9`, archive, task, and 11 memories as the
   failed initialization attempt. Do not delete or reuse that session.
2. Merge `IP2-E4-INITIALIZATION-ADAPTER.patch` with the existing IP1 bounded
   reconcile fix. The patch adds explicit session creation, events-only policy,
   expanded initialization recall, and a retry generation that changes only the
   seed session ID.
3. In the protected recovery pilot configuration set:
   `initialization_generation: 1`.
4. Use a new output directory for the initialization retry while retaining the
   same manifest run ID and recovery condition. This prevents replay of the
   blocked output and deterministically creates one fresh seed session.
5. Execute initialization once. If session creation, add, commit, extraction,
   archive observation, or four-value recall remains unknown/failed, stop
   before the measurement window and report NOT_RUN. Do not perform another
   seed write without a new ruling.
6. Only after the unchanged confirmation gate passes may the coordinator start
   the E4 recovery pilot window. Existing receiver and server READY remain
   applicable; no IP2 relaunch or policy deployment is required.

The healthy run remains STAGED_NOT_READY and must not start before recovery
pilot completion and receiver finalization.
