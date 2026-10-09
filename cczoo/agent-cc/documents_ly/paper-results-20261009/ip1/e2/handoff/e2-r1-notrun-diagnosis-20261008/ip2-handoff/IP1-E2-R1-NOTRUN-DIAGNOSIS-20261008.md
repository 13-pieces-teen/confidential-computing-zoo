# IP1 -> IP2: E2 r1 NOT_RUN diagnosis — nginx request buffering blocks in-flight first read

status: NOT_RUN (no fault injected; paper02 stack untouched)
trial_id: e2-full-helper-freeze-20261008-r1
run_id: paper-20261004t145835z
observed_at_utc: 2026-10-08T05:16Z

IP1 executed exactly one `fault_trial.py run` against the frozen paper02 stack.
The coordinator exited NOT_RUN (BASELINE_OR_INFLIGHT_READ_UNREACHABLE) before any
fault command was issued. IP2-side fault.jsonl/lifecycle paths remain empty; the
single-fault guarantee is preserved.

## Client-side probe was fully healthy

- probe_stop complete=true, errors=[] (60 s window)
- existing lane: 200/200 ok (HTTP 200, tls_connections=1)
- new lane: 141/200 ok at deadline (HTTP 200, tls_connections=1)
- inflight lane: stream_start at t=0, 32 chunks / 8444 bytes paced across the
  window, sent_chunks=32, HTTP 200, peer_announced_close
- lifecycle observer: observer_ready within 2 s, healthy_baseline=true, COMPLETED

## In-flight first read never became observable (the gate that failed)

The coordinator polls `receiver_audit.collector status --request-id <inflight>`
every 0.25 s during the 60 s baseline budget. The receiver journal contains the
inflight request's records ONLY at stream completion:

- request_enter at 1791436638984 (t=60.2 s after probe start)
- body_read at 1791436638985: 8444 bytes in ONE read, more_body=false
- request_end at 1791436639168

All 32 chunks therefore reached the audited ASGI application in a single burst
when the stream finished; no first_read existed while the request was open.

## Root cause: paper02 business-path nginx buffers full request bodies

Container listeners (nsenter -t 248534 -n ss -ltnp):

- 0.0.0.0:1943  nginx  (mTLS + authz terminator, `-c /etc/argus-experiments/paper02/full_argus/nginx.conf`)
- 127.0.0.1:1933  python (receiver-audit-wrapped OpenViking uvicorn, pid 248534)

In that nginx.conf, `location /` proxies to 127.0.0.1:1933 with
`proxy_http_version 1.1` but WITHOUT `proxy_request_buffering off`. The nginx
default (`proxy_request_buffering on`) reads the entire request body into
`client_body_temp_path` before connecting to the upstream, so the audited app
never sees the request while it is in flight.

## Reproduction evidence

A minimal slow chunked POST from IP1 through the same path (headers + chunk0 at
t=0, body completed at t=10 s):

- receiver journal: 0 records at t+2 s and t+7 s; all 4 records at t=10 s
- client log (buffering-test.log): response arrived 0.1 s after body completion
- transport legs verified streaming: garbage bytes into 127.0.0.1:1944 produced
  a TLS alert at +0.05 s and EOF at +0.12 s, so the ssh tunnel / docker-proxy
  legs pass data immediately; the buffering is nginx-side

## Requested IP2 action

Add `proxy_request_buffering off;` inside the `location /` block of
/etc/argus-experiments/paper02/full_argus/nginx.conf and reload the container
nginx (the master stays; the worker is replaced). nginx.conf is not in the
prep tool_sha256 set, but please re-verify binding/target records and readiness
after the reload if worker identity is part of the deployment binding.

With buffering off, nginx will stream the chunked body to 127.0.0.1:1933
(`proxy_http_version 1.1` is already present), the ReceiverAudit ASGI middleware
will emit per-chunk body_reads, and the coordinator's in-flight first-read gate
can fire. IP1 then re-runs the single fault trial with fresh credentials (the
NOT_RUN attempt consumed no fault budget).

## Package contents (sha256sum -c SHA256SUMS)

- trace.jsonl, releases.jsonl, lifecycle-live.jsonl — full client-side probe evidence
- receiver-inflight-excerpt.jsonl — the 8 receiver records for the inflight request and the reproduction POST
- state.json / result.json / probe-diagnostic.json / lifecycle-diagnostic.json
- buffering-test.log — reproduction client log
