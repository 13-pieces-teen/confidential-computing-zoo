# IP1 -> IP2: paper02 Node policy diagnosis (non-secret)

schema: argus.node-policy-diagnosis.v1
status: DIAGNOSIS_DONE_ACTIVATION_PENDING_USER_AUTHORIZATION
checked_at: 2026-10-08T01:25Z (+08:00 09:25)

## 1. NodeAttestor config readback (live /etc/argus-workload/server.conf)

- agent_id            : spiffe://argus.local/spire/agent/argus_tdx/openviking-node
- slot_owner_key_sha256: be8570d79b444c272b579f1fb5a4ca69f625bc9561b1b1683942786958efff58
- policy_id           : argus-node-poc-ignore-tcb-20261003-01
- trustee_url         : https://trustee.argus.local:8443
- plugin_cmd          : /opt/argus-workload/bin/argus-tdx-nodeattestor-server
- plugin_checksum     : 183e098c38255fc3f1ed846a3e6d0218849ff2f79db14d556b9970eeba0c1ede
  (on-disk sha256 of the plugin binary matches this value)

Correction to the earlier hypothesis: the Server NodeAttestor does NOT point
at the paper02 workload policy. It points at the standalone Node policy
argus-node-poc-ignore-tcb-20261003-01.

## 2. Why the 2026-10-08T09:01:07+08 attempt was denied

- AS side (request_id a3969d22-9079-4064-a63b-2a984329866c): Quote DCAP check
  succeeded, MRCONFIGID check succeeded, Verifier/endorsement check passed
  (tee=Tdx, tee_class=cpu). Evidence-level verification was fine.
- Server side (request_id 82b279d9-fb3a-43c5-bc6a-0b89eb680680,
  method=AttestAgent): "Nodeattestor(argus_tdx): Trustee verification failed:
  EAR cpu0 appraisal is not affirming".
- The policy evaluated is argus-node-poc-ignore-tcb-20261003-01 whose
  not_after = 2026-10-05T16:00:00Z. Its time_window_valid is now false, so
  tdx_evidence_allowed cannot affirm. All measurement pins (mr_td 81a3ac2d,
  rtmr_0 354e1a21, rtmr_1 69ea76fd, xfam e71a06, debug=false,
  collateral_expiration_status=0) are unchanged for this same boot, and the
  verifier-level checks passed — the expired window is the blocking factor.
- GET /policy/argus-node-poc-ignore-tcb-20261003-01_cpu -> 200, 1603 bytes,
  sha256 63d14219759a52e33975d1670ce57206f8bdfaaba1e1049c1f29df34b8bf292c
  (byte-for-byte identical to the storage copy).

## 3. Node policy history (all 72h windows)

- 20260924-01: 2026-09-24T13:00Z -> 2026-09-27T13:00Z (expired)
- 20260924-02: 2026-09-24T15:50Z -> 2026-09-27T15:50Z (expired)
- 20260929-01: 2026-09-29T02:30Z -> 2026-10-02T02:30Z (expired)
- 20261003-01: 2026-10-02T16:00Z -> 2026-10-05T16:00Z (EXPIRED, current)

## 4. Proposed renewal (NOT yet activated)

argus-node-poc-ignore-tcb-20261008-01: identical pins to 20261003-01,
window 2026-10-08T01:00Z -> 2026-10-11T01:00Z (72h convention).

This renews the ignore-tcb Node policy family, so it requires explicit user
authorization on IP1. Upon authorization IP1 will: publish the rego to the
Trustee policy path + authoritative copy, GET-verify byte-for-byte, update
server.conf policy_id (with backup), briefly restart argus-spire-server
(stage2 client re-syncs automatically; entries, CA and datastore untouched),
then confirm here. The paper02 workload policy remains unchanged and stays
the admission policy for the target workload after Node admission.

## 5. Current Node record

openviking-node argus_tdx: expiration 2026-10-04 13:11:26 +08,
CanReattest=false (unchanged). A successful fresh attestation with the
renewed policy will repopulate this record under the same SPIFFE ID.
