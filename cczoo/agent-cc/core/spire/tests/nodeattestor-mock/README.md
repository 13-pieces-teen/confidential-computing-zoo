# M3/M4 hardware-free SPIRE integration

This environment runs SPIRE Server and Agent 1.15.2 with the external
`argus_tdx` NodeAttestor and `argus_tdx_workload` WorkloadAttestor. A local mock
Evidence Provider produces binding-aware node and workload evidence. An mTLS
mock Trustee validates those bindings and returns ALLOW or DENY.

The M3 files are isolated from the Phase 1 `join_token` configuration. Generated
keys, certificates, rendered configuration, plugin binaries, and SPIRE state live
under `runtime/` and are ignored by Git.

## Prerequisites

- Docker with Compose support
- OpenSSL
- permission to change ownership of `runtime/server-data` to UID 1000
- the module cache at `/tmp/argus-go-cache`, or `ARGUS_GO_CACHE_DIR` pointing to
  an equivalent populated cache

## Run

```bash
cd core/spire/tests/nodeattestor-mock
bash test.sh
```

The default test builds the plug-ins and Broker Sidecar, generates short-lived
certificates, validates both SPIRE configurations, and completes two flows:

1. the existing Node Attestation and Docker selector matrix;
2. a real Broker PID-reference request for a running target process, custom
   Workload Attestation, strong Registration Entry matching, target SVID
   delivery, and Sidecar exit after the referenced process exits.

The Broker target is a BusyBox fixture used to exercise the software chain. It
does not claim to be a real OpenViking/TC-API launch or hardware evidence.

Run the Trustee DENY path separately:

```bash
M4_WORKLOAD_DECISION=deny bash test.sh
```

The DENY run requires all three observations: the mock Trustee records a
`workload_trustee/denied` request, the Broker API returns permission denied, and
the OpenViking target SVID is never delivered to the Broker Sidecar.

The Agent uses the fake service container's network namespace so the Evidence
Provider remains a loopback-only channel. It uses the host PID namespace because
SPIRE's Docker WorkloadAttestor must resolve peer PIDs from the shared Workload API
socket to Docker container IDs.

Inspect or stop the environment with:

```bash
docker compose ps
docker compose logs --no-color spire-server spire-agent fake-services
docker compose down
```

The isolated stack defaults to host metrics ports `39988` and `39989` so it
can run beside the dual-TDVM profile on `29988`. Override them with
`M3_SERVER_METRICS_PORT` and `M3_AGENT_METRICS_PORT` when required.

Deleting `runtime/` resets the generated keys and SPIRE state. Do this only when
a fresh isolated identity environment is required.
