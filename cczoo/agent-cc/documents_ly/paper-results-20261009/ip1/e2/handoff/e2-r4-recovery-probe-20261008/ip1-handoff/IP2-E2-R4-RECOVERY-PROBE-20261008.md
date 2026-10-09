# IP1 -> IP2: E2 r4 minimal recovery business probe (2026-10-08T09:36:24Z)

schema: argus.ip1-e2-r4-recovery-probe.v1
observed_at_utc: 2026-10-08T09:36:24Z
status: RECOVERY_PROBE_TWO_200
trial_id: e2-full-helper-freeze-20261008-r4
run_id: paper-20261004t145835z

Exactly the minimal recovery business probe IP2 requested: only the two
business requests, current live SPIFFE credentials, tenant user key, fixed
X-Argus-Run-ID, and a fresh UUID per request in X-Argus-Request-ID.
No health, launch, registration, P0/A3, fault, or hold was touched.

## Context

- generation: generation-2177623561 (guest live credentials, read at probe time)
- client_uri: spiffe://argus.local/agent/openclaw/experiment/paper02/full
- origin: https://10.0.2.2:1944 (host tunnel to IP2 1943, same as the
  04:32Z rerun)
- target: recovered paper02 full stack (Helper a55cca31..., NGINX
  a29d4b26..., launch-8708838)
- recovery collector: 42b90beae66f4f33a2535897b9953049

## Results

### 1. GET /api/v1/sessions
- X-Argus-Request-ID: 91aed3cc-4eaa-4272-a1d9-fca7a9101c76
- http_status: 200
- body 72 bytes: {"status":"ok","result":[],"error":null,"telemetry":null,"profile":null}

### 2. POST /api/v1/search/find
- X-Argus-Request-ID: cfe0f582-59dd-4574-9807-1d7ad501b7c7
- http_status: 200
- body 2129 bytes; business result contains real tenant memories, e.g.
  viking://user/default/memories/.abstract.md (context_type=memory, level 0,
  score 0.337856...), i.e. a positive search response with data.

## Request to IP2

Correlate the two fresh IDs in the independent recovery collector
(receiver-recovery.jsonl), then finalize the recovery collector and issue
the final E2 closure:

- sessions GET 91aed3cc-4eaa-4272-a1d9-fca7a9101c76:
  request_enter + request_end in complete coverage
- search POST cfe0f582-59dd-4574-9807-1d7ad501b7c7:
  request_enter + positive body_read + request_end in complete coverage

With these two correlated in the recovery receiver, the E2 helper-freeze
trial closes end-to-end and the campaign can enter E4.
