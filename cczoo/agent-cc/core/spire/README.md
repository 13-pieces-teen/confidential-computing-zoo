# Argus SPIRE integration

The current implementation includes Node Attestation and OpenViking Workload
Attestation with SPIRE Server/Agent and both Attestor SDKs pinned to v1.15.3:

```text
SPIRE Server -> argus_tdx Server NodeAttestor -> Trustee /attestation
SPIRE Agent  -> argus_tdx Agent NodeAttestor  -> TDX Evidence Provider UDS
TDX Evidence Provider -> Guest TSM -> QEMU/QGS -> real TDX Quote

TC API -> OpenViking service process -> protected target registration
SPIFFE Helper -> local SPIRE Broker API -> argus_tdx WorkloadAttestor
WorkloadAttestor -> TDX Evidence Provider UDS -> instance-bound TDX Quote
WorkloadAttestor -> Trustee /attestation -> verified EAR
Verified selectors -> static Entry -> target SVID -> Helper -> NGINX mTLS/AuthZ
```

The [Workload runbook](workload/README.md) is the build, installation, Node
configuration, launch, registration, and lifecycle entry point. The target environment
supplies approved image/configuration/platform baselines, existing Node
configuration and proof key, Trustee trust material and policy, and the SPIRE
bundle. [Validation records](workload/VALIDATION.md) distinguish completed local
tests from pending company TDVM acceptance.

Architecture: [Node Attestation](../../documents_ly/Argus-TDX-Node-Attestation-CN.md)
and [Workload Attestation](../../documents_ly/Argus-OpenViking-NGINX-SPIFFE-Helper-Workload-Attestation-Workflow-CN.md).

## Directory map

```text
spire/
  plugins/argus-tdx-nodeattestor/      Agent and Server Node plug-ins
  plugins/argus-tdx-workloadattestor/  PID-reference Workload plug-in
  helpers/spiffe-helper/             upstream v0.11.0 + Argus Broker/AuthZ tools
  workload/                         runbook, contracts, policy and lifecycle tools
  scripts/argus-node-attestation.sh   standalone Node operator commands
  tests/tdvm/                        TD Host and Guest preflight utilities
```

The TDX identity Evidence Provider is implemented by
[`../argus/src/bin/spire_evidence_provider.rs`](../argus/src/bin/spire_evidence_provider.rs),
built as `argus-spire-evidence-provider`.
It serves `POST /ra/v1/node-evidence` and, when workload configuration is supplied,
`POST /ra/v1/workload-evidence`. Both routes use the `/ra/v1/` namespace, with no
unversioned route aliases. These handlers use separate binding contracts and
share the real TSM Quote source. Workload SVID rotation does not generate a new
Quote; Helper reconnection triggers a new subscription and attestation.

The Provider's required `--agent-id` and the Server NodeAttestor's `agent_id`
must match, and the identity's trust domain must match SPIRE's core
`trust_domain`. The Node configuration supports one pinned Agent slot. The
combined OpenViking deployment takes identities, ports and directories from
[one deployment configuration](workload/config/environment.example.json).
The configured Agent ID is checked by Provider, WorkloadAttestor, Helper,
registration entries and policy; no workload-specific Agent ID is built in. See the
[identity configuration contract](../argus/docs/configuration.md#spire-node-attestation).

The Provider generates the raw Quote inside the attested TD; Trustee appraises
it, the Server NodeAttestor verifies the signed EAR and proof of possession,
and the SPIRE Server CA issues the Agent SVID. That SVID authenticates the
infrastructure Agent. The separate Workload flow above establishes a service
identity and its SVID.

## Node Attestation operator script

`scripts/argus-node-attestation.sh` wraps the deployed SPIRE Agent without
changing its configuration or generating bootstrap credentials. It validates
the policy deadline, pinned-key configuration, public trust bundle, Evidence
Provider socket, TLS certificate, ALPN, and HTTP/2 transport before starting an
Agent.

For an existing Node deployment, run from `cczoo/agent-cc` on the Agent node.
Set the actual approved policy deadline; the example below is a placeholder:

```bash
export ARGUS_POLICY_NOT_AFTER='<approved RFC3339 policy deadline>'
sudo core/spire/scripts/argus-node-attestation.sh preflight
sudo core/spire/scripts/argus-node-attestation.sh run
sudo core/spire/scripts/argus-node-attestation.sh status
```

`status` reports the Agent health, attestation phase, and Agent SPIFFE ID
without printing Quote, nonce, proof key, or SVID private material. The local
Workload API does not expose the Agent's own SVID. Run `server-status` on the
SPIRE Server node to obtain the authoritative Agent SVID serial number and
expiration:

```bash
sudo core/spire/scripts/argus-node-attestation.sh server-status
```

These commands inspect or run the existing Node configuration. Use the
[Workload runbook](workload/README.md) to deploy the combined authentication stack.
