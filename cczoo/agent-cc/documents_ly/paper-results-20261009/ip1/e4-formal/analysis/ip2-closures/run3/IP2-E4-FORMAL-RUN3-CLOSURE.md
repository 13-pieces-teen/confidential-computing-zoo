# IP2 -> IP1: E4 formal run 3 closure

status: CONFIRMED
run_id: e4p1-94e9cd04e6e58ab00041228fadf8da5c
operation_id: 863256d8999147af80164615591b7e29
condition: healthy/no-fault

The IP1 result package passed `sha256sum -c` (2/2). Both control slots used
empty argv and sent no command.

- pre-control requests: 8/8
- between control slots: 11/11
- post-control requests: 20/20
- total requests: 39/39
- `COMPLETE` coverage intervals: 2507
- all experiment-window coverage: complete
- collector: finalized with `coverage=intervals_only`
- receiver SHA-256:
  `867e7bd1a800e84c8eece4d0f51ee3db13fc693f217152063f430a34ad168dbb`
- correlation SHA-256:
  `e050dc6f3499271af1b8d227ce3e33f274fbfb12fb1a6584780f0fdde05ce5ad`

Native result is PASS and measurement complete. Actual window outcomes remain
FAIL 5 / UNKNOWN 1 / NOT_RUN 0.
