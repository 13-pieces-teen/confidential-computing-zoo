# TDVM and Node Attestation failure fixtures

This directory keeps only the TDVM lifecycle/preflight utilities still used by
the asymmetric evaluation environment and the isolated `argus_tdx` software
failure matrix. The former pre-dual-Agent deployment, endpoint-switching, and
business-path acceptance scripts were removed after the native asymmetric
runtime became authoritative.

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

## Software failure matrix

`test-failures.sh` reuses the isolated stack under `../nodeattestor-mock` and
checks that the custom NodeAttestor fails closed for:

- replayed evidence under a fresh key and challenge;
- Evidence Provider HTTP 503;
- Trustee HTTP 503;
- Trustee timeout;
- Prometheus failure classification.

Run it directly with:

```bash
core/spire/tests/tdvm/test-failures.sh
```

It is also part of `runtime/asymmetric/scripts/remote-test.sh attestation` and
`all`. The matrix uses fake Evidence Provider/Trustee services and is not TDX
hardware security acceptance.
