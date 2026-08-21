# OpenViking Ingress Broker adapter

This adapter launches the official OpenViking server without changing its
source code and represents its real process to SPIRE through a separate
Ingress Broker.

## Runtime boundary

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

## Maintained deployment

The only maintained integrated deployment is the
[dual-TDVM runtime](../../core/spire/runtime/dual-tdvm/README.md). Its
`prepare.sh` builds the official OpenViking runtime image and Ingress Broker;
`manage-guest.sh` loads the images, launches OpenViking through the existing
TDVM TC API, registers the observed runtime digest, and then starts the Broker
with the returned process PID.

`scripts/launch_openviking.sh` is the adapter launcher used by that profile.
It is not a second deployment profile and does not create Registration Entries
on its own.

## Verification

The dual-TDVM `verify.sh` checks that:

- OpenViking has no SPIRE or SVID mount;
- the Ingress Broker references the current OpenViking host PID;
- only the Broker mounts the Workload API and Broker API sockets;
- the Broker accepts the exact OpenClaw SPIFFE ID and rejects missing or wrong
  client identities;
- OpenViking exit causes the Broker to exit through pidfd monitoring.

Go unit tests and Linux cross-builds can run in a local checkout. Docker,
Broker UDS permissions, PID namespaces, `pidfd_open`, and end-to-end SPIRE
issuance remain Linux/TDVM verification items.
