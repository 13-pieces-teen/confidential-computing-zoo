# Argus SPIFFE v2

The formal configuration is `v2/`. It directly replaces the former single-Agent
Join Token bootstrap; there is no runtime profile that switches back to it.

The topology is:

- one SPIRE Server supporting both `x509pop` and `argus_tdx`;
- an OpenClaw Agent using `x509pop`, its own Docker daemon, data directory, and
  Workload API;
- an OpenViking Agent inside the TDVM using `argus_tdx`, the TDVM Docker daemon,
  a different data directory, and a different Workload API;
- one Guest-local mock Evidence Provider used only by the OpenViking
  `argus_tdx` Agent plugin;
- one independent center-side mock Trustee used only by the SPIRE Server
  `argus_tdx` plugin;
- a real Argus Guard process in explicit `mock_allow` connectivity mode;
- direct SPIFFE mTLS between the two workload identities.

See `v2/README.md` for the remote-host execution sequence. The compatibility
scripts in `scripts/` now delegate to v2 and do not generate Join Tokens.

`m3/` and the original M4 failure matrix remain historical test fixtures for the
custom NodeAttestor. They are not formal startup or rollback paths.

Current mock boundary:

- Evidence Provider: mock, OpenViking side only;
- Trustee: independent mock;
- Argus Guard: real process with explicit mock allow;
- Quote/QGS: deferred;
- unbypassable same-request Guard-to-mTLS gate: deferred;
- Envoy/service mesh: deferred.
