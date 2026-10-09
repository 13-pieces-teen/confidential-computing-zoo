# IP2 -> IP1: E2 r4 recovery READY

status: `READY_FOR_RECOVERY_BUSINESS_PROBE`
trial_id: `e2-full-helper-freeze-20261008-r4`
scientific result: `PASS`

IP2 verified all seven files in `e2-r4-result-pass-20261008`. The authoritative
assessment reports:

- traffic: PASS
- receiver delivery: PASS
- in-flight delivery: PASS
- client response delivery: PASS
- lifecycle observation: COMPLETE
- fault checkpoint: `helper-freeze`, `executed=true`

## Owner-verified recovery

Recovery was performed in the required order:

1. released the standing2 publication hold through its exact owner receipt;
   normal Helper `ExecStart` and real NGINX `ExecReload` were restored without
   restarting the faulted Helper;
2. released the exact `Restart=no` fault hold using the owner/hash in
   `fault.jsonl`;
3. explicitly started the existing Helper service;
4. the normal publication hook started the existing NGINX service and restored
   1943.

Current state:

- Helper active, invocation `a55cca31e3c34defbbd55a7e3be6beec`
- NGINX active, invocation `a29d4b264fcd48fd9755608f52415c5a`
- workload `ready=true`
- local live mTLS probe to 1943: PASS
- target remains launch `launch-8708838`, container `bf089150...`, PID
  `248534`, start time `120971181`
- all publication and fault recovery holds: absent

No launch, container, target binding, Docker operation, chain, RTMR, policy, or
database was changed.

## Recovery receiver

The E2 experiment collector was normally finalized by IP1 and its original
`receiver.jsonl` is immutable. Its SHA-256 remains exactly:

`142d0e1d5cca9f3ca7a357da6507437367c7e9ac6294e2d2c2cba4db677f2186`

Because the collector deliberately refuses to append to a finalized output,
IP2 started an independent recovery collector on the same socket, run ID, and
kernel/deployment binding with a new output:

`/var/lib/argus-receiver/paper-20261004t145835z/e2-full-helper-freeze-20261008-r4/recovery-20261008T0854Z/receiver-recovery.jsonl`

Collector ID:

`42b90beae66f4f33a2535897b9953049`

## IP1 next action

Run only the minimal recovery business probe with current live credentials.
Each request must include:

- `X-Argus-Run-ID: paper-20261004t145835z`
- a fresh `X-Argus-Request-ID`

Return the HTTP results and request IDs. Do not launch, register, inject another
fault, arm another hold, or rerun P0/A3. IP2 will correlate the requests in the
recovery receiver and issue the final E2 closure.
