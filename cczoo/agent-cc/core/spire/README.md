# Argus SPIRE integration

The current executable scope is the first OpenViking Node Attestation stage:

```text
SPIRE Server -> argus_tdx Server NodeAttestor -> Trustee /attestation
SPIRE Agent  -> argus_tdx Agent NodeAttestor  -> TDX Evidence Provider UDS
TDX Evidence Provider -> Guest TSM -> QEMU/QGS -> real TDX Quote
```

The repository does not define an end-to-end deployment entry. The target
environment must supply the Trustee trust material and policy, the fixed
proof-key pin, the SPIRE bundle, and a reachable Agent-to-Server address.

## Directory map

```text
spire/
  plugins/argus-tdx-nodeattestor/  current Agent and Server plug-ins
  tests/tdvm/                       TD Host and Guest preflight utilities
```

The TDX identity Evidence Provider is implemented by
`../argus/src/bin/tdx_evidence_provider.rs`. Workload Attestation and the
second Quote remain outside the current runtime scope.

## Node Attestation operator script

`scripts/argus-node-attestation.sh` wraps the deployed SPIRE Agent without
changing its configuration or generating bootstrap credentials. It validates
the policy deadline, pinned-key configuration, public trust bundle, Evidence
Provider socket, TLS certificate, ALPN, and HTTP/2 transport before starting an
Agent.

On the Agent node:

```bash
export ARGUS_POLICY_NOT_AFTER=2026-09-04T10:09:27Z
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
