# Remote lifecycle and Node renewal observations

These opt-in tools collect real deployment observations. Local tests of the
tools are software tests, not TDX, production, or business acceptance results.
They do not change the workload appraisal policy or require TCB UpToDate.

## Business traffic and independent receiver evidence

`remote_acceptance.py probe` uses Python's TLS chain validation plus an exact
server SPIFFE URI SAN check. It sends two concurrent lanes to an explicitly
configured business URL:

- `existing`: one TLS connection established before the fault, with automatic
  reconnection prohibited. A server that closes this connection during the
  baseline cannot demonstrate the old-connection case; the result is UNKNOWN.
- `new`: a fresh TLS connection for each request. No redirect is followed.
- `inflight` (opt-in): `--inflight` sends one synthetic POST body in paced
  chunks on a third connection, starting before the fault. The two original
  lanes remain independent. The coordinator enables this by default and waits
  for the receiver's first positive-byte read before injecting the fault.

The probe records request IDs, timestamps, peer certificate serials, HTTP status,
and byte counts. It does not save request/response bodies, API keys, or private
keys. Use synthetic data only. A health endpoint establishes reachability; it
does not demonstrate private-context delivery or memory retrieval. Use a real
application operation and independent business receiver instrumentation for
those claims. POST is an explicit opt-in and may modify the chosen application.

Choose a unique run ID and fresh output files. Start the independent receiver
observer **before** the probe, and keep it alive after the probe completes.
For example, on the client host (replace paths and URL with the actual deployment):

```sh
python3 remote_acceptance.py probe \
  --url https://service.example:1943/APPROVED_TEST_ENDPOINT \
  --cert /secure/client/svid.pem --key /secure/client/key.pem \
  --bundle /secure/client/bundle.pem \
  --server-id spiffe://argus.local/service/openviking-cmem \
  --run-id freeze-001 --output freeze-001.trace.jsonl \
  --duration 40 --interval 0.25 --timeout 2 \
  --method POST --body-file synthetic-request.json --inflight \
  --api-key-env OPENVIKING_API_KEY
```

On the service host, wait until **each of the two baseline lanes has at least
three successful business requests** and the in-flight body has actually been
read by the application, then execute the explicitly selected fault. The
`fault_trial.py` coordinator checks this with `collector status --request-id`.
If normal proxy buffering prevents an ongoing application read, the scenario
is NOT_RUN; do not silently disable buffering or infer first-read from a send.

```sh
python3 remote_acceptance.py fault \
  --config /etc/argus-workload/environment.json \
  --event helper-freeze --execute-fault --hold-recovery \
  --run-id freeze-001 --output freeze-001.fault.jsonl
```

`helper-freeze` sends SIGSTOP to the Helper main process. `helper-crash` sends
SIGKILL. `target-exit` sends Docker kill to the exact currently checked container;
it does not create a replacement. The fault timestamp is recorded immediately
before the command, so command latency is included. Command failure is not
treated as successful fault injection. Keep the systemd/Docker journal and
Helper/NGINX process observations with the artifacts as independent evidence
that the requested fault actually persisted; a successful signal command alone
does not prove a frozen interval of any particular duration.

### Isolating shutdown from legitimate re-admission

Production Helper normally restarts after failure and can independently
re-attest the still-valid workload. A successful **new** TLS call after such
re-admission is legitimate recovery. This tool does not call it a security
failure merely because a certificate serial changed or a request succeeded.

`--hold-recovery` is an **experiment control**, not a production default. It
creates only
`/run/systemd/system/<helper-unit>.d/90-argus-acceptance-no-restart.conf` with
`Restart=no`, reloads systemd, and checks the effective setting before the
fault. Existing files are refused; other drop-ins are untouched. Each file has
a unique ownership token whose hash is saved in the fault record before the
mutation. For `target-exit`, the tool also requires that Docker automatic
restart is already disabled. No automatic admission, service restart, or
replacement may be performed during this isolated observation interval.

After the probe and receiver observer finish, explicitly remove this run's
control, including after interrupted/failed runs:

