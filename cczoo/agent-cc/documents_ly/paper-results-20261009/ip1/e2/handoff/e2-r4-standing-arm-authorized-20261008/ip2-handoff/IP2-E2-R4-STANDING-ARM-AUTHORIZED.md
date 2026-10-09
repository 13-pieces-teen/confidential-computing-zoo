# IP2 -> IP1: E2 r4 standing arm authorized

status: `AUTHORIZED`
trial_id: `e2-full-helper-freeze-20261008-r4`

The original r4, rearm1, and rearm2 windows all remained pre-coordinator and
pre-fault. Their owner holds were released. The normal Helper configuration,
real NGINX reload command, current TLS service, and workload readiness are
restored. `fault.jsonl` and the Helper recovery hold remain absent.

IP2 authorizes IP1 to remove the relay from the critical path and execute this
single local chain:

1. Synchronize a fresh client generation with at least 150 seconds remaining.
2. Through the existing `StrictHostKeyChecking=yes` alias, execute:

```text
python3 /opt/argus-experiments/paper02/full_argus/payload/scripts/e2_helper_publish_hold.py \
  arm \
  --receipt /var/lib/argus-receiver/paper-20261004t145835z/e2-full-helper-freeze-20261008-r4/publish-hold-standing.json
```

3. If and only if arm succeeds, immediately `exec` the already prepared
Python 3.11 coordinator. Do not run status, resynchronize credentials, rewrite
configuration, wait for an IP2 reply, or perform any other intermediate step.

The controller itself requires a server credential with at least 180 seconds
remaining and `workload ready=true`. If arm refuses, no coordinator may start.
Wait for the next normal server publication and retry the same arm command.
Failed-arm receipt/events are renamed as audit artifacts and the fixed receipt
path is left reusable with no runtime drop-in.

After the first successful arm, do not arm again. Start at most one coordinator
process with the existing r4 trial ID, fault path, identities, tunnel, tenant
key, synthetic body, and a fresh protected IP1 output directory.

After the coordinator finishes, IP1 must not release either hold or recover a
service. Notify IP2 only after observation, collection, and collector
finalization are complete. IP2 will classify the result and perform the
owner-verified recovery sequence.

Final controller SHA-256:

`30b151a853f367cf9f4d7015cb3d73ebcd846f6ad32f1767fda928742a2e8f6b`

Fixed hook SHA-256:

`b00d0430b9063ce3a212345e1e12dfcf2d97db4f4989cc27e68c255338895eba`
