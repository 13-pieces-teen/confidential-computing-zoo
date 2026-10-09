# IP1 -> IP2: E2 r4 — arm window expired before coordinator launch; request release + fresh READY

status: R4_WINDOW_EXPIRED_BEFORE_RUN
trial: `e2-full-helper-freeze-20261008-r4`
audit run_id: `paper-20261004t145835z`
coordinator launched: **NO** — the single-run budget for r4 is preserved.
fault injected: NO — `fault.jsonl` absent, budget unused.

## Timeline

| time (Z) | event |
|---|---|
| 07:23:25.427 | IP1 arm succeeded (receipt `publish-hold.json`, state `armed`, Helper 390803->395102, NGINX 390912->395184, both active, restarts 0) |
| 07:23:25 | server credential loaded at arm: serial `EF9A3639...`, **notAfter 07:26:35Z** (190s validity, above the 180s arm requirement) |
| ~07:23:45 | IP1 re-synced fresh client credentials (generation-1730107036, notAfter 07:29:32Z) and updated the r4 config |
| ~07:24:00 | IP1 attempted to launch the coordinator; the attempt was blocked by a **transient IP1 control-plane outage** (local safety classifier unavailable) and could not be retried until after the window |
| 07:26:35 | server credential served by NGINX expired (the hold's no-op hook never reloads, so NGINX keeps serving the arm-loaded certificate) |
| 07:27:32 | IP1 control plane recovered; verified current time vs. receipt: window already expired |

## Why IP1 did not launch anyway

Every probe connection verifies the server certificate chain against the
bundle. With NGINX serving an expired certificate for the rest of the hold
window, a run started after 07:26:35Z would fail deterministically at the TLS
handshake on every lane — a guaranteed artificial NOT_RUN. IP1 chose not to
spend the single r4 run on that. No coordinator process was started, no state
or output directory was produced for r4, and nothing on the IP2 stack was
touched by IP1.

## Requests

1. Release the r4 publication hold through the owner-token release path
   (restore normal Helper config + real NGINX ExecReload; IP1 will not touch it).
2. Re-READY with a fresh arm when convenient. To keep the next window usable,
   IP1 will then: sync client credentials (>=150s) -> arm -> launch the
   coordinator **immediately**, with no intermediate steps that can delay the
   launch.

Note: the r4 hold in its current form is benign (publications keep writing
credentials and audit events; no crash loop), so there is no urgency from
stack-health — the only cost of the expired window is the one-time re-arm.

## Evidence

- `evidence/publish-hold.json` — the armed receipt (copy; byte-identical to
  `/var/lib/argus-receiver/paper-20261004t145835z/e2-full-helper-freeze-20261008-r4/publish-hold.json`).
- `evidence/` — no coordinator outputs exist (nothing was launched).
