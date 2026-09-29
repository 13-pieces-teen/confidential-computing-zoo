# Continuous private-context tasks (E4)

> 历史可选负载：revision 1373 的默认框架实验使用 E1/E2/E3/E5 和 LoCoMo E4。本文累计金额/路由/依赖任务保留兼容，不作为默认论文主线。见 [PAPER-ALIGNMENT-1373.md](PAPER-ALIGNMENT-1373.md)。

This is the direct, host-local task runner. The paired suite is documented in
[CONTINUOUS.md](CONTINUOUS.md). Code and test cases are provided; real Agent,
TDX, model extraction, fault timing and cross-host acceptance are `NOT_RUN` until
the remote checks have been executed. No local unit or integration tests were
run for this addition at the user's request; local validation is syntax/build only.

## Actual application path

Each fresh OpenClaw session calls the pinned plugin's `memory_recall` and
`memory_store`. The latter uses the existing session-message and commit APIs.
The controller seeds initial rules before measurement and reads evidence afterward;
it never writes the task's answer or substitutes a direct API write for the Agent.

Install a freshly built overlay using `adapters/OpenClaw/spiffe_client/build_plugin.py`.
Keep the upstream release pinned; the `argus.3` version label alone does not identify
the new build. Save its whole-package SHA and the actual installed image SHA.
Apply [config/continuous-tools.fragment.json](config/continuous-tools.fragment.json)
to each dedicated Gateway **before admission**, preserving its own existing
`apiKey`, account/user, origin and SPIFFE configuration. This enables only
`memory_recall`/`memory_store`, disables auto recall/capture and resource recall,
and keeps user memory scope. Do not apply the LoCoMo read-only fragment to this run.

The new passive tool wrapper records tool start/finish, commit IDs and transport
association in Gateway stderr/Docker logs. It does not decide authorization, alter
tool arguments, wait for receiver ACKs, or retry writes. Each HTTP request has
`x-argus-request-id`; experimental tool calls additionally carry run/client/task/
attempt/tool-call IDs. These headers are observation metadata, never identities.

## Configuration

Use one configuration per run and private business scope. `bindings` can have one
client for the first closed loop, or three clients after that succeeds. Example:

```json
{
  "schema": "argus.continuous.v1",
  "run_id": "e4-pilot-alice",
  "block_id": "pilot-01",
  "group": "full_argus",
  "condition": "no_fault",
  "fault_kind": "helper-freeze",
  "fault_scope": "shared_service",
  "mode": "pilot",
  "structure_seed": 1,
  "model_settings": {"model": "RECORD_THE_ACTUAL_PINNED_PROVIDER_AND_MODEL"},
  "schedule": {"release_interval_s": 60, "deadline_s": 120, "stop_budget_s": 10, "frozen": false},
  "qa_timeout_seconds": 120,
  "max_concurrency": 1,
  "queue_limit": 1,
  "task_retries": 0,
  "bindings": [{
    "client_id": "alice",
    "container": "REPLACE_GATEWAY_CONTAINER",
    "docker_user": "10001:10001",
    "config_path": "/home/node/.openclaw/openclaw.json",
    "agent_id": "main",
    "account_id": "argus-eval",
    "user_id": "alice-pilot-01",
    "client_spiffe_id": "spiffe://argus.local/agent/alice",
    "server_spiffe_id": "spiffe://argus.local/service/openviking-cmem"
  }],
  "controls": {
    "fault": {"at_s": 360, "argv": [], "timeout_s": 120},
    "recovery": {"at_s": 720, "argv": [], "timeout_s": 120}
  }
}
```

`run_id`, `block_id`, `group` are supplied by the suite environment when present.
`fault_kind` names the paired mechanism (for example `helper-freeze`); both fault
and matched no-fault configurations retain the same value. It is required for
formal runs and appears in the public manifest/result for separate analysis.
The supported fault commands affect the shared service, so `fault_scope` is
`shared_service` (also the backward-compatible default). All dependent clients
belong to that fault domain; their changes are not uninjected-client collateral
loss. Local continuous-task injection remains unimplemented; the existing
`fleet_fault.py` availability experiment is a separate workload.
`structure_seed` fixes task difficulty and the dependency graph. Optional
`secret_seed` fixes synthetic secret values; otherwise `prepare` generates fresh
random secrets once and preserves them in `private-fixture.json`. Different arms
and fault/no-fault runs need distinct private users and secrets. Never copy the
same private fixture to another treatment. `experiment_protocol_digest` and
`receiver_context_file` may be supplied by suite orchestration; this runner does
not use them to change application behavior.

`model_settings` supports only `model`. Preflight checks that name against the
selected agent's configured primary model, and the result checks the observed
model/provider returned by Agent. The full admitted agents configuration is
represented by `model_config_sha256`; the runner does not reconfigure it. Sampling
parameters such as temperature are not verified by this adapter and must not be
described as checked merely because a manifest declared them.

