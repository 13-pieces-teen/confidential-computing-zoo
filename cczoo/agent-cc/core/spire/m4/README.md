# M4 validation

M4 has two separate validation profiles:

- **v2 architecture validation** uses the real OpenClaw and OpenViking business
  path, runs OpenViking inside a TD VM, and permits a mock Evidence Provider and
  mock Trustee. This profile validates placement, connectivity, protocol failure
  handling, and application authentication without requiring a real Quote.
- **TDX hardware security acceptance** requires a real Quote and an independently
  verifying Trustee. It remains a separate, stricter profile and is not implied
  by successful mock validation.

## v2 architecture profile

The validated topology is:

```text
OpenClaw (TDX Host, host network)
    | OpenViking User API Key over HTTP
    v
127.0.0.1:2933 (QEMU loopback forwarding)
    v
OpenViking v0.4.8 :1933 (inside TD VM)

SPIRE Agent/Server v1.15.1
    | v2 NodeAttestor protocol
    v
mock Evidence Provider + mock Trustee
```

The OpenViking path is real: health, readiness, User API key validation, and an
authenticated `GET /api/v1/sessions?limit=1` are executed from the OpenClaw
container. Only the remote-attestation services are mocked. Run the combined
validation while the TD VM is active and OpenClaw is configured for the forwarded
URL:

```bash
TDVM_SSH_IDENTITY=/path/to/tdvm-key core/spire/m4/test-architecture.sh
```

Set `RUN_MOCK_V2_MATRIX=0` to validate only the real business path. The default
also runs `test-failures.sh` for replay, Provider 503, Trustee 503, timeout, and
Prometheus classification. The script never prints the configured API key or
session content.

## Repeatable TD VM operations

Prepare a private overlay once, inject only the SSH public key, and start the TD
VM with loopback-only forwarding:

```bash
export TDVM_BASE_IMAGE=/path/to/ubuntu-tdx-base.qcow2
export TDVM_OVERLAY_IMAGE=/path/to/argus-openviking-tdx.qcow2
export TDVM_SSH_PUBLIC_KEY=$HOME/.ssh/id_rsa.pub
core/spire/m4/tdvm.sh prepare
core/spire/m4/tdvm.sh start
core/spire/m4/tdvm.sh status
```

`tdvm.sh` does not delete the base image or overlay. It adopts an already running
QEMU process with the same `TDVM_NAME`, so introducing the script does not
require restarting a manually launched Guest. `stop` sends SIGTERM only to that
named QEMU process and leaves both disk images intact.

Deploy the Host's current real OpenViking image and complete persistent state to
the running Guest:

```bash
export TDVM_SSH_IDENTITY=/path/to/tdvm-key
core/spire/m4/deploy-openviking-tdvm.sh deploy
core/spire/m4/deploy-openviking-tdvm.sh status
```

The deployment streams `docker save` directly to the Guest, briefly pauses the Host
source container while a read-only helper snapshots its complete state, removes only
the copied `data/.openviking.pid`, and retains the previous Guest state under a timestamped `.backup-*` path. If the Guest does not
already have Docker, set `DOCKER_RUNTIME_ARCHIVE` to an official static Docker
`.tgz`; no package-network access is required. Roll back to the latest retained
Guest state with:

```bash
core/spire/m4/deploy-openviking-tdvm.sh rollback
```

For a business-endpoint rollback, keep the original Host OpenViking service and
switch the existing OpenClaw plugin without re-entering or printing its API key:

```bash
core/spire/m4/switch-openclaw-openviking.sh http://127.0.0.1:1934
core/spire/m4/switch-openclaw-openviking.sh http://127.0.0.1:2933
```

Each switch retains the previous OpenClaw JSON, restarts the Gateway, and
requires an authenticated sessions request to return HTTP 200.

## Host and guest boundary

A TDX Host provides the virtualization infrastructure used to launch a TD VM.
It is expected that the Host does not expose `/dev/tdx_guest`: that device and
the `tdx_guest` kernel module belong inside the TD VM. Their absence on the Host
is not a TDX configuration failure and must not be used as an M4 readiness test.

Run the Agent-side components inside the TD VM:

- SPIRE Agent with the Argus NodeAttestor Agent plugin;
- the v2 Evidence Provider;
- `core/tdx-quote` and its TSM/configfs quote backend.

The SPIRE Server and independently verifying Trustee may run outside the TD VM,
provided the TD VM can reach both services over the configured authenticated
channels.

Before launching a TD VM, run the Host preflight:

```bash
core/spire/m4/check-tdx-host.sh
```

It checks KVM TDX enablement, QEMU `tdx-guest` support, TDVF, and the Host QGS
socket. Override `TDX_QGS_SOCKET` when the packaged QGS uses another path. QEMU
must connect the `tdx-guest` object to that socket through its
`quote-generation-socket` property.

Inside the TD VM, run the Guest preflight before hardware acceptance:

```bash
core/spire/m4/check-tdx-guest.sh
```

The Guest check requires the TD Guest character device, loaded Guest module,
and a writable TSM configfs report root used by `core/tdx-quote`. It
intentionally fails when run on the TDX Host.

To test the production TSM backend with a 48-byte SHA-384 digest inside the TD
VM, build or copy the smoke binary and run it as root:

```bash
cargo build --manifest-path core/tdx-quote/Cargo.toml --locked --bin tdx-quote-smoke
sudo core/tdx-quote/target/debug/tdx-quote-smoke <96-hex-character-digest>
```

## Software failure matrix

`test-failures.sh` exercises protocol and process failure paths without hardware:

- a first fresh attestation succeeds and caches evidence;
- a second Agent with a new key and challenge receives the cached old evidence
  and must fail without adding an Agent to SPIRE;
- Evidence Provider HTTP 503 must fail closed;
- Trustee HTTP 503 must fail closed;
- Trustee delay beyond the Server verifier timeout must fail closed;
- SPIRE Prometheus metrics must classify replay, HTTP 503, and timeout failures.

Run from the repository root:

```bash
core/spire/m4/test-failures.sh
```

This software matrix does not count as TDX security acceptance. The existing
`core/argus` Evidence Provider can generate a TDX Quote but still uses the old
request serialization and binding implementation. A strict v2 adapter must
expose `POST /ra/v1/evidence` using the v2 RFC 8785/SHA-384 REPORTDATA binding.
The production Trustee must implement `POST /v1/verify/tdx-node` with independent
quote, REPORTDATA, policy, and measurement verification before the hardware
matrix can run.

## Current host result

On 2026-07-29 the v2 architecture profile passed with real OpenClaw and
OpenViking v0.4.8 running inside the TD VM. Both `/health` and `/ready` returned
HTTP 200 through the loopback forward, the OpenViking plugin reported a valid
`user_key`, and the authenticated sessions request from OpenClaw returned HTTP
200. The mock v2 replay/failure/metrics matrix also remains the accepted
attestation profile for this version.

On 2026-07-28 this Host successfully launched a TD VM with the packaged TDVF.
The Guest kernel reported `tdx: Guest detected`, and the Guest preflight passed.
The first Quote smoke request then caused QEMU to report
`KVM: unknown exit reason 40`; no Host QGS process or socket was present. The
current hardware blocker is therefore the missing QGS and QEMU QGS socket
connection, not the absence of `/dev/tdx_guest` on the Host.
