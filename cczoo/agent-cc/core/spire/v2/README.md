# Argus SPIFFE v2 remote execution

This is the formal mock-stage runtime. It does not use SPIRE Join Tokens and it
does not provide a rollback profile.

## Implemented boundary

| Role | Runtime |
| --- | --- |
| OpenClaw node identity | Independent SPIRE Agent using built-in `x509pop` |
| OpenViking node identity | Independent SPIRE Agent using external `argus_tdx` |
| OpenViking evidence | Guest-local mock Evidence Provider on `127.0.0.1:18080` |
| Verification service | Independent center-side mock Trustee over file-backed mTLS |
| Caller authorization | OpenClaw mTLS egress PEP synchronously calls the real Argus Guard PDP with `GUARD_MODE=mock_allow` |
| Workload transport | Direct SPIFFE mTLS with exact peer SPIFFE ID authorization |
| Real OpenClaw plugin traffic | Restricted `--internal` bridge ingress to the OpenClaw mTLS client |
| Docker control plane (WP2) | OpenClaw gateway reaches Docker only through `argus-docker-gate`, a minimal allowlist socket proxy that forbids privileged/host-network/host-mount/capability/device creates |
| Real Quote/QGS | `DEFERRED` |
| Unbypassable Guard/request gate | Verified: real OpenClaw business requests require a matching Guard ALLOW before SPIFFE mTLS forwarding |
| Envoy/service mesh | `DEFERRED` |

Argus Guard and the Guest-local mock Evidence Provider do not connect in this
stage. The Provider is part of OpenViking Agent Node Attestation only. A future
`fresh_evidence` Guard mode would use a separately protected OpenViking service
evidence endpoint; it must not expose the Guest loopback Node Attestation
endpoint directly.

Guard mode is mandatory and the v2 runtime requires authorization context by
default. The incomplete legacy `evidence` mode is disabled unless an isolated
development environment explicitly sets `GUARD_ALLOW_INCOMPLETE_EVIDENCE=1`;
that override is not part of this v2 runtime.

## Prerequisites

- Linux host with Docker and Docker Compose;
- `openssl`, `python3`, `ssh`, `tar`, and `curl` on the host;
- the OpenViking TDVM from `../m4/` is running and reachable over SSH;
- Docker is installed in the TDVM;
- OpenViking is already listening on TDVM loopback port 1933;
- the real OpenClaw Gateway container is already running;
- host ports 18081, 18007, and 1943 are available;
- the default `172.31.44.0/28` egress subnet is unused, or the egress network
  variables are overridden consistently;
- commands below are run as root.

The TDVM must be restarted once after taking the updated `m4/tdvm.sh`, because
the QEMU command line now adds host loopback port 1943 forwarding to Guest port
1943.

## Execution order

Set the SSH identity once if it is not the default key:

```bash
export TDVM_SSH_IDENTITY=/root/.ssh/id_rsa
```

Build the repository-owned binaries and generate the short-lived development
certificates/configuration:

```bash
core/spire/v2/prepare.sh
```

`V2_RUNTIME_DIR`, when set, must be an absolute host path. Keep it exported for
every prepare, start, registration, and verification command belonging to that
runtime. The center-side scripts pass the same path to Compose and force
recreation of SPIRE processes so regenerated configuration and plugins are
loaded.

Start the center-side Trustee and SPIRE Server, then the OpenClaw Agent and real
Argus Guard process:

```bash
core/spire/v2/start-server.sh
core/spire/v2/start-openclaw-agent.sh
```

Deploy the Guest-local Evidence Provider and OpenViking `argus_tdx` Agent:

```bash
core/spire/v2/start-openviking-agent.sh
```

The default generated OpenViking Agent config reaches the host SPIRE Server at
`10.0.2.2:18081`. Override before `prepare.sh` when the TDVM network differs:

```bash
export V2_TDVM_SPIRE_SERVER_ADDRESS=10.0.2.2
export V2_SPIRE_SERVER_PORT=18081
```

After both Agents are healthy, register the two workload identities under their
different Agent IDs:

```bash
core/spire/v2/register-workloads.sh
```

The script rejects a shared parent and registers role label, immutable
`image_id`, and `image_config_digest` selectors.

Start the two mTLS workloads:

```bash
core/spire/v2/start-openclaw-workload.sh
core/spire/v2/start-openviking-workload.sh
```

`start-openclaw-workload.sh` creates or validates a dedicated Docker bridge,
attaches the real OpenClaw container at a fixed IP, and starts the host-network
mTLS client on the bridge gateway. The client only accepts the configured
OpenClaw source IP. The Workload API socket remains mounted only in the mTLS
client container.

