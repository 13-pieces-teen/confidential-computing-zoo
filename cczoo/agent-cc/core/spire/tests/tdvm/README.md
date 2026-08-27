# TDVM and Node Attestation failure fixtures

This directory keeps only the TDVM lifecycle/preflight utilities still used by
the dual-TDVM runtime.
Former pre-dual-Agent deployment, endpoint-switching, and standalone
business-path acceptance scripts are not maintained here.

## TD VM lifecycle

Prepare a private overlay, inject only the SSH public key, and start the TD VM:

```bash
export TDVM_BASE_IMAGE=/path/to/ubuntu-tdx-base.qcow2
export TDVM_OVERLAY_IMAGE=/path/to/argus-openviking-tdx.qcow2
export TDVM_SSH_PUBLIC_KEY=$HOME/.ssh/id_rsa.pub
core/spire/tests/tdvm/tdvm.sh prepare
core/spire/tests/tdvm/tdvm.sh start
core/spire/tests/tdvm/tdvm.sh status
```

`tdvm.sh` does not delete the base image or overlay. It can adopt an already
running QEMU process with the same `TDVM_NAME`; `stop` sends SIGTERM only to
that process and leaves both disk images intact.

The mTLS guest port is forwarded twice: to host loopback for host-side checks,
and to the Docker default bridge gateway for the OpenClaw container. This keeps
port 1943 off external host interfaces. Override the latter only when the
Docker daemon uses another host-gateway address:

```bash
export TDVM_DOCKER_MTLS_BIND_ADDRESS=<docker-host-gateway-address>
```

## Host and guest preflight

Run the Host check before launching a TD VM:

```bash
core/spire/tests/tdvm/check-tdx-host.sh
```

It checks KVM TDX enablement, QEMU `tdx-guest` support, TDVF, and the Host QGS
socket. The Host is not expected to expose `/dev/tdx_guest`.

Run the Guest check inside the TD VM before hardware acceptance:

```bash
core/spire/tests/tdvm/check-tdx-guest.sh
```

The Guest check requires the TD Guest device, loaded Guest module, and a
writable TSM configfs report root. These preflights establish platform
availability; they do not by themselves prove successful production remote
attestation.
