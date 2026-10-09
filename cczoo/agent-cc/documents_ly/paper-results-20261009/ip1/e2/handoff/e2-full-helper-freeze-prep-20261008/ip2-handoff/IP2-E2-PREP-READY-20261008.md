# IP2 -> IP1: E2 Full/helper-freeze preparation

status: READY_FOR_IP1_FAULT_TRIAL
trial_id: `e2-full-helper-freeze-20261008-r1`
audit run_id: `paper-20261004t145835z`
variant: `full_argus`
event: `helper-freeze`

IP2 completed only the E2 preparation step. No fault, recovery hold, release,
service restart, Docker operation, chain operation, or RTMR operation has been
performed.

## Fixed deployment

- launch: `launch-8708838`
- container:
  `bf089150f38ad2298a1bb88ac6161ec1ba2842156ce73b90c7c48923d43e533c`
- host PID: `248534`
- boot:
  `c379b316-287e-4c6b-821b-63ae5a51c45e`
- target:
  `spiffe://argus.local/service/openviking-cmem/experiment/paper02/full`
- client:
  `spiffe://argus.local/agent/openclaw/experiment/paper02/full`

The workload, five paper02 units, container, and independent receiver are
healthy. The original backend answered `GET /health` with HTTP 200 from the
checked target network namespace.

The Helper has the required Full-arm watchdog:

- `WatchdogSec=5s`
- `WatchdogSignal=SIGKILL`
- `Restart=on-failure`
- `ARGUS_REQUIRE_SYSTEMD_WATCHDOG=1`

The hold drop-in does not exist, and all dedicated output paths are fresh.

## Run ID constraint

The receiver middleware and collector were fixed at launch to
`paper-20261004t145835z`. E2 must use that exact protocol run ID for correlated
in-flight evidence. The unique trial label and output directory are
`e2-full-helper-freeze-20261008-r1`. Changing the protocol run ID would require
replacing or restarting the admitted workload and is intentionally not done.

## Coordinator fields

Use `E2-SERVER-FIELDS.json` when building the IP1 `fault_trial.py` config.
IP1 must supply its existing configured SSH alias, live client credentials,
current tenant user key environment variable, and synthetic search body.

The SSH alias must already resolve to `root@172.31.28.53:22` with strict host
key checking. Expected ED25519 host-key fingerprint:

`SHA256:n3JhX4RnuA3TFpjXfCyAI2WQlCGEDOVLpv7l19FF7Yo`

Clock synchronization is healthy. The frozen uncertainty is 20 ms; current
chrony root dispersion was approximately 16.3 ms.

IP1 should run exactly one coordinator operation:

```text
python3 experiments/argus/fault_trial.py run \
  --config <protected-e2-config.json> \
  --output <protected-e2-ip1-output>
```

Do not invoke the server fault command manually. If interrupted after an
unknown stage, use `fault_trial.py resume` on the same output; do not inject a
second fault.

## IP2 evidence and recovery boundary

Prepared server evidence directory:

`/var/lib/argus-receiver/paper-20261004t145835z/e2-full-helper-freeze-20261008-r1/`

Preflight archive:

`/root/argus-epoch-archives/recovery-check-20261007-20261007T072828Z/runtime/e2-full-helper-freeze-20261008-r1/`

IP2 will preserve the fault state after injection. Only after IP1 explicitly
reports that observation, collection, and collector finalization are complete
may IP2:

1. run `remote_acceptance.py release` using this run's `fault.jsonl`;
2. confirm the owned hold file was removed;
3. explicitly start the existing Helper unit;
4. verify current readiness and let IP1 perform the recovery business probe.

IP2 will not pre-arm a recovery timer or perform an independent SIGSTOP.
