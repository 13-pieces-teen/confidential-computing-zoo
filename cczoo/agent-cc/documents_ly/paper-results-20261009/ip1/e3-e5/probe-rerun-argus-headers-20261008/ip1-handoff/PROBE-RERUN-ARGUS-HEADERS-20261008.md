# IP1 -> IP2: minimal two-request rerun with Argus audit headers (2026-10-08T04:32:06Z)

schema: argus.ip1-rerun-argus-headers.v1
observed_at_utc: 2026-10-08T04:32:06Z
status: RERUN_DONE_TWO_200
run_id: paper-20261004t145835z

Minimal closure rerun executed exactly as requested: only the two business
requests, current live SPIFFE credentials, tenant user key, fixed
X-Argus-Run-ID, and a fresh UUID per request in X-Argus-Request-ID.
No health, launch, registration, P0/A3, or fault was rerun.

## Context

- generation: generation-3059153746
- client_uri: spiffe://argus.local/agent/openclaw/experiment/paper02/full
- origin: https://10.0.2.2:1944 (same tunnel as Round 7)

## Results

### 1. GET /api/v1/sessions
- X-Argus-Request-ID: 913ea143-89fa-4f64-b9ca-09f7158e1426
- http_status: 200
- body 72 bytes: {"status":"ok","result":[],"error":null,"telemetry":null,"profile":null}

### 2. POST /api/v1/search/find
- X-Argus-Request-ID: 76037f47-0204-4775-b48d-d5b31b1dff91
- http_status: 200
- body 2129 bytes; business result contains real tenant memories, e.g.
  viking://user/default/memories/.abstract.md (context_type=memory, level 0,
  score 0.337856...), i.e. a positive search response with data.

## Request to IP2

Verify the independent receiver now shows correlated coverage for the two
fresh IDs:

- sessions GET 913ea143-89fa-4f64-b9ca-09f7158e1426:
  request_enter + request_end in complete coverage
- search POST 76037f47-0204-4775-b48d-d5b31b1dff91:
  request_enter + positive body_read + request_end in complete coverage

With these two, the E5/E3 receiver correlation can move from UNKNOWN to
CONFIRMED on the paper02 full stack.
