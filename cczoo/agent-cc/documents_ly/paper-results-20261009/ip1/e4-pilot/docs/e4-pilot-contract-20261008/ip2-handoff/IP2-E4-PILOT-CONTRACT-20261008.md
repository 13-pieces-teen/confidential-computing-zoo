# IP2 -> IP1: E4 pilot contract

status: `WAITING_FOR_IP1_GENERATED_MANIFEST`

E2 is closed PASS. E4 now follows the current recovery profile, not the older
Full/native four-condition text that remains in historical documentation.

Authoritative scope:

- profile: `argus-recovery-work-item-v1`
- scenario: `work-item-v1`
- group: Full only
- conditions: healthy (`no_fault`) and recovery (`fault`)
- fault: shared-service `helper-freeze`
- one client and one stable work item per run
- six steps: two normal, two pause, two recovery
- pilot: seed 1, two runs
- formal only after pilot freeze: seeds 101/102/103, healthy/recovery pairs,
  six runs

For the six-step protocol, the fault control is scheduled at
`2 * release_interval_s` and legal recovery at `4 * release_interval_s`.
The older `6x/12x` timing belongs to the legacy 18-step protocol and must not be
used. Example 60/120-second values are pilot placeholders, not approved formal
budgets.

Each run requires:

- a fresh ordinary private user and least-privilege user key;
- a distinct secret seed retained only in protected IP1 configuration;
- a fresh manifest run ID and evidence directory;
- the same pinned model, task structure, permissions and timing within the
  healthy/recovery pair;
- a per-run receiver binding/output;
- no task retries, one active task and one queued task;
- original Proposal, Decision, tool, archive, request and receiver evidence.

The healthy and recovery runs must not share a private user or secret seed.
Correct continuation on confirmed inputs, complete six-step task success and
legal recovery are separate verdicts.

## Required IP1 delivery

Before IP2 can prepare either pilot run, IP1 must generate the suite and return:

1. `suite.json` and `run-order.json`;
2. the actual healthy and recovery run IDs in manifest order;
3. protected summaries of both per-run continuous configs;
4. actual pinned provider/model;
5. actual Gateway container, Docker user, config path, agent ID and client
   SPIFFE ID;
6. the two distinct ordinary account/user IDs;
7. pilot release interval, deadline, stop budget and QA timeout;
8. proposed strict-SSH argv arrays for recovery-run fault and legal recovery.

Do not include API keys or secret seed values in the handoff.

After receiving these fields, IP2 will create the two fresh users, build
per-run receiver bindings on the existing audit socket mount, install
owner-verified fault/recovery controls, and issue separate server preflight
receipts. IP1 remains the sole E4 controller and triggers fault/recovery only at
the fixed run schedule.

No E4 run or fault has been started by this handoff. P0/A3, the existing
launch/container, chain and RTMR remain untouched.
