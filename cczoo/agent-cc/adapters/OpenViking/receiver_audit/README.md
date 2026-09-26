# Application receiver audit (explicit experiments)

This adapter observes **HTTP entry into the ASGI application and bytes returned
by its ASGI `receive()`**. It does not establish model consumption, storage
revocation, memory erasure, TLS record receipt, or absence of already buffered
bytes. Use only synthetic experiment requests. No body, URL/query, API key,
other request header or prompt is written to the collector.

## Pinned image and unchanged application

The normal OpenViking Dockerfile is unchanged. This optional image derives from
the existing `ghcr.io/volcengine/openviking@sha256:27d3c97bddbe81f31d2c5af1f31e9d504b5928506c88f559a23faf86358169b7`.
The registry's amd64 image identifies version `0.4.8`, source revision
`07113f81e0edaebaacdd23ab138087b06fe871ab`. `upstream-lock.json` records the base,
platform manifest and exact startup file hashes. The image build and each
startup check those hashes and the original console entry. The wrapper calls
that entry (preserving its early `--config` parsing), wrapping only the final
ASGI app passed to Uvicorn. Extraction, ranking, auth and route implementations
remain upstream code. Multi-worker, factory and reload modes are rejected
because this version binds one actual service process.

Primary source references: [pinned lightweight entry](https://github.com/volcengine/OpenViking/blob/07113f81e0edaebaacdd23ab138087b06fe871ab/openviking_cli/server_bootstrap.py),
[pinned Uvicorn bootstrap](https://github.com/volcengine/OpenViking/blob/07113f81e0edaebaacdd23ab138087b06fe871ab/openviking/server/bootstrap.py).

From `cczoo/agent-cc/adapters/OpenViking`:

```sh
python3 receiver_audit/build.py --tag argus-openviking-audit:trial --manifest /secure/build-audit.json
```

Use the resulting image content digest in the normal image approval workflow;
publish/export using the existing TC API path. Do not reuse the base image's
approved digest. Building the image does not approve or deploy it. Build metadata
and runtime acceptance remain separate.

## Launch through TC API and bind the independent collector

This is an experiment instance. In its normal deployment input, add:

```json
"receiver_audit": {
  "run_id": "trial-a",
  "mode": "on",
  "image_config_digest": "sha256:<the approved derived image content digest>"
}
```

The digest must equal `approved.image_config_digest`. `run_id` matches
`[a-z][a-z0-9-]{0,63}`. The generated `tc-api-workload.json` carries this protected
operator setting; API request metadata cannot add mounts. TC API checks the
loaded image content ID before giving it the audit socket mount. It records
the added mount and environment in the ordinary launch security projection.
Use `launch`/`resume-launch`, never a manual Docker run to bypass that path.

Before launch, create dedicated root-owned directories, none group/world writable:

```sh
install -d -m 0700 /run/argus-receiver/trial-a/data /run/argus-receiver/trial-a/control
install -d -m 0700 /var/lib/argus-receiver/trial-a
```

Only `/run/argus-receiver/trial-a/data` is mounted, read-only, at
`/run/argus-audit`. It contains the data socket, never the binding, evidence,
control socket, Docker or SPIRE socket. The current pinned service runs as root;
for a changed UID, explicitly grant only traversal and socket group access,
keeping directory ownership and write restrictions. Review a UID/image change
through normal approval. The launch profile passes `ARGUS_AUDIT_MODE=on`,
`ARGUS_AUDIT_RUN_ID=trial-a` and `ARGUS_AUDIT_SOCKET=/run/argus-audit/receiver.sock`.

The server and business requests continue even when the collector is absent.
An absent collector means missing experiment evidence, not denied admission.
Create the original observation binding from the actual listener process and
protected deployment record:

```sh
PYTHONPATH=/path/to/adapters/OpenViking python3 -m receiver_audit.collector bind \
  --target /run/argus-workload/target.json --run-id trial-a \
  --output /var/lib/argus-receiver/trial-a/binding.json
```

The root-owned deployment record is read-only input. Binding checks PID/starttime,
container ID, UID and PID namespace. Each Unix datagram carries kernel
`SCM_CREDENTIALS`; the collector resolves the actual process through `/proc`.
Client headers only correlate probes. Malformed correlation tags never reject
business requests: the wrapper assigns an uncorrelated identifier instead.

A process restart in an already observed container is attributed to its actual
new PID/starttime. For a new container, add its protected deployment/process
record before the replacement traffic window:

```sh
PYTHONPATH=/opt/argus-audit python3 -m receiver_audit.collector add-target \
  --control /run/argus-receiver/trial-a/control/collector.sock \
  --target /var/lib/argus-receiver/trial-a/replacement-target.json --run-id trial-a
```

This records observation membership only. It neither runs Argus registration nor
grants admission. The record may come from the protected experiment launch and
process mapping even when the replacement has no successful SPIRE registration.
A replacement that Argus should deny must still be observable. Unknown sources
leave unattributed evidence; the collector never stops their business requests.

Install this package at `/opt/argus-audit/receiver_audit`, and the supplied
`argus-receiver@.service` as an independent unit, then:

```sh
systemctl daemon-reload
systemctl start argus-receiver@trial-a.service
```

Alternatively, run the same collector under an independent supervisor:

```sh
PYTHONPATH=/opt/argus-audit python3 -m receiver_audit.collector serve \
  --socket /run/argus-receiver/trial-a/data/receiver.sock \
  --control /run/argus-receiver/trial-a/control/collector.sock \
  --binding /var/lib/argus-receiver/trial-a/binding.json \
  --output /var/lib/argus-receiver/trial-a/receiver.jsonl
```

The collector must run in the host PID namespace, outside the Helper, NGINX,
target container and every injected cgroup. It has no `PartOf`/`BindsTo` relation
to those units and no automatic restart. Protect the evidence directory and
record the collector unit/cgroup in the runtime manifest. Normal Argus units
are unchanged. After collector startup, run the ordinary admission/start/verify
flow and obtain three successful business probes in each TLS lane before faults.

## Nonblocking protocol and bounded evidence

The wrapper sends request entry, pending/read and request-end metadata through a
nonblocking Unix datagram socket. There are no data-plane ACKs, retries or disk
waits. It calls the original `receive()` and returns its identical message even
when the collector is absent. Kernel buffering is bounded; sequence gaps and
the source drop counter expose telemetry loss instead of blocking the app.

The collector buffers JSONL and schedules flush/fsync every 250 ms, plus normal
finalization. This is not a disk-completion SLA. Source watermarks also run every
250 ms. Contiguous watermarks with no sequence/drop anomaly establish `COMPLETE`
coverage intervals. Loss produces `UNKNOWN` for the affected interval; later
complete intervals remain useful. Disconnects and aborted requests do not
invalidate all later measurements. No request content is included in telemetry.

Source or collector crashes leave an unknown tail after the last complete
durable watermark. Finalization does not invent coverage for that tail. There is
no claim of lossless crash-time audit. Positive source-bound reads still prove
delivery when another interval is unknown. Business continuing during collector
failure is expected behavior.

End both probe lanes and allow active HTTP work to finish, then finalize while
the collector is still running and before restarting or replacing any target:

```sh
PYTHONPATH=/opt/argus-audit python3 -m receiver_audit.collector finalize \
  --control /run/argus-receiver/trial-a/control/collector.sock
```

The v2 stop record uses `coverage="intervals_only"`, not global `complete=true`.
An abrupt collector loss has no stop record: keep preceding durable evidence and
the uncovered tail UNKNOWN. Never fabricate a stop record or append to that file.
Restarted observation uses a new evidence epoch/file, without automatic replay.
Finalized sockets are removed; killed sockets need reconciliation before reuse.

For in-flight fault probes, query an actual positive-byte read:

```sh
PYTHONPATH=/opt/argus-audit python3 -m receiver_audit.collector status \
  --control /run/argus-receiver/trial-a/control/collector.sock \
  --request-id inflight-1
```

The result contains `first_read: null` or the original source-bound read and its
timestamps. Status flushes evidence before replying. The cache holds at most
4096 distinct request IDs; use it for the dedicated fault probe, not each load
request.

Pass `receiver.jsonl` to `remote_acceptance.py check --receiver ...`. Judge actual
`body_read` times, including requests begun before the fault. A positive
post-bound read establishes a violation even if the client saw a transport error.
Absence requires complete coverage and healthy baseline receipts. Missing or
truncated intervals remain UNKNOWN. The assessor retains its old v1 reader;
this collector emits v2. Unversioned receipts cannot establish zero delivery.

`mode=off` is the explicit audit-overhead control, using the same derived image
and app entry with no middleware. Measure it in an isolated approved launch and
label receiver acceptance NOT_RUN. Use identical `mode=on` configurations across
security experimental groups; report instrumentation latency separately.

## Verification status

`python3 -m unittest discover -s receiver_audit/tests -v` exercises real datagrams
and kernel PID binding, unchanged delivery without a collector, collector/sender
process loss, interval gaps/recovery, replacement observation and first-read
metadata. These are synthetic ASGI tests, not model acceptance. The derived
Docker build, real TDX launch and remote failure windows remain NOT_RUN.
