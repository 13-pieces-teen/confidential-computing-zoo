# IP2 -> IP1: E4 formal run 5 closure

status: CONFIRMED
run_id: e4p1-7998d97419ea05161cc33525d2b1b3c3
operation_id: 6bd58b2c32524239922cf4c50e0c46af
condition: fault/helper-freeze

The result package passed `sha256sum -c` (2/2). Owner evidence records the
exact target, verified hold, matching owner release, and restored
Helper/NGINX/1943 readiness.

- pre-fault requests: 8/8
- fault-hold requests: 7/7
- recovery-transition requests: 1/1
- post-recovery requests: 18/18
- total requests: 34/34
- `COMPLETE` coverage intervals: 2507
- collector: finalized with `coverage=intervals_only`
- receiver SHA-256:
  `857aa06202f848788368ebd435936dcf2ac615d445352052893ae6eb41e793a6`
- fault SHA-256:
  `50352064e46c2360ee3f1bf0cac1aa038ff3dc072202ea74f9c3a6c00dcc3ce9`
- recovery SHA-256:
  `07deee03465bcd08b690f36bed1d0ff6df2ee591f0937dd1a5d5b694809ebfe9`
- correlation SHA-256:
  `5e4bda26171139cadeedf90ea6e47494b75443f07bc2f3334b4fe1e350f4062a`

Native result is PASS and measurement complete. Actual window outcomes remain
FAIL 5 / UNKNOWN 1 / NOT_RUN 0. The preserved transient initialization
failures remain execution-history evidence and are not represented as
successful work.
