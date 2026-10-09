# IP1 -> IP2: E2 retry1 NOT_RUN diagnosis — two root causes, both fixes identified

status: NOT_RUN_PRESERVED
attempt: `e2-full-helper-freeze-20261008-r1-retry1` = `NOT_RUN`
reason: `BASELINE_OR_INFLIGHT_READ_UNREACHABLE` (state.json, both attempts)
audit run_id: `paper-20261004t145835z`
fault injected: **NO** — fault budget still unused, no `fault.jsonl`, no recovery hold.
Coordinator stopped before fault submission; per the retry contract the attempt is
preserved as NOT_RUN and no fault was manufactured.

## 0. Correction to the r1 package

The r1 diagnosis package stated the probe's existing lane was `200/200`.
Re-audit of `output/trace.jsonl` with a row-type filter shows the r1 existing lane
was actually **1 ok + 199 failed** (1x `ResponseNotReady`, 198x `ConnectionError`),
byte-for-byte the same failure signature as retry1. The r1 buffering root cause for
the in-flight gate remains valid, and IP2's `proxy_request_buffering off` fix is
confirmed working (retry1 shows live `body_read` chunk records in the durable
journal before the stream was severed; new lane 144/144). The correction matters
because the existing-lane failure was present in **both** attempts and is the
direct reason the `fault_ready` gate could never pass in either.

## 1. Root cause #1 (IP1 client-side, deterministic): Python 3.9 `http.client` read1 Content-Length defect

The probe runs on IP1 with Python 3.9. In 3.9, `HTTPResponse.read1` never closes
the connection when Content-Length is exhausted:

```python
# /usr/lib/python3.9/http/client.py
def read1(self, n=-1):
    ...
    result = self.fp.read1(n)
    if not result and n:
        self._close_conn()
    elif self.length is not None:
        self.length -= len(result)   # 3.9: no zero-check -> never closes at end
    return result
```

Python 3.11 adds `if not self.length: self._close_conn()` after the decrement.

Consequence for the probe's `read_response` loop
(`remote_acceptance.py`, read-until-complete): after a fully-read 391-byte
response the response object is left "open". The pinned existing lane then sends
request #2, `getresponse()` raises `ResponseNotReady`, the lane's exception branch
calls `conn.close()`, and `PinnedConnection.once=True` makes every subsequent
connect raise `ConnectionError("original TLS connection closed; automatic
reconnection is disabled")`. The lane is permanently dead: 1 ok + 199 failures.
`fault_ready` requires last-3-ok per lane and can never pass.

Server-side confirmation from the durable journal: existing-lane request #2
`5edbdeae-0b41-433d-b7ee-15912139722f` reached the app (`request_enter` at
05:46:24.018Z, record_seq 80683) but its body never did — `body_read` 0 bytes
`message_type=http.disconnect` at 05:46:24.019Z (80685), `request_end` 05:46:24.021Z
(80686). The server accepted the request; the client aborted it right after the
local `ResponseNotReady`. (The inflight kill at +22.76s is a separate cause, see §2.)

Trace signatures (request rows only), both attempts:

| attempt | existing lane | new lane |
|---|---|---|
| r1 | 1 ok, 1x ResponseNotReady @+0.6s, 198x ConnectionError | 141/141 ok |
| retry1 | 1 ok, 1x ResponseNotReady @+0.5s, 198x ConnectionError | 144/144 ok, last @+59.9s |

The server is healthy and honors keep-alive on the pinned connection: curl with
the same credentials shows `Content-Length: 391` and successful connection reuse
(`/tmp/curl-verbose2.txt`, 14:00+08).

Deterministic reproduction on the live server (same probe code, 4s window):

| interpreter | existing lane | new lane | tls_connections |
|---|---|---|---|
| python3.9 (evidence `diag-python39-trace.jsonl`) | 1 ok, then ResponseNotReady + 13x ConnectionError | 10/10 ok | — |
| **python3.11** (evidence `diag-python311-trace.jsonl`) | **10/10 ok, zero error rows** | 10/10 ok | pinned at 1 throughout (connection reused, never reopened) |

Fix: run the single r3 attempt under **python3.11** (3.11.13 installed on IP1).
Zero code changes: `fault_trial.py` launches the probe with `sys.executable`
(`argv = [sys.executable, ...connection_facts.py, "probe", ...]`), so the
coordinator interpreter propagates to the probe. Smoke-tested: `fault_trial`,
`connection_facts`, `remote_acceptance` all import cleanly under 3.11.

## 2. Root cause #2 (IP2 server-side, timing-dependent): nginx reload every ~140s from the helper publish hook

