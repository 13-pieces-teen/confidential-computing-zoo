# Maintained upstream

- Upstream: https://github.com/spiffe/spiffe-helper
- Base tag: **v0.11.0**; upstream Apache-2.0 LICENSE retained.
- Imported release source: https://codeload.github.com/spiffe/spiffe-helper/tar.gz/refs/tags/v0.11.0
- Source archive SHA-256: 124b009c0dc737c5e5f7afd11eed4fe41b0ac9b98e98fc51cd1a49b38b3e6090
- Build identity: **0.11.0-argus.1**.

Imported sample/CI files and config test strings have trailing whitespace and
extra blank lines at EOF normalized; shell scripts retain executable mode.

Opt-in changes: `broker {}` configuration and dispatch, `pkg/broker` local PID subscription,
generation publication and failure handling; `pkg/authz`; the Agent HCL merge and
mTLS probe commands. Broker support requires go-spiffe v2.8.1 (v2.6.0 bundled by
upstream v0.11.0 does not contain the Broker protocol). The relative `workload`
module contains the instance contract and Linux pidfd observer.

With no Broker block, the upstream Workload API sidecar path and its tests
remain in use. Broker mode requires Linux, a root-owned tmpfs directory,
X.509-SVIDs, daemon operation, and the supplied systemd lifecycle. It does not
support upstream cmd/PID/JWT/HTTP-health options; readiness is the protected
`ready` file plus the NGINX service state.

Run upstream tests and Argus tests with `go test ./...` on Linux. The upstream
signal-configuration tests include POSIX expectations and fail on Windows;
Broker filesystem/lifecycle tests also require Linux. Keep this file and the
custom tests when rebasing the fork.
