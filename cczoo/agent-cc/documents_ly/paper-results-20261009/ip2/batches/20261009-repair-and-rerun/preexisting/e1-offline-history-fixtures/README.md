# E1 offline production-history fixtures

These seven cases are **offline production LogVerifier history-subverifier
fixtures**, not seven online attacks and not fresh admission decisions.

Each case includes the original verifier request, fixed archived Rekor
transport, ephemeral public initialization/Rekor keys, trust configuration, and
result. No private key is exported. `trust.offline.json` only relocates public
key paths for local use; `trust.original.json` is retained byte-for-byte.

Quote, REPORTDATA, challenge freshness, current PID/starttime, and full
configuration policy remain outside this fixture boundary. Therefore
`config_mismatch` and `old_evidence` ALLOW results cannot be reported as online
admission ALLOW.
