# Independent Gateways on one client TDVM

This profile uses one existing x509pop SPIRE Agent and one shared trusted root
publisher identity. Each Gateway has its own client SPIFFE ID, numeric UID/GID,
supplementary credential reader GID, instance label, data, configuration, target
registration, credential directory and publisher unit. It does **not** provide
independent TDVMs or direct Agent–Agent cooperation. Client TDX remote attestation
remains `NOT_RUN`.

## Prepare and deploy

Start from `config/fleet.example.json`. Fill the immutable image/config/helper
digests and node configuration from the actual deployment. Names are lowercase
letters/digits/hyphens, maximum 23 characters. UID, GID, reader GID, name, container
and client ID collisions are rejected across the entire fleet before selecting
an instance. The optional `node.server_spiffe_id` defaults to the existing
`spiffe://argus.local/service/openviking-cmem` and supports isolated experiment IDs.
Never vary the helper ID per instance: an identical root binary selector would
match every such identity.

From this directory, render the shared node and each instance:

```sh
python3 deploy.py render-node --config /root/fleet.json --output /root/rendered-node
python3 deploy.py render --config /root/fleet.json --instance alice --output /root/rendered-alice
python3 deploy.py render --config /root/fleet.json --instance bob --output /root/rendered-bob
python3 deploy.py render --config /root/fleet.json --instance carol --output /root/rendered-carol
```

The node output contains only Agent configuration/unit, x509pop fragment and node
manifest. Instance output does not contain any shared Agent unit/configuration.
On the Guest, stage the existing node certificate/key/bootstrap bundle under
`/etc/argus-openclaw`; do not generate a new CA or erase Agent state. Install the
shared node explicitly with `guest-install-node --config ... --source ...`.
Existing identical files are retained; differing node files require a reviewed
migration, not implicit replacement. Start the shared Agent once through systemd.
Stopping the shared Agent is a fleet-wide event and is outside per-instance stop.

For each instance `NAME`, stage its root-owned mode-0600
`/etc/argus-openclaw/instances/NAME/gateway.env`. Use a different OpenViking **user**
API key, a common experiment account and a distinct user. Also persist these
variables in that instance's environment:

```text
OPENVIKING_API_KEY=<this user's key>
OPENVIKING_ACCOUNT_ID=<shared experiment account>
OPENVIKING_USER_ID=<this user's ID>
OPENVIKING_RECALL_RESOURCES=false
```

Keep model credentials in the protected environment. Install with
`guest-install --config /root/fleet.json --instance NAME --source /root/rendered-NAME`.
This command only installs that instance and does not start or replace the shared
Agent. Start the Gateway using its installed `compose.json`, install the locked
2026.6.18/argus.3 plugin by the existing workflow, and merge the rendered
`private-memory.fragment.json` into the instance's own OpenClaw configuration.
Preserve the model, plugin installation and contextEngine settings. The pinned
plugin's actual private settings are `recallResources=false` and
`recallTargetTypes=["user"]`; it has no `includeResources` deployment option.
The fleet preflight rejects resource or account-shared agent recall targets.

Existing shell tools select a Gateway through `OPENCLAW_CONTAINER` and
`OPENCLAW_USER` (the numeric `UID:GID` pair); their in-container config paths stay the same.
Restart a changed Gateway explicitly, find its actual process PID using `docker
top`, then on IP1 apply/check precise Entries with `--instance NAME`, and on the
Guest run `guest-register --instance NAME --pid PID`. Every command also takes
`--config /root/fleet.json`. `guest-status` and `guest-stop` use the same selection.
`guest-stop` clears only that publisher/registration and does not stop the Gateway,
another publisher, or the shared Agent. Restore by registering the actual current
PID again. Do not use a Docker init PID or reuse a PID after a restart.

The server must permit the exact set of selected client IDs through
`allowed_client_ids`. Existing overlapping Entries are refused, including a
second identity whose selectors also match the selected Gateway. Never introduce
wildcard authorization to make a negative test pass. Runtime registration rejects
extra/sibling/socket mounts, privilege/host PID/host network, added capabilities
and additional reader groups. The generated compose grants none of them.

## Run the business and isolation acceptance

Copy `config/fleet-business.example.json` and point `deployment_config` at the
actual fleet. Each `api_key_file` is an absolute root-owned mode-0600 file with
only that user's key. Keys are checked for distinct values, not only filenames.
The user scope is verified against the persistent container/plugin configuration,
the pinned OpenViking `/health` resolved identity, and mandatory-auth
`/api/v1/system/status` request context. HTTP 200 alone is not identity evidence:
the pinned `/health` can return 200 for an invalid key with no identity fields.