The initial timing is a pilot value, not a measured capacity or stopping guarantee.
After two pilots freeze timing/model settings using `mode: "formal"` and
`schedule.frozen: true`. Fault and recovery offsets are respectively `6 × interval`
and `12 × interval`. Formal fault runs require both actual control commands.
No-fault controls keep the same offsets with empty argv. Control commands are
argv arrays executed without a shell, using the existing deployment/fault tools.
`{run_id}`, `{operation_id}`, `{output}` are replaced in individual argv elements;
the first two IDs are also in the child environment. Use absolute script/config
paths. Control subprocess output is not copied into public artifacts; the existing
fault/deployment tools must keep their native receipts at the configured paths.

## Run a single-client no-fault pilot

From `cczoo/agent-cc/experiments/argus` on IP1:

```bash
python3 continuous.py prepare --config /protected/e4-pilot.json --output /protected/e4-pilot
python3 continuous.py preflight --config /protected/e4-pilot.json --output /protected/e4-pilot
python3 continuous.py run --config /protected/e4-pilot.json --output /protected/e4-pilot
python3 continuous.py analyze --config /protected/e4-pilot.json --output /protected/e4-pilot
```

`run` prepares automatically if needed. Initial A rules are written once, archived,
nonempty extraction is checked and actual user memory is retrieved before the
window starts. An unconfirmed initial write is queried, never resubmitted. Resolve
an ambiguous initialization explicitly or start a separately identified experiment;
do not populate the old run with hand-written correct answers.

There are 18 planned tasks per client: six in each normal/pause/recovery phase.
Each phase has three independent initial tasks followed by three successors.
Successors receive only predecessor fact IDs, not the preceding amount, transaction
reference, route or historical rule. Each new phase uses fresh chains so a failed
pause-phase write does not make every recovery task impossible by construction.

Releases and controls use the original monotonic schedule regardless of answers.
One Agent invocation and one queued task are permitted per client. A full queue or
expired deadline remains in the planned denominator. The controller never retries
an Agent task; an observed model-issued repeated write is reported separately.
The Agent CLI's configured timeout is recorded, but timeout alone is not a proof
that server-side extraction stopped.

If execution is interrupted:

```bash
python3 continuous.py resume --config /protected/e4-pilot.json --output /protected/e4-pilot
```

Resume reads Gateway audit and known session/task/archive state. It does **not**
release tasks, repeat an Agent invocation, redo controls, or move the original
time window. An interrupted window stays incomplete, with all planned tasks
visible. A fresh independent trial has a new run ID and fresh secrets.

Resume also queries an already submitted initial A seed using its known task ID;
confirmation requires the original rule text in an archive, nonempty extraction,
and retrieval of the actual rule. It never seeds a missing project. Even when that
query completes initialization, resume does not begin the measurement window and
the run remains `NOT_RUN`. In direct use, a deliberate `run` can finish a still
unstarted initialization; a suite operation recorded as unknown is reconciled via
the suite and is not bypassed by silently invoking `run` again. The simplest
formal recovery is a separately identified new run after the issue is resolved.

## Evidence and interpretation

- `manifest.json`: complete public task plan, fact lengths/hashes, protocol hash
  and a structure hash. Model answers and secret rule values are not in it.
- `private-fixture.json`, `state.json`: protected inputs, independent expected
  decisions and raw Agent/application observations. Keep these private.
- `events.jsonl`: task/control timeline and bounded outcome metadata.
- `operation-id.json`: existing operation identity for suite reconciliation.
- `result.json`: every planned task with offered/attempted/committed/recalled,
  request IDs, time fields, deadline and task verdict. `PASS` at the **run** level
  means its intended observation window and controls completed; it does not mean
  all tasks passed or that reception complied. Those are separate result axes.

The fact frame includes the actual project, chain, random transaction reference
and amount. It has no JSON-escaped characters, and its checksum covers the full
prefix. A complete matching frame is required; a header, marker, HTTP 200, or body
byte count alone cannot establish complete fact receipt. The independent receiver
provides the receipt axis; until collected, it is `UNKNOWN`.

Task scoring requires the exact expected decision within deadline, real recall and
store calls, a complete unchanged fact in those calls, nonempty completed extraction
and the archived fact/decision. It does not trust the upstream tool's `stored`
wording, which can accompany zero extracted memories. A known task failure is not
hidden by an unrelated receipt observation gap. Parent-task commit uncertainty is
retained and is not silently replaced by a new write.

## Tests to run remotely

```bash
python3 -m pytest experiments/argus/tests/test_continuous.py -q
node --test adapters/OpenClaw/spiffe_client/test/task-audit.test.mjs
node --test adapters/OpenClaw/spiffe_client/test/transport.test.mjs
```

Run these from `cczoo/agent-cc` with the project's pinned Python/Node dependencies.
Then run the existing patched-upstream package tests with `ARGUS_TEST_PLUGIN_DIR`
pointing at a newly built and unpacked overlay. Unit fixture results are not real
Agent or receiver evidence. Remote acceptance must first establish one genuine
task's full input→tool→transport→application-read→archive→successor chain before
expanding to fault trials and three clients.
