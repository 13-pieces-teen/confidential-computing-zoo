# E3: read-only lifecycle observation plus continuous memory queries

This recipe uses existing real public SPIRE Agent records, a dedicated Agent
metrics endpoint, Provider generation counters, workload readiness/certificate snapshots, and mTLS memory-query
traces. The observers do not renew credentials, restart Agent, delete its state,
or create containers. The optional timing wrapper below executes one explicit
operator command. No new server recovery protocol is introduced.

## Inputs and timing

1. Copy `e3-agent-renewal.example.json` and replace the paths, Agent identity and
   metric names with the actual deployment. Inspect the selected Agent's real
   metrics endpoint first. The Node plugin metric counts received evidence samples;
   actual Quote generation is counted separately at the Provider. Do not replace
   either with an aggregate Workload or HTTP metric. A missing source stays UNKNOWN.
   If SPIRE Server and the selected Agent are on different hosts, use the
   deployment's existing read-only metrics access or an explicit operator tunnel.
   Run the Node observer on the host with the SPIRE Server API socket. The example
   intentionally has no `workload_config`: if OpenViking is on the other TDVM,
   its local workload snapshot cannot be executed through that Server socket.
   The Node example also omits `provider_socket`: an IP2 UDS path cannot be read
   from IP1. Import the explicit IP2 snapshots below when the Provider is remote.
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

While this runs, after its warmup, first capture the Provider on IP2. Use the
same `run_id` and verify its returned `agent_id` is the Node observed on IP1:

```sh
python3 cczoo/agent-cc/experiments/argus/lifecycle_evidence.py quote-snapshot \
  --provider-socket /run/argus/evidence-provider.sock --run-id e3-agent-renewal-01 \
  --output /var/lib/argus/experiments/e3-provider-before.json
```

Then start the read-only Node observer on the host
with the SPIRE Server API socket (this may be the client host in the two-TDVM
topology; use another terminal):

```sh
python3 cczoo/agent-cc/experiments/argus/lifecycle_trial.py observe \
  --config /etc/argus/e3-agent-renewal.json \
  --output /var/lib/argus/experiments/e3-agent-renewal-01-server
```

`E3_OBSERVER_READY` means the before snapshots have been attempted. Let normal
renewal occur. No action is automatically issued by this command. After that
observer finishes, capture IP2 again with the same command and run ID, changing
the output to `e3-provider-after.json`. Leave a margin exceeding the measured
inter-host clock uncertainty on both sides. The two Provider snapshots must cover
the entire Node observer, including its first and last snapshots. Keep unrelated
admissions out of this measured window: Provider counters cover the process's
whole Node/Workload workload, not a single target request. If interrupted,
retain the incomplete directory and start a separately named observation; no
mutation is replayed. Copy the completed client and server evidence directories
onto one analysis host using the existing operator transfer process, then run:

```sh
python3 cczoo/agent-cc/experiments/argus/lifecycle_trial.py collect \
  --observation ./e3-agent-renewal-01-server \
  --load-result ./e3-agent-renewal-01-client/load-result.json \
  --provider-before ./e3-provider-before.json --provider-after ./e3-provider-after.json \
  --clock-uncertainty-ms 100 --max-probe-gap-ms 2000 \
  --output ./e3-agent-renewal-01-result.json
```

The example `100` must be replaced by measured host-clock uncertainty. Collection
checks snapshot and request hashes, run ID, actual TLS target identity and common
time coverage. Never copy a PASS from a previous trial into this evidence.
Imported Provider snapshots must have the same run ID and expected Agent, an
unchanged Provider startup ID, and a window covering the Node trial plus clock
uncertainty. Invalid imports leave `quote_generated` UNKNOWN without replacing
the independently observed renewal verdict. Copy both original JSON files with
the results; their hashes are included in the output. Do not also supply local
Provider snapshots for that same trial.

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
The workload example's local `provider_socket` captures actual Node and Workload
generation counts before/after. Replace its path with IP2's real UDS path.
`workload_quote_samples` still denotes an unavailable received-sample counter;
use `quote_generated.workload` for actual generation. SVID changes or unmatched
logs must not be substituted for generation counts.

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
  `workload_quote_samples` remains UNKNOWN because no received-sample source is
  attached. `quote_generated.node/workload` reports actual attempted/generated/
  failed counts, including discarded and retried generations. These are distinct
  measurements. A Provider restart invalidates the delta.
- `business_continuity` reports each client, known/unknown/timeout outcomes,
  finite-cadence coverage and sampled interruption episodes. A business FAIL is
  retained even if the primary lifecycle operation later succeeds.
- No failed scheduled probes in a covered interval does not prove zero downtime
  between probes. An interrupted/missing trace, target mismatch, or clock/cadence
  coverage gap cannot establish continuity.
- Real TDX, model task scores and cross-machine performance remain NOT_RUN until
  these commands are executed remotely with their actual evidence.

## Small control-plane cost measurements

The Provider now adds cumulative `generation_elapsed_ns` at the actual
`generate_quote` boundary, measured with a monotonic clock. `quote_generated.
generation_timing` reports the observed delta, completed call count and mean
milliseconds. Failed and later-discarded generations are included. Old Providers
can still yield valid counts while timing remains UNKNOWN; zero generation has
no mean latency. These counters do not provide per-call p95/p99.

The WorkloadAttestor logs `trustee_request_elapsed_ns` and
`response_body_complete` for each actual Trustee call. With the existing optional
admission export enabled, `capture.json.trustee_request` also records these
fields as `elapsed_ns` and `response_body_complete`. This is client-observed RTT
through the response read: it includes connection/network and server work,
excludes local EAR verification, and is not pure Trustee verification CPU time.
Failed/partial requests must be reported separately from complete responses.

For one explicitly planned initial admission or re-admission, wrap the actual
operator command with `time-ready-command`. Store the argv array in a protected
JSON file, with no secret values in argv, for example:

```json
["python3", "/opt/argus/scripts/workload.py", "start", "--config", "/etc/argus/workload-experiment.json"]
```

```sh
python3 cczoo/agent-cc/experiments/argus/lifecycle_evidence.py time-ready-command \
  --config /etc/argus/workload-experiment.json --argv-file /secure/e3-start-argv.json \
  --run-id e3-readmission-01 --timeout-seconds 180 \
  --output /var/lib/argus/experiments/e3-readmission-timing.json
```

Use the actual approved command for that deployment, after its normal launch
and registration prerequisites; this example does not replace those steps.
Unlike the read-only observer, this subcommand executes the supplied command
once. It never retries or resumes an unknown command result. An existing output
is refused so a command is not accidentally replayed. It records command elapsed
time and time from command start to subsequent valid readiness. Readiness is
polled after the command finishes, so the latter is a conservative observation
cost, including polling/checks, not an exact issuance time or pure attestation
duration. Existing readiness with the same Helper does not count as newly ready.
Normal Agent renewal continues to use the read-only recipe above.