The source-IP check prevents ordinary sibling workloads from borrowing the
egress identity. It is not a security boundary against a process that already
controls the host Docker daemon; the OpenClaw sandbox deployment still mounts
the Docker socket.

The defaults are:

```bash
export V2_REAL_OPENCLAW_CONTAINER=agentcc-openclaw-sbx-gateway
export V2_OPENCLAW_EGRESS_NETWORK=argus-openclaw-egress
export V2_OPENCLAW_EGRESS_SUBNET=172.31.44.0/28
export V2_OPENCLAW_PROXY_BIND=172.31.44.1
export V2_OPENCLAW_EGRESS_IP=172.31.44.2
export V2_OPENCLAW_PROXY_PORT=1934
```

If the network already exists, its driver, subnet, gateway, and OpenClaw IP
must match. The script fails instead of disconnecting or silently readdressing
an existing container.

Configure the real OpenViking context-engine plugin after both mTLS workloads
are ready:

```bash
export OPENVIKING_API_KEY='<non-root OpenViking user key>'
bash core/spire/v2/connect-openclaw-plugin.sh
```

Set `OPENCLAW_INSTALL_PLUGIN=0` when the expected plugin version is already
installed and only its remote endpoint should be reconfigured.

The plugin remains a normal remote HTTP client. Its `baseUrl` points to the
restricted bridge gateway, while the mTLS client obtains and rotates the
OpenClaw SVID. The API key is preserved for OpenViking application-level
authorization and is not printed by the connection script.

Run the complete remote-host validation:

```bash
core/spire/v2/verify-architecture.sh
```

The validation checks:

- one live `x509pop` Agent and one live `argus_tdx` Agent;
- no live Join Token Agent;
- different workload parents and Workload API sockets;
- active Workload API mount sources match the explicit `V2_RUNTIME_DIR` and
  `V2_GUEST_RUN` values;
- exact OpenClaw and OpenViking workload SVIDs;
- cross-role label rejection on both Agents;
- Guard requires a versioned business `authorization_context`, returns
  `mock_allow` without fabricated verified claims, and echoes a matching
  decision ID and request digest;
- authorization context v2 binds the exact target service, target URI, and
  target SPIFFE ID in addition to method, path, query, body hash, and caller;
- a digest-valid context whose target differs from `VerifyRequest.target` is
  rejected;
- the same request ID, Guard decision ID, and request digest appear in Guard
  and mTLS egress logs before the request is forwarded;
- successful OpenClaw-to-OpenViking SPIFFE mTLS;
- the real OpenClaw source is accepted by the mTLS egress while a host-source
  request is rejected with the expected body and matching proxy request ID;
- the real OpenClaw container has no SPIRE Workload API mount;
- rejection of plaintext, TLS without a client SVID, and the wrong server
  SPIFFE ID.

Host-side probes bypass the environment HTTP proxy only for the local Guard,
bridge gateway, and loopback TDVM addresses. External traffic continues to use
the machine's configured corporate proxy. This prevents an upstream proxy's
HTTP 403 page from satisfying the mTLS egress source-rejection assertion.

Run the real plugin message validation separately because it requires a
configured OpenClaw model and a non-root OpenViking API key:

```bash
bash core/spire/v2/verify-openclaw-plugin-e2e.sh
```

The script sends a real gateway-backed `openclaw agent` turn with a unique
marker. Its JSON result must report `status=ok`, a non-empty `runId`, and a
`result` object. Before issuing any E2E scan, commit, or inspection request, it
captures the mTLS proxy log window and requires a successful write-class
`/api/v1/` request from the configured OpenClaw source IP with a valid Guard
decision receipt. After locating the captured marker, it also requires the
pre-scan write evidence to target that exact OpenViking session's `/messages`
endpoint and finds the matching Guard ALLOW log by request ID, decision ID, and
request digest. It then commits the captured session and waits for
`commit_count > 0` plus an archive overview. Set
`V2_E2E_REQUIRE_MEMORY=1` only when the OpenViking LLM and embedding backends
are configured and memory extraction is part of the remote acceptance target.

A successful run proves real session capture, archive processing, and the
mock-stage same-request Guard-to-mTLS gate. It does not prove real Quote/QGS,
production Trustee verification, or a production Guard policy.

## Fault injection

Provider and Trustee failures are configured independently.

Run the caller-side Guard gate failure matrix after the positive architecture
validation:

```bash
bash core/spire/v2/verify-guard-gate-failures.sh
```

