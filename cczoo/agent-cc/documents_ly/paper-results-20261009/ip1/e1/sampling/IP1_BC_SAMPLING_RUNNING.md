# E1 Full B/C — IP1 sampling running receipt

Ready at (IP1 UTC): 2026-10-04T04:26:30Z
run_id: e1-full-20261004 (B and C share this run_id)

## Sampling status: RUNNING

- probe session: guest sampler loop PID 140738
- prefix: BC-sample-20261004T0425Z (labels BC-sample-20261004T0425Z-N)
- script: /root/e1/e1-sample-loop.sh (serial, non-overlapping rounds,
  3 legs per round: /health, /api/v1/sessions, POST /api/v1/search/find;
  real-time credentials, no LLM, read-only)
- window_started_at (guest clock): 2026-10-04T04:24:55.588Z
- window_expires_at (guest clock): 2026-10-04T05:09:55.588Z
  (same as ~05:10:03.8Z IP1 clock, ~05:09:59.9Z IP2 clock — see skew below)
- remaining window at this ready_at: well above 240 s
- live records already produced at ready time: rounds 1-3, all three legs
  HTTP 200 (sessions/search result=OBSERVED); samples-snapshot/ holds the
  exact files at this instant

## Fresh clock measurement (this window, not reused from A3)

Measured at 2026-10-04T04:23:36Z, 3 sandwich rounds (local before/guest/IP2/
local after), spread <= 0.156 s:

- guest clock is behind IP1 by 8.178 s (A3 value was 7.028 s; not reused)
- IP2 clock ahead of IP1 by 0.396 s measured via proxied ssh (round-trip
  latency not fully removed; true offset may be smaller)
- no manual clock adjustment was performed on any side

Details in clock-skew-bc.json.

## Evidence and return path

- guest originals (root-owned, private key never leaves guest):
  /root/argus-openclaw-evidence/e1-20261004/samples-bc/
  (rounds.jsonl, loop-status.jsonl, loop.pid)
  /root/argus-openclaw-evidence/e1-probes/probe.jsonl (appends)
  /root/argus-openclaw-evidence/e1-probes/rotation-watch.jsonl (appends)
- IP1 control-side collection (spire/trustee/nginx logs) remains active
- return path for this B/C window originals:
  /root/argus-ip1-handoff/e1-20261004-full-bc-evidence/ip1-handoff/
  (client + control originals, SHA256SUMS and correlation report will be
  delivered there after the window)

## Boundaries

No server-side operation, no Helper/Provider/Agent restart, no Docker action,
no barrier interaction is performed from IP1. Private key stays in the guest.
