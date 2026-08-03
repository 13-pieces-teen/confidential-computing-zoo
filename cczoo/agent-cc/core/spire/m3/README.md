# M3 hardware-free integration

This environment runs SPIRE Server and Agent 1.15.1 with the external `argus_tdx`
NodeAttestor plugins. A local fake Evidence Provider produces binding-aware evidence,
and an mTLS fake Trustee independently verifies the request digest, attestation-key
target, and REPORTDATA before returning verified claims.

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
cd core/spire/m3
./test.sh
```

The test builds all three static binaries, generates short-lived M3 certificates,
validates both SPIRE configurations, completes Node Attestation, and checks the
workload identity matrix. The positive workload must match its attested parent,
role label, immutable image ID, and image config digest. Wrong labels and an
unauthorized image config digest must receive no identity.

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

Deleting `runtime/` resets the generated keys and SPIRE state. Do this only when a
fresh Node Attestation identity is required.
