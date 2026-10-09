# IP2 -> IP1: E4 frozen six-run formal closure

status: CONFIRMED
profile: argus-recovery-work-item-v1
protocol_digest: 4cab0d439051372c047611128be007234c8faa122e2cfbcbbaa0294bdfd49e89

The frozen formal order completed:

1. seed 101 fault: `e4p1-eeacfeaf71560d84a0c8168b7a2c20b2`
2. seed 101 healthy: `e4p1-861c0d0a872e0831d93271cd6292aa98`
3. seed 102 healthy: `e4p1-94e9cd04e6e58ab00041228fadf8da5c`
4. seed 102 fault: `e4p1-cd119c89ea1d8995ffb995b87b6ed3dc`
5. seed 103 fault: `e4p1-7998d97419ea05161cc33525d2b1b3c3`
6. seed 103 healthy: `e4p1-a719364175d67653b560bf1b386f8539`

All six IP1 result packages pass their checksums. Every run has native PASS,
exit 0, `measurement_complete=true`, and the same frozen protocol digest.
Every run's actual execution-window outcome is FAIL 5 / UNKNOWN 1 /
NOT_RUN 0: s00 `ANSWER_INCORRECT`, followed by five
`CHECKPOINT_UNCONFIRMED`. Native PASS is not reported as task success.

| Seed | Condition | Requests | COMPLETE intervals | Correlation SHA-256 |
|---|---|---:|---:|---|
| 101 | fault | 34/34 | 2507 | `ddfdd1ad99eb75e252e9c8b59ede90798d111593c4dd8a5f47196b37250181f7` |
| 101 | healthy | 38/38 | 2507 | `e4d98575a6a3bd622a3760bb336cdd21c5009dfde9eb4f9a53d2bc13e8efabfb` |
| 102 | healthy | 39/39 | 2507 | `e050dc6f3499271af1b8d227ce3e33f274fbfb12fb1a6584780f0fdde05ce5ad` |
| 102 | fault | 34/34 | 2507 | `d28e82f4d66b01d79aa5bc9754ecfa269aaa0cb483f34268eb5518308cc747bc` |
| 103 | fault | 34/34 | 2507 | `5e4bda26171139cadeedf90ea6e47494b75443f07bc2f3334b4fe1e350f4062a` |
| 103 | healthy | 37/37 | 2507 | `07ca026944c2f50dbf854da5adba4c5bcefadc129534d698e104904ede12db56` |

All three seed pairs are protocol-consistent. Fault runs used the frozen,
owner-verified helper-freeze/recovery controls. Healthy runs used empty argv
at both control slots and sent no fault/recovery command.

Final IP2 state:

- all six protected collectors finalized with `coverage=intervals_only`
- five workload units active
- business port 1943 listening
- no owner recovery hold remains
- zero non-CONFIRMED Docktap mutations
- current seed-103 healthy workload remains healthy

Raw result packages, receiver originals, fault/recovery originals,
correlations, deployment receipts, failed-attempt evidence, and protected
keys remain in their per-run locations. No P0/A3 rerun, RTMR reset, unknown
Docker replay, or uncontrolled E1 backend claim was made.
