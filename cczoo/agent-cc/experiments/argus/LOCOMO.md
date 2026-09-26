# LoCoMo-derived application workload

This workload measures the usefulness and cost of persistent memory through the
real Argus entry. It does not establish a security result from an answer and does
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
