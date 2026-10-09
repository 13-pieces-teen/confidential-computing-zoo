# E1 (native preseed + full A-window) — exported materials

- control-evidence/ — IP1 control-side originals for the A window: SPIRE
  server/client issuance log excerpts, nginx access window, trustee REST
  capture, diag probe records, trustee admission bundle (rekor.json public
  transparency record + trust bundles + policy input + capture).
- probes/a2-ready, a3-ready — probe/rounds/loop-status jsonl snapshots at
  receipt + readiness md + IP1_LIVE_PROBE_READY.json.
- correlation/ — A3 correlation md, control evidence, client originals
  (probe-a3-full.jsonl, rounds-a3-full.jsonl), clock-skew-measurement.json.
- originals-supplement/ — health snapshot, peer serials, gateway-follow log,
  rotation watch, final loop status.
- sampling/ — B/C sampling-running snapshot (rounds, loop status) and
  clock-skew-bc.json.
- continuation-approval/ — E1 continuation approval receipt.
- scripts/ — the probe/collector scripts actually used (guest-side
  e1-business-probe.sh, e1-checkpoint-probe.sh, e1-guest-collector.sh,
  e1-probe.mjs; host-side e1-sample-loop.sh, e1-watch-a3.sh).

E1 field outcome: root cause of the A-window failure remained UNKNOWN at
field-freeze; E1 was not re-run. Missing items: see ../MISSING.md.
