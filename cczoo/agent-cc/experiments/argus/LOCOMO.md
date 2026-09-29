# LoCoMo-derived application workload

This workload measures real Agent integration, application overhead and fault/recovery
impact through the Argus entry. Memory answer quality is an auxiliary functional
check, not an Argus contribution. It does not establish a security result from an answer and does
not implement the official LoCoMo evaluation protocol. Real TDX, model quality,
and remote execution remain `NOT_RUN` until artifacts are collected remotely.

## Pinned interfaces and experimental protocol

The adapter requires the existing OpenViking plugin `2026.6.18`, Argus revision
`argus.3`. It imports its existing native SPIFFE transport inside the selected
Gateway; it does not contact localhost OpenViking or use a root key. API paths and
message/commit formats follow the locked OpenViking source
`07113f81e0edaebaacdd23ab138087b06fe871ab` and its OpenClaw client. No plugin
version or extraction/ranking algorithm is changed.

Create dedicated experiment users and Gateway configurations before registration.
Merge `config/locomo-readonly.fragment.json` into those configurations, preserving
their Origin, exact SPIFFE IDs, ordinary API key, account/user and model settings.
Start/admit the Gateway with that configuration; do not modify a running admitted
configuration to switch modes. The normal business Gateway keeps its existing
`autoCapture=true` behavior.

The evaluation Gateway uses `autoCapture=false`, `autoRecall=true` and
`tools.deny=["*"]`. Historical sessions are imported explicitly; QA is answered
by the actual OpenClaw ContextEngine with automatic memory retrieval/injection.
Model tool calls cannot write the evaluated questions/answers back to memory.
This evaluates memory-assisted QA, not unconstrained model tool orchestration.
No answer or evidence label from the fixture is sent to the Gateway. Reference
answers are used only by the local grader. Each question has a new session key.

Each conversation in one batch has a distinct ordinary business user and a
dedicated Gateway. Dataset speakers are transcript labels, not security identities.
Use initially empty, dedicated users for every replicate/arm; the tool does not
erase or reset existing application data. To cover all ten public conversations
without ten simultaneous Gateways, run predeclared disjoint batches with new
users/configurations and retain each batch's original sample IDs. Freeze the
sample list, model, memory configuration and budgets identically across arms.

## Commands on the client Guest

Paths below are relative to `cczoo/agent-cc/experiments/argus`:

```sh
umask 077
python3 locomo.py --source /protected/locomo10.json \
  --output private/locomo-derived.json --samples conv-26 \
  --categories 1,2,3,4 --seed 0 --limit 100
# Copy examples/locomo.example.json and fill the actual sample/user/Gateway mapping.
python3 locomo_run.py preflight --config config/locomo.remote.json --output evidence/locomo-run-001
python3 locomo_run.py run --config config/locomo.remote.json --output evidence/locomo-run-001
python3 locomo_run.py resume --config config/locomo.remote.json --output evidence/locomo-run-001
python3 locomo_run.py analyze --config config/locomo.remote.json --output evidence/locomo-run-001
```

Use actual `sample_id` values from the pinned local dataset. `--samples` is
optional. Selection uses a fixed-seed round-robin across conversation/category
strata, not the first conversation's first 100 questions. `--categories 1,2,3,4,5`
adds unanswerable questions with a declared `UNKNOWN` abstention rule; category 5
is not a security attack. Record the source dataset checksum emitted by convert.

The runtime preflight checks the pinned plugin, read-only QA settings, exact
transport identities and OpenViking's resolved ordinary-user identity. Applying
the fragment must be followed by the normal deployment/admission flow before
starting this experiment. A local configuration read does not independently prove
which configuration a stale preexisting Gateway process loaded.

## Recovery and records

Every create, message write, commit and QA has durable intent before submission.
Completed imports are not repeated. Known extraction task IDs resume via GET;
transient query failures have one additional query attempt. Unknown submission
outcomes remain unknown and are never automatically replayed. Configuration or
fixture changes reject resume. A new run is a new replicate, not a way to conceal
an unknown operation against the same application user.

Natural conversation sessions may legitimately produce zero additional memories.
The runner records archive status, extraction counts (including zero/unknown),
task IDs and failures without applying the synthetic-fact rule that every session
must produce a new memory. A completed answer may still have poor QA score or
missing injection evidence; `COMPLETE` describes workload completion, not a
security acceptance result.

- `state.json`: durable operation IDs, task IDs, statuses, scope/configuration
  digests, metadata-only request receipts and question/injection records.
- `predictions/*.json`: private local predicted answers. Keep the output directory
  protected. These are evaluation data, not runtime audit logs.
- `result.json`: reproducible per-question, per-category and per-conversation
  results, denominators, macro score and conversation-cluster bootstrap interval.

Scoring is declared as normalized token F1; category 1 uses the mean best match
for comma-separated/list references; other answerable categories use best
reference F1. Category 5 reports the explicit abstention match rate separately.
This deliberately modest lexical grader is **not** an official score or an LLM
judge. Report answered-only score and all-task score with uncompleted answers
assigned zero together, plus the complete UNKNOWN/NOT_RUN counts. Do not call
individual questions independent runs. Bootstrap intervals have few conversation
clusters and must be interpreted accordingly.

