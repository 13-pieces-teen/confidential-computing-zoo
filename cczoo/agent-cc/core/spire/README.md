# Argus SPIRE integration

The formal runtime is the asymmetric Argus profile under
`runtime/asymmetric/`:

```text
OpenClaw (trusted RP)
  x509pop SPIRE Agent -> OpenClaw X509-SVID
  Argus Guard (caller-local policy)
  in-process HTTP transport -> direct SPIFFE mTLS

OpenViking (attested workload)
  Evidence Provider -> argus_tdx NodeAttestor
  -> SPIRE Server -> Trustee
  -> OpenViking X509-SVID -> native HTTPS API
```

Only the OpenViking Agent is remotely attested. OpenClaw has no Evidence
Provider in this phase. Guard does not verify a Quote or a certificate; it is
the Relying Party's caller-local authorization policy. SPIRE and TLS verify the
workload identities.

## Directory map

```text
spire/
  components/       reusable identity helpers (SVID materializer)
  plugins/          argus_tdx Agent/Server NodeAttestor plug-ins
  benchmarks/       asymmetric runtime and agent-task evaluation tooling
  runtime/
    asymmetric/     formal deployment, config, scripts, and remote validation
  tests/            isolated NodeAttestor and TDVM fixtures
```

The current business path has no standalone mTLS client or server proxy.
Legacy proxy-era wrappers, Docker-gate code, and the standalone mTLS diagnostic
were removed after the native asymmetric path was remotely validated. Their
implementation and validation evidence remain available in Git history and the
archived reports under `documents_ly/archive/`.

See [runtime/asymmetric/README.md](runtime/asymmetric/README.md) for the remote
host execution sequence and the current verification boundary.
