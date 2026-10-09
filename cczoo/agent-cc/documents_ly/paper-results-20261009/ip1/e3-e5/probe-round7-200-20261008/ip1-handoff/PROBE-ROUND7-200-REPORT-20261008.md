# IP1 -> IP2: paper02 three-leg probe round7 - ALL 200 (2026-10-08T04:19:39Z)

schema: argus.ip1-probe-round7-report.v1
observed_at_utc: 2026-10-08T04:19:39.829Z
label: paper02-full-20261008-round7
status: THREE_LEG_ALL_200

The user API key (default/default, role=user) delivered via
paper02-user-api-key authenticates and authorizes tenant-scoped data APIs.
Full three-leg success on the live paper02 stack.

## 1. Credentials used

- generation: generation-4163695741
- client_serial: 194F42CFA474E3B506505CB24295B643
- client_uri: spiffe://argus.local/agent/openclaw/experiment/paper02/full
- server SVID serial observed: FD60ABDA96CA8BA7965DF201AEA7829F

## 2. Leg results (fresh request IDs for receiver verification)

### mtls_health: PASS
- tls=ok, http 200, body: {"status":"ok","healthy":true,"version":"v0.4.8","auth_mode":"api_key"}

### gateway_sessions: PASS
- request_id: 785cc19a-1997-40ab-b60e-7667e0404369
- GET /api/v1/sessions -> http 200, application_status=ok, duration ~173ms

### gateway_search: PASS
- request_id: 4bcb88be-6ab4-4f5b-ae4a-2f401979c191
- POST /api/v1/search/find -> http 200, application_status=ok, duration ~291ms
- returned 7 items, e.g.:
  viking://user/default/memories/.abstract.md, viking://user/default/.overview.md,
  viking://user/default/peers/.abstract.md, viking://user/default/resources/.abstract.md,
  viking://resources/.overview.md, viking://user/default/privacy/.abstract.md,
  viking://user/default/skills/.abstract.md

## 3. Request to IP2

1. Verify independent receiver coverage for the two fresh request IDs above
   (GET /api/v1/sessions 785cc19a..., POST /api/v1/search/find 4bcb88be...).
2. Confirm the successful-request receiver evidence completes the
   round-trip closure for E5/E3 on the paper02 full stack.

Client identity chain verified live end-to-end: paper02/full client SVID
(presented and accepted via mTLS) + user API key authorization + business
data returned. No secrets included in this report.
