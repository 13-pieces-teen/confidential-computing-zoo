# Durable lifecycle delivery

The production Docktap launcher always supplies `TruConCommitter`. For `create`,
`start`, `stop`, and `rm`, the proxy must receive a durable mutation reservation
from TruCon **before** sending the request to Docker. If the service/database or
the new endpoint is unavailable, the operation is blocked with 503. Upgrade
TruCon before Docktap; an older TruCon does not silently bypass the requirement.

This uses the existing internal UDS/HTTP transport and the existing SQLite
database. It does not introduce a deployment mode or change Docker operator
permissions. Administrators must use these supported operations through Docktap
for the recorded lifecycle guarantee. `pull`/`build` retain their prior delivery
path; `restart`, `kill`, `pause`, `update`, `exec`, and direct Docker calls are not
covered by this fence or by complete lifecycle recording.

## State and ordering

| Durable state | Meaning | Restart behavior |
| --- | --- | --- |
| `INFLIGHT` | Reservation acknowledged, or acknowledgement lost; Docker may or may not have run | Do not replay Docker, infer success/failure, expire, or release the fence |
| `RESULT_READY` | Complete framed HTTP response observed; required operation/image/container/status fields saved | Resume signing and sequencing; no Docker replay |
| `SUBMITTED` | Exact signed submission accepted as an associated sequencer record | Wait for immutable-log confirmation; sequencer daemon resumes its existing retry path |
| `CONFIRMED` | The associated record has a nonempty log reference and `CONFIRMED` status | Fence released in the same SQLite transaction as record confirmation |

The outbox has no automatic TTL. A sequence commit intent still has its existing
TTL: if it expires before acceptance, the saved result can be signed under a new
contract, after proving that its old contract expired and no record was accepted.
The mutation ID remains the commit idempotency key. An active or accepted signed
submission cannot be replaced. Signed bytes are saved before `/commit`; a lost
commit reply therefore retries those exact bytes or observes `SUBMITTED` after
acceptance. Acceptance is not confirmation.

The successful Docker response is returned only after the complete operation
result is durably saved. Known non-success HTTP outcomes, including HTTP 400,
are also saved and submitted as failed events. A missing/truncated/ambiguous
response leaves `INFLIGHT`. If result persistence acknowledgement is lost, the
caller gets 503 even if the result was saved or Docker succeeded. Do not blindly
retry the Docker request after any uncertain outcome. The current lifecycle
response validator accepts fixed-length responses and bodyless 204/304; it
conservatively treats unknown-length/chunked results as unknown.

Only reconstruction/signing fields are stored: operation ID/time/engine and
operation type/path/method, image/container identifiers, and parsed result status.
No raw HTTP request, Authorization header, identity token, environment or command
text is stored. Exact signed bundles and owner authorization are retained for
commit recovery; these are evidence, not OIDC credentials.

## Admission gate and concurrency

Both forms of `/chain-state` return HTTP 409 while any mutation in the default
chain is unresolved. Mutation reservation, result/submission updates, history
reads and commits share the existing sequencer lock. A history read linearizes
before a new reservation can be acknowledged; it cannot check for no fence and
then read an old head after an acknowledged mutation reservation. Confirmation
and fence release share a SQLite transaction. Connections use WAL and explicitly
set `synchronous=FULL`.

The global default chain means an unknown operation can block new admission for
all targets. This is a deliberate fail-closed availability cost. The workload
Provider's pre/post `chain-state?include_history=true` snapshots receive 409, so
new workload evidence/admission fails. Node Quote generation does not consult
this gate. Existing workload credentials, active proxies, ongoing connections,
and already admitted traffic are not synchronously revoked by this change.

## Recovery boundary and operator action

The default queue database is on `/dev/shm` (tmpfs). WAL/FULL and these records
cover **process restarts in the same guest boot**, not VM reboot or power-loss
persistence. Moving it to host-controlled storage would require a separate
authenticated-storage/rollback design and is not part of this change.

`RESULT_READY` and `SUBMITTED` recover automatically as described above.
`INFLIGHT` cannot be resolved safely from its age, current Docker state alone,
or a successful client retry. It also includes the conservative case where
reservation acknowledgement was lost or the process died before forwarding.
There is no API to delete/force-confirm an unresolved fence. Trusted operators
must investigate the exact mutation and relevant Docker/operation evidence;
if no defensible outcome can be established, provision a fresh guest/chain and
perform normal fresh launch/admission. Do not delete/reset the current database,
replay Docker blindly, or retain the old launch qualification as a recovery
shortcut. Same-boot manual outcome recovery requires trustworthy evidence of
that exact operation; this release provides no speculative reconciliation tool.

## Local validation

`tests/docktap/test_mutation_delivery.py` crosses the real proxy, SQLite outbox
handlers, real sequencer reserve/commit, owner signing and associated record
confirmation with fake Docker/RTMR/Rekor. It covers crash cuts, unknown results,
lost write/commit acknowledgements, exact idempotent recovery, intent expiry,
immutable result/submission binding, and a concurrent history/reserve race.
The Windows tests substitute only unused native process-lock/UDS startup
imports; no native socket or real TDX acceptance is claimed.
