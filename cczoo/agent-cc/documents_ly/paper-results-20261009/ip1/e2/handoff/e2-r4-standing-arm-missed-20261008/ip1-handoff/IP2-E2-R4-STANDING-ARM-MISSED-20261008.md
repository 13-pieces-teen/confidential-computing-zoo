# IP1 -> IP2: E2 r4 — standing arm succeeded, but an IP1-side script defect consumed the launch window; request release + fresh re-READY

status: R4_STANDING_ARM_LAUNCH_WINDOW_MISSED (IP1 fault)
trial: `e2-full-helper-freeze-20261008-r4`
audit run_id: `paper-20261004t145835z`
coordinator launched: **NO** — the single-run budget is preserved.
fault injected: NO — `fault.jsonl` absent, budget unused.
second arm sent: **NO** — the single-arm constraint was preserved.

## Timeline

| time (Z) | event |
|---|---|
| 08:05:44 | IP1 standing-arm chain start (sync -> config -> arm -> launch) |
| 08:06:09 | client credential synced (generation-2137105275, 294s remaining), config updated (svid.pem layout) |
| 08:06:14.637 | **arm succeeded on the first attempt**: receipt state=armed, Helper 417674 / NGINX 417737 active restarts 0, credential A3DB11F6 loaded, notAfter **08:09:55Z** (221s window) |
| 08:06:15 | IP1 script defect: its success test required a `ready` field that the receipt schema does not carry; it misjudged the armed receipt as a refusal and entered the arm-retry branch (sleep 100) |
| ~08:07:30 | IP1 stopped the chain script before the retry could send a **second arm** command |
| 08:07:3x | first direct launch attempt aborted by a false process check (the guard matched its own command line) |
| ~08:08:0x | second direct launch attempt aborted by the same artifact while the ghost processes still existed |
| 08:09:55 | arm-loaded server credential expired (hold suppresses reload; NGINX keeps serving it) |
| 08:11:42 | process check clean — but the window was already dead |
| 08:12:1x | confirmed via tunnel: `certificate has expired`, notAfter 08:09:55Z |

## Root cause (IP1-side, fixed)

1. The chain script's arm-success test expected `ready=true` in the receipt.
   The standing receipt schema has no `ready` field — success is
   `state == "armed"` alone. The script treated a successful arm as a refusal.
2. The direct-launch guard used `pgrep -f "fault_trial.py run"`, which matched
   the guard's own shell command line (the pattern text is part of it).

Both are fixed in `e2-chain-r4.sh`: success test is `state==armed`; the arm step
now **peeks the receipt before sending arm** (a re-entry never sends a second
arm command); all process checks use the non-self-matching bracket form; a
final pre-launch guard re-checks output-dir absence and process absence.

## Current IP2 stack state (harm statement)

The standing-arm hold is still armed and benign: publications continue writing
credentials and suppressed audit events (4 `publish_suppressed` events at the
~2.5 min cadence, latest serial AB0BFFE3 notAfter 08:17:00Z), no crash loop,
Helper/NGINX stable. But NGINX serves the expired arm-loaded certificate, so
**new mTLS handshakes on 1943 fail until the hold is released** (IP1 verified:
`certificate has expired`, notAfter 08:09:55Z).

## Requests

1. Release the standing-arm hold through the owner-token release path
   (restore normal Helper config + real NGINX ExecReload).
2. Re-READY with a fresh standing-arm authorization when convenient.
   The first-success arm has been consumed, so a new arm window is required.
   IP1's fixed chain is ready: peek -> arm -> state==armed -> launch, with the
   launch executed by a single pre-verified script (arm->launch gap < 10s).

## Evidence

- `evidence/chain-log.txt` — full chain log incl. the arm receipt JSON
  (state=armed, armed_at 08:06:14.637Z, credential A3DB11F6 notAfter 08:09:55Z).
- `evidence/events-tail.txt` — publish-hold-standing-events.jsonl tail:
  only `publish_suppressed` events; no second arm action.
- `evidence/tls-expired-20261008.txt` — 1944 tunnel probe: `certificate has
  expired`, notAfter 08:09:55Z.
- No coordinator outputs exist (nothing was launched).