```sh
python3 remote_acceptance.py release \
  --config /etc/argus-workload/environment.json \
  --fault freeze-001.fault.jsonl
```

Release verifies the expected path, ownership token, and exact bytes before
deleting this one file and reloading systemd. It does not start any service.
The fault reader can recover the last complete checkpoint when interruption
leaves only the final JSON line unfinished; earlier corrupt lines and malformed
complete lines are rejected. Probe and receiver evidence remain strictly parsed.
If file contents differ, inspect the experiment manually; the tool will not
overwrite or remove them. If the Helper remains stopped/frozen, use the existing
deployment stop/re-register/start workflow when deliberately recovering.

Without `--hold-recovery`, the default fault command does not change restart
policy. New-connection traffic without an independent re-admission boundary is
UNKNOWN. Original pre-fault TLS traffic that continues beyond the chosen stop
bound remains a failed old-connection closure observation.

### Receiver contract and result meaning

The receiver journal is an **independent application-side observation**, not the
client trace, NGINX access log, PEM cleanup, or a manually inferred list of
requests. Use the implemented [OpenViking ASGI receiver adapter](../../../../adapters/OpenViking/receiver_audit/README.md)
and independent Unix collector. Version 2 binds protected deployment records to
kernel sender credentials and process mappings. The wrapper sends metadata via
nonblocking datagrams; there is no synchronous acknowledgement and collector
failure cannot prevent business delivery. The collector batches persistence.
Source watermarks, sequence and drop counters delimit COMPLETE/UNKNOWN intervals;
crash tails and unlocatable damage remain UNKNOWN. A pre-bound gap does not
invalidate later covered intervals. Positive, attributed post-bound reads remain
FAIL even when other coverage is incomplete. `receiver_stop.complete=false` is
normal for v2: it makes no blanket lossless-capture claim.

Complete legacy v1 journals remain readable for their original two-lane scope;
they cannot satisfy the new in-flight scenario. Hand-authored three-record
journals cannot establish absence. Do not manufacture coverage records.

Build the explicitly approved derived image, launch it through the normal TC API
profile with the fixed data-socket mount, and run the collector outside all
Helper/NGINX/business container fault scopes. Audit stops at the ASGI read
boundary: it does not prove model consumption or plaintext erasure. The image
build and remote receiver acceptance are separate from local IPC tests.

Copy the actual artifacts to one analysis host, preserving raw journals and
measured clock synchronization/uncertainty. Check a declared stop bound (the
example 10 seconds is an experiment parameter, not a proven SLA):

```sh
python3 remote_acceptance.py check \
  --trace freeze-001.trace.jsonl --fault freeze-001.fault.jsonl \
  --receiver freeze-001.receiver.jsonl \
  --bound-ms 10000 --clock-uncertainty-ms 20 \
  --max-gap-ms 3000 --output freeze-001.result.json
```

Each lane needs a healthy baseline, at least three post-bound attempts, and
bounded observation gaps. PASS requires both blocked post-bound traffic and
complete independent receiver coverage with all successful baseline request
IDs represented. Any recorded post-bound delivery fails the isolated test even
if the client timed out. A receiver file missing or incomplete yields NOT_RUN
or UNKNOWN rather than PASS. The receiver uses actual application read times,
including body chunks from requests started before the fault. It uses source and
fault monotonic timestamps on the same service host; cross-host observation ends
still account for measured clock uncertainty. `inflight_delivery` reports that
scenario separately. Already-running response streams, OS memory deletion,
storage access and already-delivered plaintext are separate claims.

Same-container successor processes can be observed through the protected
deployment and kernel cgroup mapping. A new container requires the collector's
explicit `add-target` control before its observations can be attributed. This is
experiment observation only and does not register, attest, or admit the successor.

For target replacement, first complete the isolated `target-exit` trace. Then
release the experiment control and use the existing deployment workflow to
stop, launch, and register the replacement. Before opening its ingress, record
the actual replacement identity:

```sh
python3 remote_acceptance.py replacement \
  --config /etc/argus-workload/environment.json \
  --run-id exit-001 --fault exit-001.fault.jsonl \
  --output exit-001.replacement.json
```

