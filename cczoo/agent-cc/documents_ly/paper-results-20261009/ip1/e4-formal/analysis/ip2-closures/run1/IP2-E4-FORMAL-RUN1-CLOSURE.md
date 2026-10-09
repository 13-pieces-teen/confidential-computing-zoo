# IP2 -> IP1: E4 formal run 1 closure

schema: argus.e4.ip2-formal-run-closure.v1
status: CONFIRMED
run_id: e4p1-eeacfeaf71560d84a0c8168b7a2c20b2
operation_id: d2f6dfcef50045d0a8067c6cf418074f

The IP1 result package passed checksum validation. Owner fault and recovery
records match the exact run, target, owner, hold path and hold hash. Recovery
restored all workload units, workload readiness and port 1943.

Receiver correlation:

- pre-fault: 7/7 request enter/end; 716 COMPLETE intervals
- fault hold: 7/7 request enter/end; 716 COMPLETE intervals
- recovery transition: no request; 33 COMPLETE intervals
- post-recovery: 20/20 request enter/end; 1042 COMPLETE intervals
- total: 34 unique requests; 2507 COMPLETE intervals

The collector was finalized through its protected control socket. The final
record conservatively reports `coverage=intervals_only` and an uncovered
shutdown tail; no global-complete claim is made.

Run-level native result is PASS and measurement is complete. The actual
execution-window result remains FAIL 5 / UNKNOWN 1 / NOT_RUN 0; it is not
rewritten as task success.

Evidence hashes:

- receiver:
  `59aef087f4a872609c078390011c6145c76f0f6efbe3b28d9c9a57f31941cf75`
- fault:
  `779fc3924a96438bef6c013560f9baa31e7a53a817ba0f1a81a36209a9691910`
- recovery:
  `7b47e78ccb5d5381203d6dce92d53c35098d98895ad0070bd3faa8284652b051`
- correlation:
  `ddfdd1ad99eb75e252e9c8b59ede90798d111593c4dd8a5f47196b37250181f7`
