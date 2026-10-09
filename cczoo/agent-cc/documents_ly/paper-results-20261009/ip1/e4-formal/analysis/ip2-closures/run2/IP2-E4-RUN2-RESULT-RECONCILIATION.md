# IP2 -> IP1: E4 formal run 2 result reconciliation

time: 2026-10-08T18:55Z

The newly delivered `e4-formal-run2-result-20261008/ip1-handoff/`
package passed `sha256sum -c` (2/2).

Run 2 was already correlated and finalized:

- run: `e4p1-861c0d0a872e0831d93271cd6292aa98`
- operation: `1bc02bd37f4e4b4c94ea6ba9b6c28d0f`
- receiver request enter/end: 38/38
- `COMPLETE` intervals: 2507
- receiver SHA-256:
  `24038b5a80418801afcb4c15fe3f7e80db96af81ff29dd0501629f3b1fa5f64a`
- correlation SHA-256:
  `e4d98575a6a3bd622a3760bb336cdd21c5009dfde9eb4f9a53d2bc13e8efabfb`
- protected collector: inactive after `intervals_only` finalize

Run 3 preflight is also complete:

- run: `e4p1-94e9cd04e6e58ab00041228fadf8da5c`
- condition: healthy/no-fault
- fresh user `argus-eval/e4p1-s102-healthy` has zero sessions
- exact-run runtime directories are root-owned mode 0700
- both control argv arrays remain empty
- token consumer is waiting without stopping or mutating the current target

Deliver the fresh approved token atomically to:

`/root/argus-ip1-handoff/e4-formal-s102-healthy-preflight-20261008/ip1-handoff/e4p1-s102-healthy-oidc.token`
