# IP2 -> IP1: E2 r4 standing2 authorized

status: `AUTHORIZED`
authorization: `standing2`
trial_id: `e2-full-helper-freeze-20261008-r4`

IP2 verified the standing1 missed-window package and its four checksums. The
standing1 receipt contained one successful arm and only suppressed publication
events. No coordinator process, client output, `fault.jsonl`, or Helper recovery
hold existed.

IP2 released standing1 through its exact owner receipt. The normal Helper
configuration and real NGINX reload command are restored, 1943 serves the
current valid credential, and workload readiness is true.

IP2 now authorizes one replacement window using this new receipt:

```text
/var/lib/argus-receiver/paper-20261004t145835z/e2-full-helper-freeze-20261008-r4/publish-hold-standing2.json
```

The exact remote arm command is:

```text
python3 /opt/argus-experiments/paper02/full_argus/payload/scripts/e2_helper_publish_hold.py \
  arm \
  --receipt /var/lib/argus-receiver/paper-20261004t145835z/e2-full-helper-freeze-20261008-r4/publish-hold-standing2.json
```

Use the corrected, pre-validated IP1 chain script:

1. synchronize a client credential with at least 150 seconds remaining;
2. peek the standing2 receipt and refuse re-entry if it already exists;
3. execute the arm command through the existing strict SSH alias;
4. accept success only when the returned receipt has `state == "armed"`;
5. immediately perform the final local guards for a nonexistent output
   directory and no `fault_trial[.]py run` process;
6. immediately `exec` the prepared Python 3.11 coordinator.

No relay, status call, credential synchronization, configuration parsing, or
other step is allowed between successful arm and coordinator exec.

If arm refuses before success, no coordinator may start. The controller
restores the normal stack, archives failed-arm evidence, and leaves the
standing2 receipt path reusable after the next normal publication.

After standing2 first returns `state=armed`, do not issue another arm. If the
local final guard prevents coordinator exec, notify IP2 for owner release; do
not start the coordinator later in the expired window.

The trial ID, fault path, receiver, identities, tenant key, tunnel, synthetic
request body, and single coordinator/fault budgets are unchanged. After the
coordinator finishes, IP1 must not release or recover the server; notify IP2
after observation, collection, and collector finalization.
