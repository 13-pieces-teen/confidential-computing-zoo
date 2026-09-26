# E4 fixed multi-client fault and explicit recovery recipe

Run `fleet_fault.py` on the client TDVM containing the independent Gateways. It
reuses `fleet_business.py` and each Gateway's own configured mTLS transport and
ordinary OpenViking key. The setup must already have completed private-memory
acceptance for every selected instance, with the matching `result.json`, `marker.txt`
and `fact.txt`. A preceding PASS is a prerequisite; it is not the fault result.

Copy `fleet-fault.example.json`, replace the paths and choose the exact deployed
instance name. Three clients are the paper default; at least two are required.
Each round concurrently performs a read-only memory search from every Gateway.
No fact or key is written into this tool's output.

## Local Gateway stop

From the repository root:

```sh
python3 cczoo/agent-cc/experiments/argus/fleet_fault.py run \
  --config /etc/argus/e4-client-stop.json \
  --output /var/lib/argus/experiments/e4-client-a-stop-01 --execute-fault
```

`run --execute-fault` is an explicitly disruptive experiment command. After three
successful memory reads from every Gateway, the tool checks the selected
container's instance label, image and isolated mounts, then executes
`docker stop --time 2 EXACT_CONTAINER_ID`. It does not stop the shared SPIRE Agent,
the other Gateways or their credential publishers. Searches continue across the
stop; at least the configured number of complete per-instance rounds must start
after fault completion plus `settle_seconds`.

The settle period is an experiment observation choice, not a proven closure
deadline. The result describes unaffected-client availability. A stopped
Gateway's unknown transport result does not establish zero server receipt.

Recover only the selected Gateway through the existing deployment procedure:

```sh
docker start EXACT_STOPPED_CONTAINER_ID
docker top EXACT_STOPPED_CONTAINER_ID -eo pid,comm,args
python3 cczoo/agent-cc/adapters/OpenClaw/spiffe_client/deploy.py guest-register \
  --config /etc/argus/fleet.json --instance client-a --pid ACTUAL_CURRENT_GATEWAY_PID
python3 cczoo/agent-cc/adapters/OpenClaw/spiffe_client/deploy.py guest-status \
  --config /etc/argus/fleet.json --instance client-a
```

Use the actual Gateway process PID from the deployment, not an old PID or Docker
init PID. Preserve its business data and do not rerun the original fact-write
acceptance merely to manufacture recovery. Then run:

```sh
python3 cczoo/agent-cc/experiments/argus/fleet_fault.py observe-recovery \
  --config /etc/argus/e4-client-stop.json \
  --output /var/lib/argus/experiments/e4-client-a-stop-01
```

This command performs three additional read rounds and one fresh-session question
per Gateway. The question contains only the project marker, never the expected
answer. PASS requires the answer and linked actual recall-to-context-injection
audit. Recovery does not erase earlier local-fault failures.

## Shared service fault

Use a separate run ID and output directory. Change `event` to
`shared-service-fault`, remove `instance`, and add `server_fault_config` pointing
to a local JSON file with the existing `fault_trial.py` SSH/fault settings:

```json
{
  "run_id": "e4-shared-helper-freeze-01",
  "event": "helper-freeze",
  "ssh_host": "argus-service-host",
  "server_python": "python3",
  "server_deployment": "/etc/argus-experiments/paper01/full_argus/environment.json",
  "server_remote_acceptance": "/opt/argus-experiments/paper01/full_argus/payload/scripts/remote_acceptance.py",
  "fault_file": "/srv/argus-experiments/paper01/e4-shared-helper-freeze-01-fault.jsonl"
}
```

The SSH alias must already exist with a pinned known host key. The remote account
must have the permissions required by the existing fault tool. The file path is
fresh and exclusive to this run. Other supported events are `helper-crash`,
`target-exit`, `config-change` and `same-container-restart`. The last two also need
`server_fault_fixture`; configuration mutation needs the existing original and
replacement file/digest fixture fields. Reuse the E2 configuration fixture
recipe rather than introducing arbitrary remote shell snippets.

Run the same `run --execute-fault` command with this configuration. It uses the
existing fault tool with `--hold-recovery`. The downloaded final receipt must be
complete, match the event and run, show observed mutation/success and verify the
recovery hold. Its saved hash is checked again during collection. A truncated,
unknown or subsequently changed receipt cannot verify the fault.

The operator restores the approved configuration when applicable, releases only
this run's recovery hold using the existing `remote_acceptance.py release`, and
performs the deployment's explicit stop/registration/start/verify sequence.
`release` alone does not restart services. This tool neither starts services nor
creates or readmits a replacement. After the operator verifies recovery, invoke
`observe-recovery` as above with the same unchanged fault configuration and output.

## Interruption and results

```sh
python3 cczoo/agent-cc/experiments/argus/fleet_fault.py collect \
  --config /etc/argus/e4-client-stop.json \
  --output /var/lib/argus/experiments/e4-client-a-stop-01
```

`collect` only recomputes the saved evidence. Never issue `run` again against an
existing trial. A `fault_submission_unknown` phase requires reconciliation; the
tool refuses automatic fault replay and recovery questions. A submitted question
with an unknown result is retained and is not submitted again on a later
`observe-recovery`. Interrupted read phases remain incomplete; collection does
not fill the missing trace with successful later reads.

- `coverage` requires a sealed trace, every configured client, complete matching
  round sequences and at least three observations per client in each required
  interval. Missing clients, truncated logs and insufficient post-settle samples
  stay UNKNOWN.
- For a local stop, PASS concerns the verified selected-container stop and
  unaffected clients' continued memory availability. Positive responses from the
  stopped client after settling, or known errors in unaffected clients, fail.
- For a shared fault, `fault_availability` distinguishes known unavailable
  responses, persistent availability and UNKNOWN. All-unknown probes remain
  UNKNOWN even when fresh-session recovery later succeeds.
- A native HTTPS request with `ECONNREFUSED`/`ECONNRESET`, or an actual response
  followed by body reset, is a known unavailable business request. Its request ID,
  phase and error category are retained. Configuration, credentials, Docker,
  timeout and TLS validation failures stay UNKNOWN. Neither category proves
  that zero bytes were received. Rebuild the customized plugin to enable this
  additional observation; an older plugin falls back to UNKNOWN.
- `recovery_result` and each fresh question are separate from fault results. A
  known wrong answer stays FAIL even if recall audit is unavailable.
- `delivery_compliance` and `independent_readmission` are NOT_RUN here. Use E2
  receiver evidence and E1/E3 admission evidence for those claims. Network or
  Docker errors are not evidence of zero received data.
- CLI exits 0 for PASS and 2 for all other current result states. Preserve the
  JSON's distinct FAIL/UNKNOWN/NOT_RUN fields when collecting paper results.

This is a fixed experiment recipe, not an automatic multi-Agent recovery or
orchestration service. Real remote results remain NOT_RUN until executed.