The script temporarily points only the OpenClaw mTLS egress at a loopback fault
stub and verifies valid Guard DENY, malformed DENY, HTTP 503, timeout, malformed
JSON, missing decision receipt, mismatched request digest, and expired decision
receipt. Every case must return the exact fail-closed response and must have no
`forwarded_mtls` log for the same generated request ID. A valid DENY must
preserve its decision receipt, while malformed responses must not leak one. Its
exit trap reports restoration failures, and the script finishes only after the
restored egress completes a real Guard-gated health request.

Run the OpenClaw-side WP3 lifecycle and denial-convergence matrix only against
an isolated runtime:

```bash
bash core/spire/v2/verify-wp3.sh
```

The mTLS egress and server default to a 60-second absolute connection lifetime
and a 30-second idle timeout. Override both before `prepare.sh` when required:

```bash
export V2_CONN_MAX_LIFETIME=60s
export V2_CONN_IDLE_TIMEOUT=30s
```

`verify-wp3.sh` observes the actual workload SVID expiry rather than assuming
the requested entry TTL was accepted unchanged. Its default 420-second SLA
budget covers SPIRE's five-minute minimum test TTL, the connection lifetime,
and probe tolerance. If `V2_CONN_MAX_LIFETIME` is increased, set
`V2_WP3_CONNECTION_GRACE_SECONDS` to at least that lifetime plus probe
tolerance and increase `V2_WP3_SLA_BUDGET_SECONDS` accordingly. A failure
triggers restoration of the Agent, workload
entry, ban state, egress identity, and positive path. Trust-bundle rotation,
wrong trust domain, stale bundle, and the OpenViking `can_reattest=false`
expiry case still require dedicated runtimes and are reported as skipped.

Before deploying the OpenViking Agent:

```bash
export V2_EVIDENCE_STATUS=503
export V2_EVIDENCE_DELAY=20s
export V2_REPLAY_EVIDENCE=true
```

Before starting the center runtime:

```bash
export V2_TRUSTEE_STATUS=503
export V2_TRUSTEE_DELAY=20s
```

Recreate only the affected service after changing a variable. These controls
are for the remote mock failure matrix; clear them for the positive architecture
run. Changing a fault variable after an Agent is already attested does not force
Node Attestation to run again. Use a fresh, isolated v2 runtime for each failure
case rather than deleting or reusing the positive-run identity state.

For example, run one fault case with a consistently scoped set of paths:

```bash
export V2_RUNTIME_DIR=/var/lib/argus-spire-v2-runtimes/trustee-503
export V2_GUEST_ROOT=/opt/argus-spire-v2/trustee-503
export V2_GUEST_DATA=/var/lib/argus-spire-v2/trustee-503/openviking-agent
export V2_GUEST_RUN=/run/argus-spire-v2/trustee-503/openviking
export V2_TRUSTEE_STATUS=503
```

Guest overrides are intentionally restricted to scoped paths under
`/opt/argus-spire-v2`, `/var/lib/argus-spire-v2`, and
`/run/argus-spire-v2`. This prevents a malformed environment variable from
turning deployment ownership changes into a recursive modification of a system
directory. Isolated cases are run sequentially because the container names and
host ports remain fixed.

## WP2: Docker control-plane isolation and network tightening

The OpenClaw gateway no longer controls the raw Docker daemon socket. It reaches
Docker only through `argus-docker-gate`, a minimal socket proxy that enforces a
strict endpoint allowlist and validates `POST /containers/create` (no
`--privileged`, no `host` or named-network mode, no `cap_add`, no `unconfined`
security options, no arbitrary host binds, no device mounts, and only the
configured sandbox image).

Start the proxy and repoint the gateway:

```bash
core/spire/v2/start-docker-gate.sh        # build + run the proxy on /var/run/argus/docker-proxy.sock
core/spire/v2/apply-wp2.sh                # ensure proxy, repoint gateway socket, rebuild egress bridge --internal, recreate egress
```

`apply-wp2.sh` is idempotent and is the single entry point that applies the WP2
state to a running v2 deployment. The egress bridge is recreated with
`--internal` so only the gateway is attached and no bridge container can reach
outside the bridge. Verify with:

```bash
core/spire/v2/verify-wp2.sh
```

which checks the gateway socket is the proxy, privileged/unsafe creates are
denied, the bridge is `--internal` with only the gateway, sibling containers get
403 `source_rejected`, the gateway is not host-networked / not on the identity
plane, and the positive Guard-gated egress still returns 200.

## Compatibility entry points

The scripts under `../scripts/` delegate to this directory:

- `bootstrap-agent.sh` prepares v2 and starts the center plus OpenClaw Agent;
- `register-workloads.sh` performs the dual-parent registration;
- `verify-svid.sh` and `verify-mtls.sh` run the v2 validation.

They do not generate or consume Join Tokens.
