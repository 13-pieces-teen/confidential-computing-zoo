# Formal configuration location

The formal Argus SPIFFE configuration is generated from the role-specific
templates under `../v2/`:

- `server.conf.tmpl`: one SPIRE Server with `x509pop` and `argus_tdx`
  NodeAttestors;
- `openclaw-agent.conf.tmpl`: the OpenClaw `x509pop` Agent;
- `openviking-agent.conf.tmpl`: the OpenViking `argus_tdx` Agent in the TDVM.

Run `../v2/prepare.sh` to inject the external-plugin checksums and TDVM address.
Generated files are written to `../v2/runtime/conf/` and are not committed.

There is intentionally no formal `join_token` configuration or token-generation
path.
