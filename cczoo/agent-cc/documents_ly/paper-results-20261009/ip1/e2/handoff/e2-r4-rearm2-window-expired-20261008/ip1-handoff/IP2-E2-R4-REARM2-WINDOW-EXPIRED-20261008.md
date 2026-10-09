# IP1 -> IP2: E2 r4 — rearm2 window expired before the RUN NOW reached IP1; request release + coordination change

status: R4_REARM2_WINDOW_EXPIRED_BEFORE_RUN
trial: `e2-full-helper-freeze-20261008-r4`
audit run_id: `paper-20261004t145835z`
coordinator launched: **NO** — the single-run budget is preserved.
fault injected: NO — `fault.jsonl` absent, budget unused.

## Timeline

| time (Z) | event |
|---|---|
| 07:29:32 | client credential in the prepared r4 config (generation-1730107036) expired — the config was already stale before rearm2 was armed |
| 07:35:42.819 | IP2 rearm2 arm succeeded (workload ready=true) |
| 07:35:42 | server credential loaded at arm: serial `60992BE9F16CCCA698A15542EC3784F6`, **notAfter 07:38:55Z** (193s validity) |
| 07:38:55 | arm-loaded server credential expired; the hold's no-op hook never reloads, so NGINX keeps serving it |
| 07:40:15 | IP1's first possible launch check (the RUN NOW notice had just reached IP1): window already dead by **80s** |

## What IP1 did

On receipt of the RUN NOW notice, IP1 ran a single guarded launch chain: check
the arm window, then (only if viable) re-sync a fresh client credential, update
the r4 config, and start the one Python 3.11 coordinator. The guard measured
`now=07:40:15Z` against the receipt deadline `07:38:55Z` and aborted:

```
now=07:40:15Z left=-80s
ABORT: window too short (need ~135s)
```

Nothing was started: no coordinator process, no output directory, no
credential sync, nothing touched on the IP2 stack. `fault.jsonl` absent, the
single-run budget is intact. (Full guard output: `evidence/launch-gate-output.txt`.)

## The systemic problem: relay latency exceeds the arm window

This is the second window lost the same way:

| attempt | arm | window (cert validity) | IP1 first actionable moment | outcome |
|---|---|---|---|---|
| r4      | 07:23:25Z | 190s (exp 07:26:35Z) | ~07:27:32Z (blocked by a transient IP1 control-plane outage, then expired) | expired |
| rearm2  | 07:35:42.8Z | 193s (exp 07:38:55Z) | 07:40:15Z (RUN NOW received; already dead by 80s) | expired |

The arm->notify->relay->launch path costs ~4.5 minutes end to end, while the
arm guarantees only ~180-290s of served-certificate validity. **No arm whose
"RUN NOW" must traverse the relay can survive** — the window is structurally
shorter than the coordination path. A third rearm under the same mechanism
would expire the same way.

## Requests

1. Release the rearm2 publication hold through the owner-token release path.
   NGINX is currently serving an expired certificate, so new mTLS handshakes
   on 1943 fail until release. (The hold is otherwise benign: publications
   keep writing credentials and audit events; no crash loop.)
2. **Coordination change for the next attempt — standing arm authorization.**
   After IP2's READY, IP1 executes the entire critical chain locally, in one
   uninterrupted sequence with zero intermediate steps:

   ```
   sync fresh client credential (>=150s) -> arm -> launch coordinator immediately
   ```

   The relay is removed from the critical path: nothing between arm and
   launch can consume the window. If the arm refuses because the current
   server credential has <180s of validity, IP1 waits for the next normal
   publication and retries the arm (same retry rule as the r3 contract).
   IP2's constraints remain fully in force: IP1 arms only after explicit
   READY, through the strict alias, with the owner-token-protected control,
   exactly one arm and one run.

## Evidence

- `evidence/launch-gate-output.txt` — the guarded launch-chain output; the
  abort decision and timestamps above are taken verbatim from it.
- No coordinator outputs exist (nothing was launched).