`helper.conf` declares `broker.publish_hook = nginx-hook.sh`; every credential
publication executes `systemctl reload` on the container nginx unit. Measured
cadence: publications at 06:12:29Z and 06:14:49Z (140s); nginx worker pid churns
in lockstep (350624 -> 352443 -> 354056). A reload replaces the worker; with
`worker_shutdown_timeout 5s` the old worker drains and closes its connections.
This kills (a) the pinned existing-lane connection if it lands during the 60s
baseline, and (b) the in-flight chunked stream mid-window.

retry1 match, all timestamps from the durable journal
(`evidence/receiver-excerpts-20261008.txt`):

- inflight stream `644876dd-c86e-46e6-95b6-668d32b35afb`: `request_enter`
  05:46:23.610Z (80673); body chunks land ~1.93s apart; chunk 12 at +21.26s
  (81211, 05:46:44.866Z); chunk 13 `body_read` 0 bytes `http.disconnect`
  at **05:46:46.367Z** (81250, stream +22.76s); `request_end` 05:46:46.368Z.
- client side (`trace-retry1-probe.jsonl`): stream started 05:46:23.495Z,
  15/32 chunks sent (3960 bytes), TLS error at 05:46:52.58Z (+29.1s).
- The +5.2s gap between the app-side disconnect (05:46:46.4Z) and the client-side
  TLS close (05:46:52.6Z) matches `worker_shutdown_timeout 5s`: the old worker
  stops reading, the app sees EOF, and the TLS close lands one shutdown grace
  later. No other mechanism in this stack produces that signature.
- Cadence back-extrapolation: 140s steps backwards from the measured 06:12:29Z
  publication land at 05:46:49Z — inside the retry1 window, seconds away from the
  measured disconnect. (Full table in `evidence/reload-cadence-20261008.txt`.)

Impact on the two NOT_RUN attempts: in r1 the existing lane was already dead from
cause #1 and the in-flight stream survived because no reload landed in its window;
the failing gates were `fault_ready` (cause #1) and the in-flight first-read
(buffering, since fixed). In retry1 `fault_ready` still failed from cause #1, and
the reload severed the in-flight stream before the gate could complete.

## 3. Verified non-issues

- `proxy_read_timeout 30s` is NOT a blocker: the 40s slow-chunked discriminator
  stream (`7a94f078-9d08-4081-b8bd-ae05e180edc7`) delivered all 32 chunks —
  `body_read` chunk 32 `more_body=false` at enter+39.999s (98483),
  `request_end` at +40.2s, 66 records, no disconnect. The 30s timer arms only
  after the request body completes; the app responds ~0.2s later.
- The buffering fix is confirmed: retry1 in-flight produced live `body_read`
  records from chunk 1 (no end-of-stream batching) until the reload severed it.
- Server keep-alive + Content-Length: confirmed by curl (§1).

## 4. Plan for the single r3 attempt

IP1 (all local, no tooling change):

1. Run `python3.11 fault_trial.py run` with a fresh output directory
   (e.g. `/root/argus-e2-ip1/output-r3`), fresh credential generation synced
   right before start (>=150s validity), same live identities, tunnel and
   payload plan. Exactly one run, per contract.
2. If the coordinator again stops before fault submission: preserve NOT_RUN,
   no fault manufactured.

Requested from IP2:

1. **Reload-free window** for the run: the helper publish -> nginx reload cycle
   (~140s) must not fire during the ~90s trial window (60s baseline + in-flight
   + fault delivery). Options: suspend/hold publishes for the window, or
   coordinate the start to a verified gap between publications. This is the only
   remaining server-side risk once IP1 runs under 3.11.
2. Confirm the refreshed retry fields for r3 (trial id, `fault_file` path, any
   server-field delta), same shape as `E2-RETRY-SERVER-FIELDS.json`.
3. Standing contract unchanged: IP2 keeps the faulted state until IP1 reports
   observation/collection/finalize complete.

## 5. Evidence bundle (`ip1-handoff/evidence/`)

- `diag-python39-trace.jsonl` — 4s probe under 3.9 on the live server: existing
  lane dies after 1 ok (ResponseNotReady -> ConnectionError x13).
- `diag-python311-trace.jsonl` — 4s probe under 3.11: existing 10/10 ok,
  tls_connections pinned at 1, zero error rows.
- `trace-r1-probe.jsonl` — full r1 probe trace (existing 1 ok / 199 bad).
- `trace-retry1-probe.jsonl` — full retry1 probe trace (existing 1 ok / 199 bad;
  new lane 144/144; inflight 15/32 then TLS error at +29.1s).
- `receiver-excerpts-20261008.txt` — durable-journal records for 5edbdeae
  (client-bug disconnect), 644876dd (reload kill), 7a94f078 (40s survivor).
- `reload-cadence-20261008.txt` — publication/reload cadence measurements and
  the back-extrapolation table.
