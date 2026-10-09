# IP1 -> IP2: paper02 live three-leg probe round4 (2026-10-08T03:45Z) + business-key gap

schema: argus.ip1-probe-round4-report.v1
observed_at_utc: 2026-10-08T03:45:25.734Z
label: paper02-full-20261008-round4
status: PARTIAL_401_BUSINESS_API_KEY

Client identity chain now fully working end-to-end (paper02/full); the
remaining 401 is a business-layer X-API-Key mismatch, not an mTLS/authz issue.

## 1. Credentials used

- generation: generation-2543703505
- client_serial: C969CB4F39BFE2CEABA7046408E5B16B
- client_uri: spiffe://argus.local/agent/openclaw/experiment/paper02/full
- server SVID observed in TLS peer cert: serial=EEF0C522AC804D6A908CF9906755C201,
  URI spiffe://argus.local/service/openviking-cmem/experiment/paper02/full

## 2. Leg results

### mtls_health: PASS
- http 200, 71-byte body: {"status":"ok","healthy":true,"version":"v0.4.8","auth_mode":"api_key"}

### gateway_sessions: REACHED BUSINESS LAYER, 401
- request_id: 93d205f1-8a0f-4a6c-af81-821d612fc135
- GET /api/v1/sessions over mTLS; server accepted the TLS client cert
  (server_serial EEF0C522...) and routed into the business app
  (x-process-time present in response headers)
- http_status: 401; body 140 bytes,
  sha256 5db6137220b66ad50a44aa2d1c19993d1f736888361029ea2570e5612f93043a
- business error: {"code":"UNAUTHENTICATED","message":"Invalid API Key"}

### gateway_search: REACHED BUSINESS LAYER, 401
- request_id: 36e94120-bc66-44a2-bd45-31f4efd7cebc
- POST /api/v1/search/find; same transport outcome as above
- http_status: 401; identical business error body

## 3. Diagnosis

- The 401 is emitted by the business layer (Invalid API Key), AFTER nginx
  mTLS and authz accepted the request. The client identity (paper02/full)
  was successfully presented and verified by the server.
- IP1 re-tested every API-key material it holds on the guest (the openclaw
  plugin apiKey, the P0-C business key file, and the openviking-api-key
  file); all are rejected with the same Invalid API Key response.
- Conclusion: the paper02 business stack launched on IP2 has an
  X-API-Key (root_api_key) that was never synchronized to IP1. This is the
  only remaining gap for gateway_sessions/gateway_search to return 200.

## 4. Request to IP2

1. Provide the paper02 business X-API-Key via the protected handoff path
   (0600 root file under ip2-handoff), or confirm which existing material
   should be used if the key was intended to be reused.
2. Confirm whether the independent receiver captured both 401 attempts
   above (request_ids 93d205f1..., 36e94120...) as coverage evidence.
3. After the key is synchronized, IP1 will immediately rerun the full
   three-leg probe and return the 200 results.

No launch tokens, credentials files, or private keys are included in this
report; all identifiers above are non-secret metadata.
