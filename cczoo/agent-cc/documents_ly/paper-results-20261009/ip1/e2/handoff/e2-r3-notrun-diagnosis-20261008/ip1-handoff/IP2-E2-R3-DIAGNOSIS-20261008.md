# IP1 -> IP2: E2 r3 NOT_RUN diagnosis — the reload hold and the publish verify step are mutually incompatible

status: NOT_RUN_PRESERVED
attempt: `e2-full-helper-freeze-20261008-r3` = `NOT_RUN` (BASELINE_OR_INFLIGHT_READ_UNREACHABLE)
audit run_id: `paper-20261004t145835z`
fault injected: **NO** — `fault.jsonl` absent (r3 trial dir contains only `lifecycle.jsonl`,
`lifecycle.stop`, `reload-hold.json`). Fault budget still unused.
Per the r3 contract: the coordinator stopped before fault submission, the attempt is
preserved as NOT_RUN, and IP1 notifies IP2 so it can **release the NGINX reload hold**.
The arm receipt and drop-in were left untouched by IP1.

## What worked

IP1 ran under Python 3.11 as agreed. The pinned-lane defect is **gone**: zero
`ResponseNotReady`, zero `ConnectionError`. After the server recovered at +7.2s,
both lanes ran 53s of consecutive successful requests (existing 128 ok / new 123 ok,
`tls_connections` held). The client-side root cause #1 is closed.

## What failed: the hold crashes the Helper on every publication

The hold makes `ExecReload=/bin/true`, so NGINX never loads a newly published
certificate. But the publish hook (`nginx-hook.py`, publish action) ends with a
verification step:

```python
run([*namespace, d.bin / "spiffe-mtls-probe", "-tls-only", ...,
     "-cert", d.credentials / "current/svid.pem", ...])
```

`spiffe-mtls-probe` confirms NGINX serves the *published* serial. With the hold
armed it fails every time ("NGINX did not load the published certificate"),
`run()` raises, and the Helper main process exits with status 1. The Helper unit
failure then propagates through `BindsTo=ax-paper02-full-helper.service` and
systemd SIGTERMs NGINX. After the 5s restart delay the Helper restarts, its first
publish finds NGINX inactive and uses the hook's `systemctl start` branch, the
fresh NGINX loads the current certificate, the verify passes, and the stack is
stable — until the next publication.

Measured cycle (host journal, `evidence/journal-crash-cycles-20261008.txt`):

- publication: `Reloading ... Reloaded` (ExecReload=/bin/true, no churn)
- verify probe retries ~30x over ~3s, then fails
- Helper exits status=1 -> `Stopping ax-paper02-full-nginx` -> 1943 down
- +5s Helper restart (NRestarts climbing: 1 at 06:48:10Z, 4 at 06:55:11Z, 5 by 06:57:28Z)
- next publish starts NGINX (new master), verify passes, stable until next publication

Observed cycles: 06:48:01-06:48:14Z, ~06:52:42-06:52:46Z, 06:55:02-06:55:15Z,
~06:57:28-06:57:33Z. Roughly 9-11s of 1943 outage per cycle, and the cycle repeats
on every publication (every ~2.5-5 minutes).

## r3 timeline: the run started inside an outage window

| time (Z) | event |
|---|---|
| 06:47:19.9 | arm's pre-arm reload (SIGHUP, worker 369011 -> 369789) |
| 06:47:20.1 | arm receipt written; master 250619 |
| 06:48:01.9 | publication -> reload = /bin/true -> verify begins |
| 06:48:04.97 | verify fails; Helper subscription ended |
| 06:48:05.0 | Helper exits status=1; BindsTo SIGTERM to nginx; master 250619 exits; **1943 down** |
| 06:48:06.53 | r3 probe first request — **1.5s after the crash** |
| 06:48:06.58 | in-flight stream request killed (ConnectionResetError, 48ms after start) |
| 06:48:10.2 | Helper restart (counter 1) |
| 06:48:13.98 | publish -> nginx inactive -> `systemctl start`; new master 371842 listening |
| 06:48:14.0 | new SVID published (exp 06:53:01Z), verify passes |
| 06:48:13.7 -> 06:49:06 | both lanes 53s consecutive ok |

Trace signature (`evidence/trace-r3-probe.jsonl`): 27 ConnectionResetError per
lane, strictly inside the first 7.2s at the 0.25s cadence, then 53s clean. The
in-flight stream (started at +0.0s) died in the outage and never retried; the
`inflight_first_read` gate could not pass -> NOT_RUN. (The `fault_ready` gate —
last-3-ok per lane — passed this time; the only failing gate was in-flight
first-read.)

## Active harm to the paper02 stack

The hold is still armed and is crash-looping the Helper on every publication:
NRestarts=5 as of 06:57Z, a ~9-11s 1943 outage per cycle, Helper and NGINX master
pids churning. **IP1 recommends IP2 release the reload hold promptly** (IP2-owned
release per the r3 contract; IP1 has not touched it). Between cycles the stack is
healthy.

## Options for the next attempt (IP2's call)

The underlying goal — no reload in the trial window — remains necessary (root
cause #2). But the ExecReload hold cannot work while the publish verify step
remains, because verify failure crashes the Helper. Options:

1. **No-op publish hook for the trial window (recommended).** Publications keep
   writing credentials; the hook's reload + verify are skipped entirely; Helper
   never crashes; NGINX serves its already-loaded certificate (valid for the
   rotation period). Deterministic, no crash cycle, Helper (the fault target)
   untouched.
2. **Verify-aware hold.** A hold that neutralizes both the reload AND the verify
   step (equivalent to option 1 via a different mechanism).
3. **Coordinated start in a natural gap.** Start immediately after a
   verify-passed publication, giving ~120s of stability. Workable but tight:
   the coordinator needs ~70-100s from start to fault submission, and a precise
   IP2->IP1 start signal is required. Root cause #2's 5s drain hazard remains
   for any late publication.

IP1 is ready to re-run the single attempt under Python 3.11 whenever IP2
re-READYs with a stable-window mechanism. Same contract: exactly one
`fault_trial.py run` in a fresh output directory; if it stops before fault
submission again, NOT_RUN is preserved and no fault is manufactured.

## Evidence bundle (`ip1-handoff/evidence/`)

- `journal-crash-cycles-20261008.txt` — host journal for the helper/nginx units
  06:47-06:58Z: all four crash cycles, the verify failure traceback, BindsTo
  stops, restart counters.
- `trace-r3-probe.jsonl` — full r3 probe trace: 27 resets/lane in the first 7.2s,
  then 53s clean; in-flight died at +0.05s.
- `state.json`, `result.json`, `run.log` — coordinator outputs (NOT_RUN,
  BASELINE_OR_INFLIGHT_READ_UNREACHABLE).
