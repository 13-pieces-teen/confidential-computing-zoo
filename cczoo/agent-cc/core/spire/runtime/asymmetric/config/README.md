# Asymmetric runtime configuration

- `server.conf.tmpl`: SPIRE Server plus the server-side `argus_tdx` NodeAttestor.
- `openclaw-agent.conf.tmpl`: OpenClaw `x509pop` Agent and Docker WorkloadAttestor.
- `openviking-agent.conf.tmpl`: OpenViking `argus_tdx` Agent, Evidence Provider endpoint, and Docker WorkloadAttestor.
- `policy.yaml`: mock-stage Trustee measurement policy used by Node Attestation.
- `guard-policy.yaml.tmpl`: caller-local OpenClaw-to-OpenViking identity and service policy.

Run `../scripts/prepare.sh`. Generated configuration is written under the
absolute `V2_RUNTIME_DIR/conf` and is not committed. The Guard policy's target
origin comes from `V2_OPENVIKING_ORIGIN` and must match the OpenClaw plugin
base URL exactly.
