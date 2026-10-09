# IP2 -> IP1: E4 formal run 6 closure

status: CONFIRMED
run_id: e4p1-a719364175d67653b560bf1b386f8539
operation_id: 96e5c2cf2d59452b87a0a7e43387971f
condition: healthy/no-fault

The result package passed `sha256sum -c` (2/2). Both control slots contained
empty argv and sent no command.

- pre-control requests: 7/7
- between control slots: 11/11
- post-control requests: 19/19
- total requests: 37/37
- `COMPLETE` coverage intervals: 2507
- all experiment-window coverage: complete
- collector: finalized with `coverage=intervals_only`
- receiver SHA-256:
  `eb72e57098de5bedd719fe64ca328393c4a2ec4fb0c5dc8c37a00b6855570062`
- correlation SHA-256:
  `07ca026944c2f50dbf854da5adba4c5bcefadc129534d698e104904ede12db56`

Native result is PASS and measurement complete. Actual window outcomes remain
FAIL 5 / UNKNOWN 1 / NOT_RUN 0.

An initial local correlation draft used an incorrect ISO-to-epoch offset and
produced an empty window. It was rejected by the nonzero/2507 assertions and
replaced from the unchanged receiver original using strict UTC parsing. The
hash above identifies the accepted correlation.
