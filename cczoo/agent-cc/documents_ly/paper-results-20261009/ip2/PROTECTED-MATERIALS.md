# Protected and unavailable materials

The server originals remain unchanged. The following are intentionally not
published:

| Material | Reason | Public replacement / reproducibility boundary |
|---|---|---|
| API keys, OIDC tokens, user-key responses, credential caches | Active or historical credentials | No values exported. Deployment logs retain only field names and token lifetime metadata. |
| Private keys and E1 ephemeral fixture trust/signing material | Key material | `e1/offline-rules/summary.json` and per-case `result.json` preserve decisions and original request hashes. Re-signing or full cryptographic fixture replay requires protected originals. |
| Owner records/material and recovery hold files | Privileged recovery authorization | E4 `fault.jsonl`/`recovery.jsonl` preserve owner identifiers, hold hashes, execution times, and outcome, but not owner material. |
| TruCon/Docktap/runtime DB, WAL, SHM and chain state | Mutable operational state and credentials | Receipt/correlation JSON and hashes are exported; database-level replay is unavailable. |
| Container images, VM/disk images, tmpfs state | Large operational artifacts and potentially sensitive state | Image configuration digest, boot ID, policy ID, and runtime versions are exported. |
| Full paper02 shared receiver journal (122,493,797 bytes) | Contains unrelated campaign traffic and is not needed for the selected E3/E5 closure | `e3-e5/receiver-request-excerpt.jsonl` contains the six exact request records; its context records source SHA-256 `142d0e...2186`. |
| E2 full experiment and recovery receiver journals | Large protected originals containing unrelated records | E2 result/timeline/source hashes and closure are exported. Full record-by-record recomputation requires protected originals. |
| E1 archived Rekor transport and full signed fixture requests | Signed originals were not modified or selectively redacted | Only unchanged public result files are exported. Full signature verification requires the protected package. |
| E4 secret initialization seeds | Deliberately retained only in protected configs | Public config summaries retain seed hashes and state that the seed itself is protected. |
| Run 6 rejected empty-window correlation draft | Superseded draft was not retained as an authoritative original | The correction narrative, unchanged receiver hash, accepted correlation, strict-UTC basis, and nonzero/2507 rejection rule are exported. |

## Directly reproducible offline

- Public-file checksum validation.
- E4 full receiver SHA validation and phase/request/coverage recount for all six
  runs.
- E4 paired-seed tables and figures.
- E1/E2 figure regeneration from exported derived data.
- E1 offline-rule result-table aggregation.

## Requires protected originals or live infrastructure

- Full E1 cryptographic fixture replay and real admission replay.
- E2 record-level recomputation from complete receiver/lifecycle/trace inputs.
- E3/E5 recomputation over the entire shared receiver journal.
- Any new online E1 negative injection, E3 renewal campaign, or E5 formal
  load/resource campaign. These remain `NOT_RUN`; this export does not create
  substitute results.
