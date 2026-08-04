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
| Caller authorization | Real Argus Guard process with `GUARD_MODE=mock_allow` |
| Workload transport | Direct SPIFFE mTLS with exact peer SPIFFE ID authorization |
| Real OpenClaw plugin traffic | Restricted bridge ingress to the OpenClaw mTLS client |
| Real Quote/QGS | `DEFERRED` |
| Unbypassable Guard/request gate | `DEFERRED` |
| Envoy/service mesh | `DEFERRED` |

Argus Guard and the mock Evidence Provider do not connect in this stage. The
Provider is part of OpenViking Agent Node Attestation only.

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
- exact OpenClaw and OpenViking workload SVIDs;
- cross-role label rejection on both Agents;
- Guard `mock_allow` response without fabricated verified claims;
- successful OpenClaw-to-OpenViking SPIFFE mTLS;
- the real OpenClaw source is accepted by the mTLS egress while a host-source
  request is rejected;
- rejection of plaintext, TLS without a client SVID, and the wrong server
  SPIFFE ID.

Run the real plugin message validation separately because it requires a
configured OpenClaw model and a non-root OpenViking API key:

```bash
bash core/spire/v2/verify-openclaw-plugin-e2e.sh
```

The script sends a real `openclaw agent` turn with a unique marker, locates the
captured marker through the OpenViking sessions API over the same mTLS egress,
commits the captured session, and waits for `commit_count > 0` plus an archive
overview. Set `V2_E2E_REQUIRE_MEMORY=1` only when the OpenViking LLM and
embedding backends are configured and memory extraction is part of the remote
acceptance target.

This proves real session capture and archive processing. It does not make
Argus Guard an unbypassable same-request gate; that boundary remains deferred.

## Fault injection

Provider and Trustee failures are configured independently.

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

## Compatibility entry points

The scripts under `../scripts/` delegate to this directory:

- `bootstrap-agent.sh` prepares v2 and starts the center plus OpenClaw Agent;
- `register-workloads.sh` performs the dual-parent registration;
- `verify-svid.sh` and `verify-mtls.sh` run the v2 validation.

They do not generate or consume Join Tokens.
