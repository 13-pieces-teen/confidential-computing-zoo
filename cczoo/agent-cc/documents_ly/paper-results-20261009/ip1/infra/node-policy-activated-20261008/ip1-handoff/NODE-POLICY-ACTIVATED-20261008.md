# IP1 -> IP2: paper02 Node policy activated (non-secret)

schema: argus.node-policy-activated.v1
status: NODE_POLICY_ACTIVATED_READY_FOR_RETRY
activated_at: 2026-10-08T01:15:31Z (+08:00 09:15:31)

## Authorization

Explicit IP1 user authorization obtained 2026-10-08T01:31Z. Approval receipt:
/var/lib/argus-review/node-policy-renewal-20261008/ip1/APPROVAL.md

## Policy

- policy_id : argus-node-poc-ignore-tcb-20261008-01
- sha256    : cd0579e21806185b77c061b8d63309989341f0498ca1aebf92c30f062410655f
- window    : 2026-10-08T01:00:00Z -> 2026-10-11T01:00:00Z (72h, family convention)
- lineage   : byte-for-byte copy of argus-node-poc-ignore-tcb-20261003-01
  except the two window constants; all pins unchanged (mr_td 81a3ac2d...,
  rtmr_0 354e1a21..., rtmr_1 69ea76fd..., xfam e71a06..., debug=false,
  collateral_expiration_status=0).
- GET readback: https://127.0.0.1:8444/policy/argus-node-poc-ignore-tcb-20261008-01_cpu
  -> 200, 1603 bytes, sha256 cd0579e2... identical to published file.

## Server NodeAttestor repointed and restarted

- server.conf policy_id now argus-node-poc-ignore-tcb-20261008-01
  (backup: /etc/argus-workload/server.conf.before-20261008T0135Z-node-policy,
  one-line diff).
- argus-spire-server restarted 2026-10-08T09:15:31+08:00:
  old MainPID 2027950 -> new MainPID 1714105, unit active/running.
- Plugins loaded: argus_tdx NodeAttestor (plugin_checksum 183e098c...),
  x509pop NodeAttestor, disk KeyManager. APIs up on
  /run/spire/server/private/api.sock and [::]:8081.
- CA continuity: current bundle CA unchanged, C5:21:13:CF...
  (valid until 2026-10-08T16:38:50Z). No chain initialization occurred.
- stage2 client unaffected: agent x509pop/0792eff1... reconnected at
  09:15:43+08 and rotation resumed (BatchNewX509SVID for paper02/full
  entry 5f75fa03 and paper02/native a92ef7d3, 300s TTLs).

## IP2 next step

Retry the fresh Node attestation (isolated data dir retained at
/var/lib/spire/argus-poc/paper02-data, or a new one). Expected flow:
TLS/challenge/proof-key/Quote -> Trustee evaluates
argus-node-poc-ignore-tcb-20261008-01_cpu (window valid now) -> affirming
EAR -> Node re-admission under
spiffe://argus.local/spire/agent/argus_tdx/openviking-node.

After Node admission, target workload attestation continues to be governed
by argus-workload-openviking-paper02-20261004-01 (unchanged), then helper /
business entry / 1943, then signal IP1 to rerun the three-leg probe.

The paper02 workload policy and native line4 remain untouched.
