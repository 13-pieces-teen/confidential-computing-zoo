# E4 formal frozen protocol (six runs)

Profile: `argus-recovery-work-item-v1` (scenario `work-item-v1`, group
`full_argus`, model `siliconflow/deepseek-ai/DeepSeek-V3.2`,
model_config_sha256 `cffc15f6a4a29b47c2695431e69d75a6cd747c1ddace3aaeb633a80bb646b76f`).

Frozen schedule (identical across all six runs): `schedule.frozen=true`,
release 90 s, deadline 180 s, QA 180 s, stop budget 10 s, fault control at
+180 s, recovery control at +360 s, window 630 s, run_timeout_s 1200
(step `--timeout 1170`).

## run-order (frozen, from generated/run-order.json)

| # | run_id (ARGUS_RUN_ID) | seed | condition |
|---|---|---|---|
| 1 | e4p1-eeacfeaf71560d84a0c8168b7a2c20b2 | 101 | fault (recovery chain) |
| 2 | e4p1-861c0d0a872e0831d93271cd6292aa98 | 101 | healthy (no_fault) |
| 3 | e4p1-94e9cd04e6e58ab00041228fadf8da5c | 102 | healthy |
| 4 | e4p1-cd119c89ea1d8995ffb995b87b6ed3dc | 102 | fault |
| 5 | e4p1-7998d97419ea05161cc33525d2b1b3c3 | 103 | fault |
| 6 | e4p1-a719364175d67653b560bf1b386f8539 | 103 | healthy |

One client `e4c1` per run, six steps s00–s05, `initialization_generation=1`.

## protocol digest

All six runs share protocol digest
`4cab0d439051372c047611128be007234c8faa122e2cfbcbbaa0294bdfd49e89`.
The digest covers schedule/QA/model/client_ids/control-times only; it does
not include run_id/block_id/secret_seed, so it is identical across seeds.
Seed-pair consistency (101/102/103) was verified by both sides.

## Per-run configs in this directory

- `full-<seed>-{fault,healthy}.json` — the six frozen configs with the single
  redaction R1 applied (see ../REDACTION.md): `secret_seed` replaced by
  `REDACTED:R1:sha256:<sha256>`. `structure_seed` (101/102/103), synthetic
  bindings (`e4p1-s<seed>-<cond>` identities) and the frozen control argv
  lists are kept verbatim.
- `suite-input.json`, `suite.json`, `run-order.json` — verbatim frozen copies
  (no secret fields).
- Control argv (fault runs): argv_sha256 fault `b0f4d271bdce19b39a31251af24304e1f24477805c0ba6278c8dff1edad01030`,
  recovery `06123d651d427888a43a844e773034d3424f5561dce46509a9ced31e901cd415`;
  healthy runs: both slots empty argv, argv_sha256
  `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945` (= sha256('[]')).
  All fault-run commands returned rc 0 at exactly +180 s / +360 s.

## Clock-correction basis

- All `*_at_ms` values are gateway-host wall-clock milliseconds (clock domain
  `gateway_host_realtime` in events.jsonl); `*_monotonic_ns` where present are
  the local monotonic clock. Receiver-side correlation on IP2 used strict UTC
  parsing; an initial run-6 correlation draft with an incorrect ISO-to-epoch
  offset produced an empty window and was rejected by the nonzero/2507
  assertions, then recomputed from the unchanged receiver original (accepted
  correlation sha256 `07ca026944c2f50dbf854da5adba4c5bcefadc129534d698e104904ede12db56`).
- Cross-host UTC offset was measured at ~0.7 s during run 6 (jump-host local
  timezone differs; UTC identical). E1/E2 clock-skew measurements are exported
  under ../e1/sampling and the e2 handoff packages.
