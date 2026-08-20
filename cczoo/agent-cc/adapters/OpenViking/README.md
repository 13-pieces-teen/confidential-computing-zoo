# OpenViking Broker Sidecar adapter

This adapter launches the official OpenViking Python server without changing
its source code and represents it to SPIRE through a separate Broker Sidecar.

## Runtime boundary

- OpenViking listens on TD Guest loopback port 1933.
- OpenViking does not mount the Workload API or Broker API socket.
- OpenViking does not receive an SVID or private key.
- The launcher passes OpenViking's actual host PID to the Broker Sidecar.
- The Sidecar requests the target identity through the SPIRE Broker API, keeps
  the resulting key material in memory, and publishes mTLS port 1943.
- The Sidecar accepts only
  `spiffe://argus.local/agent/openclaw` and proxies accepted requests to
  OpenViking loopback.

The Sidecar is not configured to restart automatically because a restarted
container must not reuse a stale target PID.

## Prepare OpenViking

Select a pinned OpenViking image and create its normal model/API configuration:

```bash
export OPENVIKING_LUKS_MOUNT_ROOT="<mounted-storage-path>"
export OPENVIKING_LUKS_SUBDIR=openviking
OPENVIKING_HOST_DATA_DIR="${OPENVIKING_LUKS_MOUNT_ROOT}/${OPENVIKING_LUKS_SUBDIR}"
mkdir -p "$OPENVIKING_HOST_DATA_DIR"
cp configs/ov.conf.example "$OPENVIKING_HOST_DATA_DIR/ov.conf"
chmod 700 "$OPENVIKING_HOST_DATA_DIR"
chmod 600 "$OPENVIKING_HOST_DATA_DIR/ov.conf"

export OPENVIKING_VERSION="<tested-version>"
export OPENVIKING_BASE="ghcr.io/volcengine/openviking@sha256:<digest>"
```

## Build, register, and launch

First build the OpenViking and Broker Sidecar images in the TD Guest:

```bash
export OPENVIKING_LAUNCH_ACTION=build
bash scripts/launch_openviking.sh
```

Create the Broker and target Registration Entries from the SPIRE Server host.
For the asymmetric profile:

```bash
bash ../../core/spire/runtime/asymmetric/scripts/register-workloads.sh
```

Use the exact Agent SPIFFE ID printed by that command and launch from the TD
Guest:

```bash
export OPENVIKING_WORKLOAD_API_DIR=/run/argus-spire-v2/openviking
export OPENVIKING_BROKER_API_DIR=/run/argus-spire-v2/openviking-broker
export OPENVIKING_AGENT_SPIFFE_ID='spiffe://argus.local/spire/agent/argus_tdx/<exact-id>'
export OPENVIKING_LAUNCH_ACTION=launch
bash scripts/launch_openviking.sh
```

The launcher submits OpenViking through TC API, waits for the launch result,
extracts the single returned container ID, resolves its host PID, and starts the
Sidecar with that PID.

TC API reloads the default `IMAGE_ID=openviking-cmem` as
`openviking-cmem:latest`. The asymmetric registration script matches that
runtime image ID and separately pins the config digest of the source image.

## Verification

Run these checks on the remote Linux/TDVM environment:

```bash
docker inspect agentcc-openviking-service +  --format '{{.State.Pid}} {{json .Mounts}}'
docker inspect agentcc-openviking-broker-sidecar +  --format '{{json .Config.Cmd}} {{json .Mounts}}'
docker logs agentcc-openviking-broker-sidecar
```

The expected result is:

- no SPIRE socket mount in `agentcc-openviking-service`;
- the Sidecar command references the current OpenViking host PID;
- the Sidecar mounts both SPIRE socket directories;
- the Sidecar log reports that the target identity is ready.

Go unit tests and Linux cross-builds can run in the local checkout. Docker,
Broker UDS permissions, PID namespaces, `pidfd_open`, and end-to-end SPIRE
issuance are remote-only verification items.