The new ContextEngine audit reports a hash and character count for the selected
recall block, its presence in input/output, search counts and native request IDs.
It neither records private text nor proves semantic support for a correct answer.
Task score, injection observation and receiver delivery compliance remain
separate measurements.

## Local validation

```sh
python3 -m unittest discover -s tests -p test_locomo_run.py -v
node --test ../../adapters/OpenClaw/spiffe_client/test/recall.test.mjs
node --check locomo_gateway.mjs
```

Tests use a controlled application adapter for state-machine failures and a
subprocess boundary check. They do not substitute for a remote Gateway/model run.

## Revision 1373: concurrent clients and paired fault trials

`examples/locomo.example.json` enables `concurrent_clients=true` and a pilot QA
schedule of release interval 60 s / deadline 180 s. Historical initialization
finishes for every conversation before the shared QA origin is set. Each Gateway
has one QA worker; independent Gateways may run concurrently. Questions remain
the selected original LoCoMo questions. Freeze the sample set, model and budgets
after pilot; record the actual overlap rather than assuming three clients imply
three simultaneous requests.

For a shared-service fault, add the following to a **separate** per-run config:

```json
{
  "condition": "fault",
  "concurrent_clients": true,
  "schedule": {"release_interval_s": 60, "deadline_s": 180},
  "controls": {
    "fault": {"at_s": 180, "argv": ["/secure/controls/inject-service", "{run_id}"], "timeout_s": 60},
    "recovery": {"at_s": 360, "argv": ["/secure/controls/recover-service", "{run_id}"], "timeout_s": 120}
  }
}
```

The absolute script paths are operator-created wrappers for the existing isolated
variant fault/release and normal stop/register/start/verify commands. They must
save actual server evidence using the passed run ID and preserve persistent data.
They are not supplied generic remote orchestration. Before a formal trial verify
both commands once against the actual deployment. Use a fixed SSH alias if the
commands are remote; secrets stay in protected files. Output is discarded by the
controller, so wrappers must write receipts directly to their declared evidence
location. A zero exit status establishes controller completion only; keep the
server's fault, identity and readiness receipts with the result.

The paired `no_fault` config uses identical times and timeout budgets, condition
`no_fault`, and `argv: []` for both controls. Use fresh ordinary users for every
run, including no-fault repeats. An admitted Gateway's configuration is changed
only through the normal deployment and registration flow before that run.
Recovery within a run keeps its storage. Choose enough questions per conversation
to release tasks before the fault, during it and after recovery. The generated
suite exposes `locomo_phase_counts`; an empty recovery phase cannot measure task
recovery. Inspect these counts before formal execution; a short smoke run with empty phases is only a functional smoke run. The times above are pilot examples, not a claim that recovery finishes
within a fixed bound.

Copy `examples/suite.locomo-paired.example.json`: `locomo_configs[group][seed]
[condition]` references each provisioned run's config. It reuses the normal runner:

```sh
python3 suite.py --config /secure/locomo-paired.json --output /secure/generated-locomo
python3 runner.py prepare --config /secure/generated-locomo/suite.json --output /secure/evidence/locomo01
# Deploy the selected run's declared Gateway/user configuration first.
python3 runner.py preflight --output /secure/evidence/locomo01 --role client --run-id RUN_ID
python3 runner.py run --output /secure/evidence/locomo01 --role client --run-id RUN_ID
python3 runner.py resume --output /secure/evidence/locomo01 --role client --run-id RUN_ID
python3 runner.py collect --output /secure/evidence/locomo01
python3 runner.py analyze --output /secure/evidence/locomo01
python3 plot.py --output /secure/evidence/locomo01
```

These commands are relative to this experiment directory. Start with one seed
for pilot; formal blocks are explicitly listed with per-run fresh users/configs.
No new deployment controller is introduced. Unknown initialization submissions
are not replayed. Once QA starts, resume only supplements known audit records:
no question, fault, recovery or missed release is replayed, and the time origin
is unchanged. `measurement_complete=true` means the scheduled experiment ended;
`COMPLETE/INCOMPLETE` separately indicates whether every question produced an
answer. Step PASS may therefore accompany INCOMPLETE QA, preserving every failure.

`application` records all planned/attempted questions, outcome and injection
counts, controller-to-Gateway and Agent CLI latency separately, request outcomes
and latency, actual worker overlap, and first successful access/task after the
recovery command. A valid completion requires an answer, observed injection and
completion before the deadline; it does not require a particular F1 and does not
prove delivery safety. Recovery access uses new requests started after recovery;
no such request leaves the time unknown. Latencies always carry sample counts.
Request metadata comes from the normal transport, including automatic recall;
HTTP 200 with an incomplete body is a failure. No audit body, prompt or key is
exported. Predicted answers remain in the protected evaluation directory.

Analysis retains all planned tasks, including absent native evidence and failed
runs. Paired estimates require matching protocol and observed models, plus
completed fault/recovery controls (or the explicit no-fault markers). It reports
same-arm fault-minus-control and Full-minus-native differences by independent
run block; missing or ineligible counterparts stay visible. Task/control timelines
and aggregate plots can be regenerated from the saved records. F1 remains in the
report as an auxiliary functional check. E2's receiver experiment separately
answers when an invalidated service stopped reading application data.
