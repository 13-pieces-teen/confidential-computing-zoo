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
  components/       reusable helpers (SVID materializer) and diagnostics
  plugins/          argus_tdx Agent/Server NodeAttestor plug-ins
  runtime/
    asymmetric/     formal deployment, config, scripts, and remote validation
  tests/            isolated NodeAttestor and TDVM fixtures
  compatibility/    legacy wrappers and archived proxy-era hardening
```

The current business path has no standalone mTLS client or server proxy.
`components/mtls-diagnostic` remains useful for isolated SPIFFE diagnostics,
but is built only when `V2_BUILD_DIAGNOSTICS=1`.

See [runtime/asymmetric/README.md](runtime/asymmetric/README.md) for the remote
host execution sequence and the current verification boundary.