This checks that launch ID and container ID changed. It does **not** label the
replacement admitted. Keep pre-admission blocked probes, then the full
`workload.py verify` output/current EAR correlation and a separate successful
business/receiver trace after deliberately starting the new Helper. Recovery
acceptance requires that evidence; a new PID, serial, or HTTP response alone
does not establish fresh admission.

`verify-lifecycle.py` remains available for its narrower local cleanup check.
Its disruptive modes now require `--execute-fault`; cleanup success is recorded
as `cleanup_result: PASS`, while old/new TLS and receiver delivery remain
NOT_RUN. `--stop-timeout` is an observation budget, not a claimed security bound.

## Node initial enrollment versus ordinary Agent SVID renewal

`node_attestation_observe.py` is read-only: it runs SPIRE Server `agent list`,
reads one dedicated Agent Prometheus endpoint, and writes public observations.
It never deletes Agent state, expires a credential, changes TTL, or triggers
enrollment/rotation. Do not use a workload SVID serial as an Agent SVID serial.

Inspect the actual Agent metrics endpoint and identify exact exported names
for the Node plugin's `argus_nodeattestor/attempts`,
`argus_nodeattestor/evidence_bytes` **sample count**, and that Agent process's
start-time gauge. Prefixes/exporter conventions vary, so names are explicit
arguments; do not substitute a workload Quote counter or a metrics proxy's
process-start time. The existing Node plugin emits best-effort metrics; absent
telemetry stays unknown, never zero. The endpoint must be dedicated to the same
Agent ID selected from the Server API. Save deployment/unit-to-endpoint mapping
and raw metrics/journals alongside the report.

```sh
python3 node_attestation_observe.py snapshot \
  --spire-server /opt/spire-1.15.3/bin/spire-server \
  --server-socket /run/spire/server/private/api.sock \
  --agent-id spiffe://argus.local/spire/agent/argus_tdx/openviking-node \
  --metrics-url http://AGENT_METRICS_HOST:PORT/metrics \
  --attempt-metric ACTUAL_NODE_ATTEMPT_METRIC \
  --quote-count-metric ACTUAL_NODE_EVIDENCE_SAMPLE_COUNT \
  --process-start-metric ACTUAL_AGENT_PROCESS_START_SECONDS \
  --output before.json
```

For initial enrollment, take `before.json` while the chosen new deployment's
Agent is absent from the Server's public registry. The metrics endpoint may be
unavailable then; this is saved as unavailable. Start that already-authorized
new Agent using its deployment procedure, then capture `after.json` with the
same command/source selection. The check requires absent-to-registered identity,
an unbanned `argus_tdx` public record with a live SVID, and either:

- a new dedicated Agent process whose start time falls strictly inside the
  observation window accounting for clock uncertainty, with positive observed
  Node attempts/Quote sample counts in that process; or
- continuous same-process metrics with actual positive counter increments.

Missing old metrics are never used as numeric zero. This demonstrates enrollment
in the recorded window, not a historical assertion that this identity has never
existed. If source mapping, process time, or Node counters cannot be observed,
the check returns UNKNOWN; retain the records and add suitable telemetry before
claiming this experiment passed. No automatic credential-loss simulation occurs.

For ordinary renewal, take `before.json` after successful enrollment and while
the Agent SVID is valid; wait for normal renewal, then take `after.json`. The
check requires a changed public Agent SVID serial, the same metrics process,
and unchanged Node attempts and Node Quote sample counters. A process restart,
counter reset, missing field, or unchanged serial is UNKNOWN. New Node
attestation during this isolated renewal interval fails the requested reuse
property. Do not inject unrelated re-attestation faults into that interval.

```sh
python3 node_attestation_observe.py check \
  --before before.json --after after.json --mode renewal \
  --clock-uncertainty-ms 20 --output renewal.result.json
```

Use `--mode enrollment` for the separate initial-enrollment check. Both check
commands exit 0 for PASS, 1 for FAIL, and 2 for UNKNOWN. These are observations
under the configured policy, not a claim of continuous TDX platform appraisal.
