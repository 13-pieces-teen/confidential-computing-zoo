# Argus SPIRE integration

The formal runtime is the asymmetric Argus profile under
`runtime/asymmetric/`:

```text
OpenClaw (trusted RP)
  x509pop SPIRE Agent -> OpenClaw X509-SVID
  Argus Guard (caller-local policy)
  in-process HTTP transport -> SPIFFE mTLS

OpenViking (attested workload)
  Evidence Provider -> argus_tdx NodeAttestor
  -> SPIRE Server -> Trustee
  -> Broker PID reference -> argus_tdx_workload
  -> OpenViking target X509-SVID -> Broker Sidecar
  -> loopback OpenViking HTTP API
```

Only the OpenViking Agent is remotely attested. OpenClaw has no Evidence
Provider in this phase. Guard does not verify a Quote or a certificate; it is
the Relying Party's caller-local authorization policy. SPIRE and TLS verify the
workload identities.

## Directory map

```text
spire/
  components/       OpenClaw identity helpers (SVID materializer)
  plugins/          argus_tdx NodeAttestor and WorkloadAttestor plug-ins
  benchmarks/       asymmetric runtime and agent-task evaluation tooling
  runtime/
    asymmetric/     formal deployment, config, scripts, and remote validation
  tests/            isolated NodeAttestor and TDVM fixtures
```

OpenViking itself has no SPIRE integration. The dedicated Broker Sidecar owns
the target SVID in memory, terminates mTLS, and forwards only to OpenViking's
TD Guest loopback listener. The old Python TLS wrapper and OpenViking-side
materializer path have been removed.

See [runtime/asymmetric/README.md](runtime/asymmetric/README.md) for the remote
host execution sequence and the current verification boundary.
