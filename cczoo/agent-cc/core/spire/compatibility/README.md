# Compatibility and archived paths

This directory is not part of the asymmetric runtime data path.

- `scripts/` keeps legacy command names as wrappers around the current runtime.
- `proxy-hardening/` preserves the former proxy-era WP2/WP3 implementation for
  historical comparison. It expects the old proxy Compose service and must not
  be mixed with `runtime/asymmetric/compose.yaml`.

The Docker control-plane hardening work is retained here because it may still
be useful independently. It is not a prerequisite for the trusted-OpenClaw
Relying Party model.