```sh
sudo python3 fleet_business.py run --config /root/fleet-business.json --output /root/evidence/fleet-001
sudo python3 fleet_business.py resume --config /root/fleet-business.json --output /root/evidence/fleet-001
sudo python3 fleet_business.py isolation --config /root/fleet-business.json --output /root/evidence/fleet-001
```

`run` requires a new evidence directory. `resume` never repeats the initial
Gateway write: a known operation continues through the existing E2E `--resume`;
an interrupted operation without processing state remains `INITIAL_WRITE_UNKNOWN`.
Unknown cross-user question submissions also are never automatically repeated.
Safe API reads/searches can be repeated. Changing either configuration prevents
resume. A process lock protects each run. Timeouts terminate the owned local
polling process group; an unanswered remote Gateway operation remains unknown.

Optional `--instance NAME` runs only that business flow; fleet isolation remains
`NOT_RUN`. The experiment harness may supply `ARGUS_RUN_ID` and
`ARGUS_OPERATION_ID`; resume checks both. `result.json` contains `completed`,
`phase`, configuration digest, run/operation IDs, per-instance results and isolation
rows. A file with `completed=false` is an interrupted operation, not a completion
receipt. Each instance has independent random facts, sessions, tasks and original
six-stage E2E evidence. A run ID in a client request is correlation, not identity.
With `ARGUS_SEED`, synthetic fact contents are deterministic from the fixed seed
and logical client position in the fleet array, independent of arm-specific
names. Session/project markers remain unique to the run. Preserve the array order
across paired groups and the seed on resume. Without a seed, the original random
fact behavior is retained. The initial question never contains the expected fact.

For each established owner memory, the suite finds an actual leaf URI and first
proves that the owner can read its random fact. Another user's exact read must be
403/404; its targeted search must reject or omit the owner's URI and fact. The
same checks run with forged user headers, and the resolved mandatory request
context must remain the caller (or reject). A genuinely new Gateway session asks
only about the other user's marker and must answer `UNKNOWN`. Missing/invalid
keys are checked through authenticated APIs, separately from model responses.

Configure the two edge tests explicitly in the business JSON:

```json
{
  "direct_backend_origin": "http://ACTUAL_BACKEND_ADDRESS:1933",
  "wrong_identity": {
    "cert_file": "/root/negative-client/svid.pem",
    "key_file": "/root/negative-client/key.pem",
    "bundle_file": "/root/negative-client/bundle.pem",
    "server_spiffe_id": "spiffe://argus.local/service/openviking-cmem"
  }
}
```

These fields extend the example, not replace it. Use a valid certificate from
the approved trust domain whose SPIFFE ID is absent from the allowlist. The probe
verifies the server's exact URI identity before sending the valid business key.
HTTP 403 proves this authorized-channel denial; a TLS/network failure is
`UNKNOWN`, not proof of the intended identity policy. For the backend test,
connection refusal is a negative observation from this client location, any HTTP
response shows a reachable bypass and is `FAIL`, and timeout/DNS/TLS ambiguity is
`UNKNOWN`. Missing edge configuration produces `NOT_RUN`, never overall `PASS`.

This is deliberately dual authorization: Argus controls admitted client/service
instances; OpenViking resolves the business key's user. No one-to-one key/SPIFFE
mapping is added or claimed. A permitted Gateway holding another valid user key
can act as that key's user. Shared node compromise is outside per-Gateway isolation.

## Local evidence and remaining remote work

Run `python3 -m unittest discover -s test -p 'test_*.py' -v` and the existing
`test-client.sh`. To execute the exact upstream 0.4.8 auth/access predicates:

```sh
python3 verify_openviking_contract.py --cache /tmp/argus-openviking-contract --fetch
```

The verifier hashes exact source revision
`07113f81e0edaebaacdd23ab138087b06fe871ab` against
`config/openviking-contract.lock.json`. It runs the original key/header resolution
and private owner predicate with a controlled key store, excluded OAuth path and
normalized URI ownership fixtures. This is a source-level contract test, not
HTTP/storage integration or actual multi-Gateway evidence.

Local tests cover collision rejection, disjoint selectors, shared helper identity,
mount/group isolation, selected stop, scope proof, inconclusive denials, resume
without duplicate questions and incomplete-result aggregation. The Linux UID/GID
test actually drops to one reader UID/group, reads its own synthetic key and gets
PermissionError for a sibling key. Real Guest deployment permissions, three
running Gateways, model-backed memory isolation, live wrong
identity and backend probes remain `NOT_RUN` until executed remotely. Keep original
evidence in a protected directory, return only hashes/paths and sanitized results,
and distinguish client-local failures from shared-service/node failures.
