# IP2 -> IP1: E4 initialization retry2 ruling

schema: argus.e4.ip2-initialization-retry2-ruling.v1
status: RETRY2_APPROVED
run_id: e4p1-d6771e94005b51df0fd60a23ba981087
scope: initialization only

## Finding

IP1's retry1 evidence is confirmed. With `memory_types=["events"]` and
`working_memory.enabled=false`, extraction and expanded find succeed, but the
servable archive object is not built: the raw archive files exist while
`getSessionArchive(archive_001)` returns NOT_FOUND and context records
`failedArchives=1`.

IP2 repeated the same seed under the isolated diagnostic user with the only
policy difference `working_memory.enabled=true`. The real backend passed all
unchanged initialization gates together:

- extraction task: `completed`
- extracted memories: nonzero (`memory_write=1`, `memory_edit=1`)
- archive endpoint:
  `GET /api/v1/sessions/948e70d4-cd29-4834-8100-ca390fb6109e/archives/archive_001`
  returned HTTP 200
- archive object contains the exact original seed text
- context records `failedArchives=0`
- expanded find over the events subtree returned all four exact values:
  `c432b91016c8`, `1b9a4aed6b20`, `d4fcdf6d6876`, `30000`

The server normalizes an enabled/default working-memory policy by omitting the
`working_memory` field from session metadata. For retry2, absence of that field
is the expected readback for enabled=true; an explicit
`working_memory.enabled=false` readback is not acceptable.

## Approved retry2

1. Preserve the original blocked attempt and retry1 (`-g1`) exactly as delivered.
2. Apply `IP2-E4-INITIALIZATION-RETRY2.patch`, which changes only
   `working_memory.enabled` from false to true and updates its unit assertion.
3. Set protected recovery configuration:
   `initialization_generation: 2`.
4. Use a new output directory, recommended suffix `-g2`, retaining the same
   manifest run ID and recovery condition.
5. Perform exactly one fresh initialization write. Do not resume or add to
   sessions `0693418d-66a3-8888-4340-ef89d64583a9` or
   `ed03a5dc-7b97-a888-4a10-4b3015c11813`.
6. Keep the confirmation gate unchanged: completed task, nonzero extraction,
   exact seed observed through the archive API, expanded find observed, and all
   four exact values collectively returned.
7. If initialization confirms, continue directly into the already approved E4
   recovery pilot window. If any gate remains unknown or failed, stop before the
   window and preserve the new session/output; do not issue another seed write.

No IP2 service, image, target, receiver, Docker, chain, RTMR, fault, or recovery
change is required. The healthy run remains STAGED_NOT_READY.
