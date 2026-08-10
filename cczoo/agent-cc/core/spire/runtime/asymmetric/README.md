# Argus asymmetric attestation + SPIFFE runtime

This is the formal implementation of the current Argus Initial phase.

## Boundary

- OpenViking is the attested workload. Its Evidence Provider supplies evidence
  only to the OpenViking Agent-side `argus_tdx` NodeAttestor. The Server-side
  plug-in verifies that evidence through Trustee before the Agent is admitted.
- OpenClaw is the trusted Relying Party. Its Agent uses `x509pop`; it has no
  Evidence Provider and no remote-attestation claim.
- The caller-local Rust Guard evaluates an explicit YAML allowlist at
  `POST /guard/v1/authorize` in `spiffe_identity` mode.
- OpenClaw's own Node process calls Guard before sending a sensitive body, then
  directly uses its rotating X509-SVID for HTTPS.
- OpenViking's own Uvicorn/FastAPI process requires a SPIRE-validated client
  certificate and exactly `spiffe://argus.local/agent/openclaw`.
- OpenViking still applies its native API-key and application permissions after
  TLS authentication. There is no OpenViking-side Argus Guard.

The profile does not attempt to defend against a compromised trusted OpenClaw
runtime or Docker administrator. The old proxy/Docker-gate work is retained
under `../../compatibility/` and is not a prerequisite here.

## Components

| Location | Responsibility |
|---|---|
| `plugins/argus-tdx-nodeattestor` | OpenViking Agent/Server Node Attestation plug-ins |
| `components/svid-materializer` | Watch one exact Workload API identity and atomically publish rotating PEM files inside the same workload container |
| `core/argus/src/spiffe_guard.rs` | Caller-local SPIFFE policy parsing, validation, ALLOW/DENY, TTL, and audit IDs |
| `adapters/OpenClaw/spiffe-transport` | In-process fetch gate and direct SPIFFE mTLS client |
| `adapters/OpenViking/spiffe_server` | Native OpenViking ASGI TLS listener with exact client SPIFFE ID enforcement |

The materializer is credential plumbing, not a data-plane proxy: it never
accepts or forwards a business request.

## Remote-host preparation

Run these commands only on the remote validation host. This repository change
was intentionally not built or tested on the local Windows checkout.

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

`prepare.sh` builds the real Guard and OpenClaw workload images. The mock-stage
Evidence Provider and Trustee remain explicit; a real Quote/QGS/production
Trustee result is not claimed by this profile.

## Build, register, then launch OpenViking

The workload registration is pinned to the real application image digests, so
build the OpenViking image in the TD Guest before registration:

```bash
export OPENVIKING_SPIFFE_ENABLED=1
export OPENVIKING_SPIFFE_WORKLOAD_API_DIR=/run/argus-spire-v2/openviking
export OPENVIKING_LAUNCH_ACTION=build
bash adapters/OpenViking/scripts/launch_openviking.sh
```

From the validation host, create the two workload entries:

```bash
bash core/spire/runtime/asymmetric/scripts/register-workloads.sh
```

Then launch the already-built OpenViking image in the TD Guest:

```bash
export OPENVIKING_SPIFFE_ENABLED=1
export OPENVIKING_SPIFFE_WORKLOAD_API_DIR=/run/argus-spire-v2/openviking
# Optional: required only when the model provider uses a private CA. This path
# must exist in the TD Guest; the launcher mounts it read-only into the workload.
# export OPENVIKING_MODEL_CA_BUNDLE=/path/on/td-guest/model-ca-bundle.pem
export OPENVIKING_LAUNCH_ACTION=launch
bash adapters/OpenViking/scripts/launch_openviking.sh
```

The native workload mounts only the OpenViking Workload API directory, listens
on mTLS port 1943, and does not publish plaintext port 1933.

## Start OpenClaw and configure the plugin

The target hostname defaults to `openviking.argus.local`. Set
`V2_OPENVIKING_HOST_ADDRESS` to the TDVM/forwarded host address when it is not
available through DNS; the launcher adds only this exact host mapping.

```bash
# export V2_MODEL_CA_BUNDLE=/path/on/validation-host/model-ca-bundle.pem  # optional
bash core/spire/runtime/asymmetric/scripts/start-openclaw-workload.sh
export OPENVIKING_API_KEY='<non-root OpenViking user key>'
bash core/spire/runtime/asymmetric/scripts/connect-openclaw-plugin.sh
```

The OpenClaw container joins the Compose control network to reach Guard and
mounts its own Workload API directory read-only. `NODE_OPTIONS` loads the
in-process transport for the gateway and explicit `docker exec node` probes.

## Remote verification

Run the complete unit, isolated Node Attestation, native mTLS, Guard failure,
and business coverage from the remote host:

```bash
OPENVIKING_API_KEY='<non-root key>' \
  bash core/spire/runtime/asymmetric/scripts/remote-test.sh all
```

The stages can also be run independently as `unit`, `attestation`, and
`integration`. Every selected check runs even when an earlier check fails; the
script prints a consolidated failure list and returns non-zero at the end. The
isolated attestation stack uses host metrics ports 29988 and 29989 by default,
so it does not collide with the formal profile.

Integration verification covers:

- one live `x509pop` OpenClaw Agent and one live `argus_tdx` OpenViking Agent;
- isolated `argus_tdx` admission, replay rejection, Evidence Provider failure,
  Trustee failure, and Trustee timeout coverage;
- real workload SVIDs and expected Workload API mounts;
- Guard ALLOW and policy DENY;
- direct OpenClaw HTTPS success;
- missing client certificate and wrong client SPIFFE ID rejection;
- Guard DENY, malformed response, 503, timeout, and outage fail-closed;
- real OpenClaw turn, OpenViking session capture, commit, and archive.

## Remote evaluation

After functional validation passes, E3-E7 Guard, mTLS, capacity, SVID rotation,
resource, and attestation-amortization measurements are available under
[`../../benchmarks/asymmetric`](../../benchmarks/asymmetric/README.md). They run
only against the already-admitted asymmetric runtime and keep Mock RA/Mock
Trustee explicit in every manifest and report.

```bash
V2_RUNTIME_DIR=/var/lib/argus-spire-asymmetric/run-001 \
ARGUS_BENCHMARK_RESULT_ROOT=/var/lib/argus-spire-asymmetric/benchmarks \
  bash core/spire/runtime/asymmetric/scripts/remote-benchmark.sh all
```

The benchmark's mTLS-only profile is diagnostic. Formal capacity results use
the real OpenClaw preload, caller-local Guard, rotating SVID, and native
OpenViking mTLS server.

For a staged run, the scripts can also be invoked individually:

```bash
bash core/spire/runtime/asymmetric/scripts/verify-architecture.sh
bash core/spire/runtime/asymmetric/scripts/verify-guard-gate-failures.sh
bash core/spire/runtime/asymmetric/scripts/verify-openclaw-plugin-e2e.sh
```

## Migration and rollback

Inventory the old proxy containers without changing state:

```bash
bash core/spire/runtime/asymmetric/scripts/migrate-from-proxy-profile.sh plan
```

`apply` removes only the two named legacy mTLS proxy containers. It does not
delete application volumes, images, SPIRE data, or OpenViking state. The old
code remains under `compatibility/proxy-hardening/` for reference.
