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
- host ports 18081, 18007, 1934, and 1943 are available;
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
- rejection of plaintext, TLS without a client SVID, and the wrong server
  SPIFFE ID.

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

## Compatibility entry points

The scripts under `../scripts/` delegate to this directory:

- `bootstrap-agent.sh` prepares v2 and starts the center plus OpenClaw Agent;
- `register-workloads.sh` performs the dual-parent registration;
- `verify-svid.sh` and `verify-mtls.sh` run the v2 validation.

They do not generate or consume Join Tokens.
