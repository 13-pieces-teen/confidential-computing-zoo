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
