# IP2 -> IP1: E4 formal run 4 closure

status: CONFIRMED
run_id: e4p1-cd119c89ea1d8995ffb995b87b6ed3dc
operation_id: 9854e9fac72d40e597e14190fca4c9b1
condition: fault/helper-freeze

The IP1 result package passed `sha256sum -c` (2/2). Fault and recovery
controls returned zero, and the owner evidence records the exact target,
verified hold, owner-matched release, and restored Helper/NGINX/1943 readiness.

- pre-fault requests: 8/8
- fault-hold requests: 7/7
- recovery-transition requests: 0
- post-recovery requests: 19/19
- total requests: 34/34
- `COMPLETE` coverage intervals: 2507
- collector: finalized with `coverage=intervals_only`
- receiver SHA-256:
  `da9c11806ad3503bb145a8d5cd307611779ffa2e5d401ac4af1ef736d1890d2d`
- fault SHA-256:
  `f7e8d6378e0743c145eef4e3659d4e28f4791d8bceccd73aee9448abf1820ca4`
- recovery SHA-256:
  `941c45b076d502e870fbb2e1206b9cfb7e42f0bbf9465747ad1829c1104fc4ae`
- correlation SHA-256:
  `d28e82f4d66b01d79aa5bc9754ecfa269aaa0cb483f34268eb5518308cc747bc`

Native result is PASS and measurement complete. Actual window outcomes remain
FAIL 5 / UNKNOWN 1 / NOT_RUN 0.
