# Argus SPIRE integration

The maintained runtime is the dual-TDVM profile under
[`runtime/dual-tdvm/`](runtime/dual-tdvm/README.md):

```text
OpenClaw official runtime
  -> local HTTP -> Egress Broker -> caller-local Guard
  -> OpenClaw PID reference -> in-memory OpenClaw X.509-SVID
  -> cross-TDVM SPIFFE mTLS
  -> OpenViking Ingress Broker
  -> OpenViking PID reference -> in-memory OpenViking X.509-SVID
  -> loopback OpenViking HTTP API
```

Both TDVMs have independent SPIRE Agents and Node Attestation state. The two
business containers do not mount SPIRE sockets and do not hold SVID private
keys. Guard remains the caller-local policy decision point; the Egress Broker
is the policy enforcement point for the normal OpenViking plugin path.

## Directory map

```text
spire/
  plugins/          argus_tdx NodeAttestor and WorkloadAttestor plug-ins
  runtime/
    dual-tdvm/      deployment, config, scripts, and remote validation
  tests/            isolated NodeAttestor and TDVM fixtures
```

The former asymmetric preload/materializer runtime and its benchmark harness
have been removed. See the dual-TDVM README for the execution sequence and the
current Mock-versus-real-attestation evidence boundary.
