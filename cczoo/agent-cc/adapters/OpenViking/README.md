# OpenViking adapter

The current Workload Attestation deployment uses TC API's
`nginx-spiffe-helper-v1` launch profile, the shared TDX Evidence Provider,
SPIRE WorkloadAttestor, Broker-aware SPIFFE Helper, and NGINX. See the
[current design](../../documents_ly/Argus-OpenViking-NGINX-SPIFFE-Helper-Workload-Attestation-Workflow-CN.md),
[runbook](../../core/spire/workload/README.md), and
[validation records](../../core/spire/workload/VALIDATION.md).

## Legacy Ingress Broker adapter

The older adapter in this directory launches the official OpenViking server without changing its
source code and represents its real process to SPIRE through a separate
Ingress Broker.

### Historical runtime boundary

- OpenViking listens on TD Guest loopback port 1933.
- OpenViking does not mount the Workload API or Broker API socket.
- OpenViking does not receive an SVID or private key.
- The launcher passes OpenViking's actual host PID to the Ingress Broker.
- The Broker obtains the target identity through the SPIRE Broker API, keeps
  the key material in memory, and publishes mTLS port 1943.
- The Broker accepts only `spiffe://argus.local/agent/openclaw` and forwards
  authenticated requests to OpenViking loopback.

The Broker is not configured to restart automatically because a restarted
container must not reuse a stale target PID.

### Deployment status

This directory contains the adapter implementation and launcher only; it is
not an integrated SPIRE deployment and does not create Registration Entries on
its own. It is not used by the current Helper + NGINX deployment. Its historical
architecture and tests are kept in the [documentation archive](../../documents_ly/archive/pre-workload-implementation/README.md).

### Legacy adapter verification

The adapter unit tests check its local contracts. Its historical integration
criteria were:

- OpenViking has no SPIRE or SVID mount;
- the Ingress Broker references the current OpenViking host PID;
- only the Broker mounts the Workload API and Broker API sockets;
- the Broker accepts the exact OpenClaw SPIFFE ID and rejects missing or wrong
  client identities;
- OpenViking exit causes the Broker to exit through pidfd monitoring.

Go unit tests and Linux cross-builds can run in a local checkout. Docker,
Broker UDS permissions, PID namespaces, `pidfd_open`, and end-to-end SPIRE
issuance require Linux/TDVM evidence. Current acceptance uses the runbook linked above.
