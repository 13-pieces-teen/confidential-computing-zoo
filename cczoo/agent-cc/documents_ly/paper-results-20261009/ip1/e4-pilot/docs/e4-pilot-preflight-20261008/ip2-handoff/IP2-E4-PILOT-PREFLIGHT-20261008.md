# IP2 E4 seed-1 pilot server preflight

## Recovery/fault run: READY

- Run: `e4p1-d6771e94005b51df0fd60a23ba981087`
- New normal TC API launch: `launch-99533a9`
- Container: `1a20331937bef36bb213281396c1f3bdce9d899a09d0fbd14089d37f57de186d`
- Listener PID: `512035`
- Target and receiver binding are tied to the exact run ID.
- Provider, Agent, AuthZ, Helper, NGINX and the independent receiver are active.
- Workload readiness is true and port 1943 is listening.
- Receiver has no `receiver_gap`; after one expected UNKNOWN interval covering
  the pre-collector tail, watermarks are continuously COMPLETE.
- `fault.jsonl` and `recovery.jsonl` do not exist.
- The recovery API key is delivered separately as root:root 0600 and is
  excluded from SHA256SUMS.

The old paper02 container was stopped through Docktap. Its stop mutation
`mutation-1feb8967145a48148a2e5226caf81c62` is CONFIRMED. The new launch
record is also CONFIRMED at chain sequence 5. There is no Docker INFLIGHT.

The initial zero-touch continuation encountered two deterministic pre-run
startup races: register was attempted before the new listener became ready,
and the rendered Agent source initially pointed at the generic data directory.
No launch was replayed. Registration was continued against the completed
launch after container health, and the Agent source is now permanently pinned
to `/etc/spire/argus-poc/paper02-agent.conf`, which preserves the existing
`paper02-data` Node material across future renders.

## Corrected controls

`E4-PILOT-CONTROL-OVERRIDES.json` replaces the proposed recovery shell chain.
Fault still uses the existing owner-marked `remote_acceptance.py fault`.
Recovery calls one fixed server program, `e4_owner_recovery.py`, which:

1. validates the matching executed helper-freeze checkpoint and owner/hash;
2. removes only that run's recovery hold;
3. explicitly starts the Helper;
4. waits for Helper, NGINX, workload readiness and port 1943;
5. writes an O_EXCL durable recovery receipt.

IP1 must install these exact control arrays into the protected recovery config
before `continuous.py preflight/run`.

## Healthy/no-fault run: STAGED, NOT READY

The user and protected key are prepared, and the isolated receiver directory
and unit definition exist. It must not run yet. The audit run ID is fixed in
the target middleware at normal launch, so two collectors cannot be made valid
against one target by merely switching binding files.

After the recovery run is complete and its collector is finalized, IP2 will
perform the next manifest-ordered normal launch/register/start for
`e4p1-3d00004a2a939c240ce5883134fbdbae`, activate its receiver and issue a
separate READY update. IP1 must run only the recovery item now.

No E4 task, fault, recovery control or model request has yet been executed.
