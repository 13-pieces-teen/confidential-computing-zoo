# E3: read-only lifecycle observation plus continuous memory queries

This recipe uses existing real public SPIRE Agent records, a dedicated Agent
metrics endpoint, workload readiness/certificate snapshots, and mTLS memory-query
traces. It does not renew credentials, restart Agent, delete its state, or create
containers. No new server recovery protocol is introduced.

## Inputs and timing

1. Copy `e3-agent-renewal.example.json` and replace the paths, Agent identity and
   metric names with the actual deployment. Inspect the selected Agent's real
   metrics endpoint first. The Quote source must be the Node plugin's evidence
   count, not an aggregate Workload or HTTP metric. A missing metric stays UNKNOWN.
   If SPIRE Server and the selected Agent are on different hosts, use the
   deployment's existing read-only metrics access or an explicit operator tunnel.
   Run the Node observer on the host with the SPIRE Server API socket. The example
   intentionally has no `workload_config`: if OpenViking is on the other TDVM,
   its local workload snapshot cannot be executed through that Server socket.
2. Keep the approved `CanReattest=false`. For ordinary renewal, retain valid Agent
   credentials and state. Choose an observation duration that spans the configured
   normal Agent renewal; a window with no new serial is UNKNOWN, not a failure of
   the renewal mechanism. For first enrollment, use a separate genuinely unused
   enrollment identity and separately provisioned deployment; never erase the
   working Agent's state to manufacture this case.
3. Prepare a frozen `load_fleet.py` configuration with `connection_mode: "reuse"`,
   `workload_kind: "memory_query"`, one or more independent clients and an actual
   `/api/v1/search/find` URL. Each protected body file contains a fixed nonempty
   query against preloaded private memory. This measures memory API continuity,
   not complete Gateway reasoning. Keep full Agent tasks as a separate E4 run.
4. Both hosts must record a measured clock uncertainty. Keep the API measurement
   active before the server's first snapshot and after its last snapshot. For a
   600 s observation, a 900 s client measurement with 30 s warmup provides room
   for setup; confirm actual timestamps afterwards. Probe at least 2 requests/s
   for a 2000 ms maximum admitted sample gap. The bound controls evidence coverage,
   not a new availability guarantee.

## Two-machine commands

From the repository root, start the client first (terminal on client TDVM):

```sh
python3 cczoo/agent-cc/experiments/argus/load_fleet.py \
  --config /etc/argus/e3-memory-load.json \
  --output /var/lib/argus/experiments/e3-agent-renewal-01-client \
  --run-id e3-agent-renewal-01 --clients 1
```

While this runs, after its warmup, start the read-only Node observer on the host
with the SPIRE Server API socket (this may be the client host in the two-TDVM
topology; use another terminal):

```sh
python3 cczoo/agent-cc/experiments/argus/lifecycle_trial.py observe \
  --config /etc/argus/e3-agent-renewal.json \
  --output /var/lib/argus/experiments/e3-agent-renewal-01-server
```

`E3_OBSERVER_READY` means the before snapshots have been attempted. Let normal
renewal occur. No action is automatically issued by this command. If interrupted,
retain the incomplete directory and start a separately named observation; no
mutation is replayed. Copy the completed client and server evidence directories
onto one analysis host using the existing operator transfer process, then run:

```sh
python3 cczoo/agent-cc/experiments/argus/lifecycle_trial.py collect \
  --observation ./e3-agent-renewal-01-server \
  --load-result ./e3-agent-renewal-01-client/load-result.json \
  --clock-uncertainty-ms 100 --max-probe-gap-ms 2000 \
  --output ./e3-agent-renewal-01-result.json
```

The example `100` must be replaced by measured host-clock uncertainty. Collection
checks snapshot and request hashes, run ID, actual TLS target identity and common
time coverage. Never copy a PASS from a previous trial into this evidence.

## Workload SVID rotation

Copy `e3-workload-rotation.example.json` and run its observer on the OpenViking
host, using the same target and the local installed workload configuration.
Use a distinct run ID and continuous client probe for this separate case.
Allow normal Helper credential updates. The collected workload verdict reuses
`lifecycle_evidence.rotation`: same target and Helper, valid changed SVID. Node
counter observations are NOT_RUN in this workload-only recipe. If the deployment
provides both local snapshot interfaces on one observation host, adding `node`
settings captures their differences separately even if Agent serial did not change.
Do not present counters from a separately timed trial as this rotation's counter.
There is currently no dedicated captured Workload Quote counter: its count is
explicitly UNKNOWN. SVID changes, logs without matches, or Node counts cannot be
used to infer a Workload Quote count.

## Known launch query recovery

On the OpenViking host, set `case: "resume-launch"` only after an interrupted query left a known,
unfinished `launch_id`. Start the independent Docker create observer first:

```sh
python3 cczoo/agent-cc/experiments/argus/lifecycle_evidence.py observe-creates \
  --workload-id ACTUAL_WORKLOAD_ID --duration 900 \
  --output /var/lib/argus/experiments/e3-resume-creates.jsonl
```

Start `lifecycle_trial.py observe` in another terminal. After
`E3_OBSERVER_READY`, invoke the existing recovery command exactly once:

```sh
python3 /opt/argus/scripts/workload.py resume-launch \
  --config /etc/argus/workload-experiment.json --launch-id ACTUAL_KNOWN_LAUNCH_ID \
  > /var/lib/argus/experiments/e3-resume-result.json
```

Use the actual installed `workload.py` path. The known operation resumes its
query/commit behavior; a submission-unknown state is not permission to create a
new container. Complete both observers, then supply the two additional files to
`collect`: `--resume-result .../e3-resume-result.json --creates .../e3-resume-creates.jsonl`.
The existing resume checker requires create-stream coverage around both snapshots
before claiming no second Docker create. An absent stream remains UNKNOWN.

## Reading results

- `result` is the primary Node-renewal, workload-rotation or resume verdict.
  Business continuity and Quote measurement coverage are independent fields.
- `observed_serial_transitions_min` is a lower bound from two snapshots, not an
  exact certificate issuance count.
- `node_quote_samples` is a real counter delta or a proved new-process count.
  `workload_quote_samples` remains UNKNOWN until a real dedicated source is added.
- `business_continuity` reports each client, known/unknown/timeout outcomes,
  finite-cadence coverage and sampled interruption episodes. A business FAIL is
  retained even if the primary lifecycle operation later succeeds.
- No failed scheduled probes in a covered interval does not prove zero downtime
  between probes. An interrupted/missing trace, target mismatch, or clock/cadence
  coverage gap cannot establish continuity.
- Real TDX, model task scores and cross-machine performance remain NOT_RUN until
  these commands are executed remotely with their actual evidence.
