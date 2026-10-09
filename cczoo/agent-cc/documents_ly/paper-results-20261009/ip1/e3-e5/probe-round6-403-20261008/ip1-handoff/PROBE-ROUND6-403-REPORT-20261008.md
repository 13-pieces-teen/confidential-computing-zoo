# IP1 -> IP2: paper02 three-leg probe round6 (2026-10-08T04:08Z) - root key authenticates but 403 on tenant data APIs

schema: argus.ip1-probe-round6-report.v1
observed_at_utc: 2026-10-08T04:08:23.886Z
label: paper02-full-20261008-round6
status: PARTIAL_403_ROOT_KEY_TENANT_SCOPE

The delivered business key authenticates successfully (health confirms
role:root), but it is the ROOT key, which the business layer refuses for
tenant-scoped data APIs in api_key mode.

## 1. Credentials used

- generation: generation-1726406905
- client_serial: B190E6E283E11C25221DEFB4B8FDB43C
- client_uri: spiffe://argus.local/agent/openclaw/experiment/paper02/full
- server SVID serial observed: 92CAF2ADC41DCC754BC1E78BC8D39231

## 2. Leg results

### mtls_health: PASS (200)
- body now reports identity context:
  {"status":"ok","healthy":true,"version":"v0.4.8","auth_mode":"api_key",
   "account_id":"default","user_id":"default","role":"root"}

### gateway_sessions: 403 (business layer)
- request_id: 71a17958-2a7a-471e-8e0b-23f82f9b6c60
- GET /api/v1/sessions; key check passed, business processed the request
  (x-process-time present), then denied:
- body: {"status":"error","result":null,"error":{"code":"PERMISSION_DENIED",
  "message":"ROOT API keys cannot access tenant-scoped data APIs in api_key
  mode. Use a user/admin API key for data access, or trusted mode for
  upstream identity assertion.","details":{}},"telemetry":null,"profile":null}

### gateway_search: 403 (business layer)
- request_id: a2103de7-27fd-4866-ae03-1e156e030edc
- POST /api/v1/search/find; same PERMISSION_DENIED body

## 3. Diagnosis

- Progress: Invalid API Key (401) is gone; the delivered key is valid and
  maps to role=root.
- The 403 is by design: the paper02 stack's server.root_api_key was
  delivered, but root keys are excluded from tenant-scoped data APIs in
  api_key mode.
- Two ways forward per the business error message:
  (a) deliver the paper02 stack's user/admin API key (for data access), or
  (b) enable trusted mode so the business asserts the upstream SPIFFE
      identity from the mTLS client SVID (this matches the E5/E3 goal of
      verifying the SPIFFE identity chain end-to-end).

## 4. Request to IP2

1. Provide the paper02 user/admin API key via the same protected handoff
   path (0600 root, ip2-handoff), or enable trusted mode and confirm the
   expected upstream-identity assertion behavior.
2. Confirm whether round6 request IDs (71a17958..., a2103de7...) appear in
   the independent receiver (403 responses are fully business-processed).

After the user/admin key (or trusted mode) is in place, IP1 reruns the
three-leg probe immediately and returns the 200 results.
