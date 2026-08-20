# Argus asymmetric SPIFFE runtime

This profile implements the Broker Sidecar design for non-intrusive OpenViking
workload attestation. OpenViking remains the official Python process: it neither
calls the SPIRE Workload API nor receives an SVID, private key, or SPIRE socket.

## Current-stage boundary

Only the Evidence Provider and Trustee are mocks in this profile. The following
parts use the real implementation path:

- SPIRE Server and Agent 1.15.2;
- the experimental SPIRE Broker Endpoint and Broker API;
- a real host PID for the running OpenViking container;
- the external `argus_tdx_workload` WorkloadAttestor;
- selector matching and X.509-SVID issuance by SPIRE;
- the Broker Sidecar subscription and in-memory SVID rotation;
- mTLS termination and reverse proxying to OpenViking loopback port 1933.

The mock Evidence Provider returns PID-bound launch evidence. The mock Trustee
checks that binding and returns ALLOW or DENY. Therefore this profile does not
claim a real TDX Quote, TC-API/Rekor evidence, QGS result, or production Trustee
decision.

The design does not add a new protection boundary around a Docker administrator
or a compromised TDVM. It only proves and exercises the identity flow currently
in scope.

## Identity flow

1. The OpenViking SPIRE Agent completes `argus_tdx` Node Attestation.
2. The Broker Sidecar gets only its own
   `spiffe://argus.local/infra/openviking-broker` SVID through the ordinary
   Workload API.
3. The launcher obtains the actual host PID of the TC-API-launched OpenViking
   container and passes it to the Sidecar.
4. The Sidecar sends a `WorkloadPIDReference` over the Broker API. The Agent
   invokes `argus_tdx_workload` for that referenced PID.
5. The WorkloadAttestor calls the mock Evidence Provider and mock Trustee. An
   ALLOW result emits `verified`, `workload_id`, and `policy` selectors.
6. SPIRE matches those selectors together with the Docker selectors in the
   OpenViking Registration Entry and streams the target SVID to the Sidecar.
7. The Sidecar keeps the target key material in memory, accepts only the exact
   OpenClaw SPIFFE ID over mTLS, and proxies the request to
   `http://127.0.0.1:1933`.

The target Registration Entry has X.509 prefetch disabled so issuance is tied
to the Broker PID-reference request rather than background prefetch.

## Components

| Component | Responsibility |
|---|---|
| `plugins/argus-tdx-nodeattestor` | Node Attestation plus mock Evidence Provider and Trustee processes |
| `plugins/argus-tdx-workloadattestor` | PID-reference attestation and verified selector emission |
| `adapters/OpenViking/broker_sidecar` | Broker subscription, in-memory target SVID, mTLS, and loopback proxy |
| `adapters/OpenViking/scripts/launch_openviking.sh` | TC-API launch, real PID lookup, and Sidecar start |
| `adapters/OpenClaw/spiffe-transport` | OpenClaw caller-local Guard gate and SPIFFE mTLS client |
| `components/svid-materializer` | OpenClaw credentials only; it is not used by OpenViking |

## Local and remote verification boundary

This checkout can run Go tests, shell syntax checks, and Linux cross-builds on
Windows. Docker, Linux PID namespace, `pidfd_open`, SPIRE UDS permissions, and
the complete A-F flow must be run on the remote Linux/TDVM environment.

## Remote deployment order

On the remote validation host:

```bash
cd cczoo/agent-cc
export V2_RUNTIME_DIR=/var/lib/argus-spire-asymmetric/run-001
export V2_OPENVIKING_ORIGIN=https://openviking.argus.local:1943

sudo env \
  V2_RUNTIME_DIR="$V2_RUNTIME_DIR" \
  V2_OPENVIKING_ORIGIN="$V2_OPENVIKING_ORIGIN" \
  bash core/spire/runtime/asymmetric/scripts/prepare.sh
bash core/spire/runtime/asymmetric/scripts/start-server.sh
bash core/spire/runtime/asymmetric/scripts/start-openclaw-agent.sh
bash core/spire/runtime/asymmetric/scripts/start-openviking-agent.sh
```

In the TD Guest, build the pinned OpenViking image and Broker Sidecar image
before registration so their config digests are available:

```bash
cd cczoo/agent-cc
export OPENVIKING_LAUNCH_ACTION=build
bash adapters/OpenViking/scripts/launch_openviking.sh
```

Back on the validation host, register the OpenClaw, Broker, and strong
OpenViking target entries:

```bash
bash core/spire/runtime/asymmetric/scripts/register-workloads.sh
```

The command prints the exact OpenViking Agent SPIFFE ID. In the TD Guest, use
that value to launch OpenViking and its Sidecar:

```bash
export OPENVIKING_WORKLOAD_API_DIR=/run/argus-spire-v2/openviking
export OPENVIKING_BROKER_API_DIR=/run/argus-spire-v2/openviking-broker
export OPENVIKING_AGENT_SPIFFE_ID='spiffe://argus.local/spire/agent/argus_tdx/<exact-id>'
export OPENVIKING_LAUNCH_ACTION=launch
# Optional and unrelated to SPIFFE:
# export OPENVIKING_MODEL_CA_BUNDLE=/path/on/td-guest/model-ca-bundle.pem
bash adapters/OpenViking/scripts/launch_openviking.sh
```

By default TC API reloads `IMAGE_ID=openviking-cmem` as
`openviking-cmem:latest`. The registration script deliberately matches that
runtime `docker:image_id` while taking `docker:image_config_digest` from the
prebuilt source image. If `IMAGE_ID` is changed, set
`V2_OPENVIKING_RUNTIME_IMAGE_ID` to the exact resulting runtime tag before
running `register-workloads.sh`.

On the validation host, confirm the runtime relationship and then start the
OpenClaw workload:

```bash
bash core/spire/runtime/asymmetric/scripts/deploy-v2-guest.sh start-workload
bash core/spire/runtime/asymmetric/scripts/start-openclaw-workload.sh
export OPENVIKING_API_KEY='<non-root OpenViking user key>'
bash core/spire/runtime/asymmetric/scripts/connect-openclaw-plugin.sh
```

OpenViking publishes plaintext only on TD Guest loopback port 1933. The Broker
Sidecar publishes the protected mTLS endpoint on port 1943. The TDVM launcher
forwards that port to both host loopback and the Docker bridge gateway, so the
OpenClaw control-network container can reach it without exposing 1943 on
external host interfaces. Restart a TDVM that was started with the previous
loopback-only QEMU command before running this profile.

## Remote verification

Run the complete remote checks from the validation host:

```bash
OPENVIKING_API_KEY='<non-root key>' \
  bash core/spire/runtime/asymmetric/scripts/remote-test.sh all
```

The isolated hardware-free matrix exercises Broker ALLOW and DENY separately:

```bash
bash core/spire/tests/nodeattestor-mock/test.sh
M4_WORKLOAD_DECISION=deny bash core/spire/tests/nodeattestor-mock/test.sh
```

The positive flow must observe the Sidecar becoming ready with the target SVID
and then exiting when the referenced process exits. The DENY flow must never
receive the target SVID.

Individual runtime checks remain available:

```bash
bash core/spire/runtime/asymmetric/scripts/verify-svid.sh
bash core/spire/runtime/asymmetric/scripts/verify-mtls.sh
bash core/spire/runtime/asymmetric/scripts/verify-architecture.sh
bash core/spire/runtime/asymmetric/scripts/verify-openclaw-plugin-e2e.sh
```
