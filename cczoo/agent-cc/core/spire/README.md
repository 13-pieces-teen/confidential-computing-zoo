# Argus SPIRE Phase 1

This directory deploys a host-level SPIRE Server and Agent for the single-host
Argus Phase 1 topology. It does not change Argus quote generation or policy
evaluation.

## Local prerequisites

- SPIRE 1.15.1 is installed at `/opt/spire` (override with `SPIRE_HOME`).
- Docker is available through `/var/run/docker.sock`.
- Commands are run as root on the Docker host.

The release archive must be verified with
`spire-1.15.1-linux-amd64-musl_sha256sum.txt` before installation.

## Bootstrap and registration

```bash
sudo core/spire/scripts/bootstrap-agent.sh
sudo core/spire/scripts/register-workloads.sh
```

Both commands are idempotent and can be rerun after a host reboot. Runtime state
is kept outside the repository:

- `/var/lib/spire/server`
- `/var/lib/spire/agent`
- `/etc/spire/bootstrap.crt`
- `/run/spire/sockets/agent.sock`
- `/var/log/spire`

SPIRE assigns a join-token Agent ID under
`spiffe://argus.local/spire/agent/join_token/`. The registration script reads the
single attested Agent ID from the Server and uses it as the workload parent. If
more than one Agent is present, set `ARGUS_AGENT_PARENT` explicitly.

The workload entries issue:

- `spiffe://argus.local/agent/openclaw`
- `spiffe://argus.local/service/openviking-cmem`

## Verification

After recreating both workload containers with the SPIRE label, socket, binary,
and environment variable, run:

```bash
sudo core/spire/scripts/verify-svid.sh
```

The script fetches each X.509-SVID from inside its actual workload container,
checks that the identities are distinct, and confirms that an incorrectly
labeled temporary container receives no identity.

After the OpenViking mTLS listener on port 1943 and the OpenClaw local proxy on
port 1934 are running, verify identity issuance and the protected application
path together:

```bash
sudo core/spire/scripts/verify-mtls.sh
```

The application plugin uses `http://127.0.0.1:1934`; the local proxy obtains the
OpenClaw SVID and connects to `127.0.0.1:1943`. The OpenViking proxy requires the
OpenClaw SPIFFE ID and forwards authenticated traffic to the real service on
container-local port 1933. Port 1943 rejects plaintext HTTP.

To observe rotation of the 600-second workload SVID:

```bash
docker exec agentcc-openclaw-sbx-gateway \
  spire-agent api watch -socketPath /run/spire/sockets/agent.sock
```

Health checks:

```bash
/opt/spire/bin/spire-server healthcheck \
  -socketPath /tmp/spire-server/private/api.sock
/opt/spire/bin/spire-agent healthcheck \
  -socketPath /run/spire/sockets/agent.sock
```

The development CA key, join tokens, datastore, Agent keys, and issued private
keys must never be copied into this repository or an image.
