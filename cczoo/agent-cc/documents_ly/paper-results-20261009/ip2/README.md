# Argus paper02 IP2 results export

This directory is a public, offline-analysis export of completed paper02
experiments from the original IP2 TDX guest. It was exported without running
new experiments, changing the live environment, clearing state, or replaying
Docker operations.

## Result boundary

- E1: real online Full A3 legal admission and 54/54 business legs are complete;
  seven production-log-verifier fixtures are complete offline. Online abnormal
  injection cases remain `NOT_RUN`.
- E2: the single-budget Helper-freeze, receiver/traffic closure, owner-verified
  recovery, and post-recovery business requests are complete.
- E3: the real application read is complete for sessions/search with exact
  receiver association. A separate formal renewal/Quote-count campaign was not
  run and is `NOT_RUN`.
- E4: six formal runs form three paired seeds (`n=3` pairs). All evidence
  windows closed, but every task result remains `FAIL5/UNKNOWN1`; runner
  `native PASS` is not task success.
- E5: live SPIFFE/AuthZ access and the exact sessions/search receiver closure
  are complete. A separate formal per-round load/resource series was not run
  and is `NOT_RUN`.

Request counts and receiver interval counts are coverage evidence, not
independent samples. Missing values remain `UNKNOWN` or `NOT_RUN`, never zero.

## Layout

- `tables/`: E1-E5 status, E1 coverage, E4 component table, and figure data.
- `e1/`: public online-admission originals and offline-rule result outputs.
- `e2/`: receiver/closure analysis and fault timeline evidence.
- `e3-e5/`: exact two-request client/receiver closure and a derived receiver
  excerpt.
- `e4/manifest/`: frozen run order and non-secret configuration summaries.
- `e4/runs/`: six result packages plus full receiver, binding, deployment,
  correlation, and fault/recovery originals.
- `config/`: actual runtime, protocol, policy, and analysis boundary.
- `figures/`: generated SVG/PDF figures.
- `scripts/`: offline verification, E4 correlation recomputation, and plotting.
- `ORIGINALS-MAP.json`: source-to-export mapping and original SHA-256 values.
- `PROTECTED-MATERIALS.md`: omitted protected originals and reproducibility
  limits.
- `FILES.json` and `SHA256SUMS`: public file inventory and checksums.

## Offline commands

From this directory:

```bash
python3 scripts/verify_export.py
python3 scripts/recompute_e4.py
python3 scripts/plot_paper02.py
```

`verify_export.py` and `recompute_e4.py` use only files in this directory and
the Python standard library. Plot regeneration additionally requires
`matplotlib` as listed in `requirements.txt`.

The E1 fixture results were produced by the repository's actual
`../../../experiments/argus/admission_cases.py`, which calls
`history_diagnostics.py` and the production `LogVerifier`. Those sources are
already present in this Git base. Re-executing that cryptographic fixture
generator additionally requires the omitted ephemeral signing/trust material,
so it is not presented as a directly runnable command in this public export.

## Environment and labeling

The data comes from the original IP2 TDX guest, paper02 Full Argus, SPIRE
1.15.3, and the actual OpenViking image/policy identified in
`config/runtime-environment.json`. The controlled-v2 software backend
described by base commit `8cdc1900af999f1eef5191e03fef725b66940d1f` was not
deployed or used; that commit is only the clean Git export base.

## Public-data policy

No API key, OIDC token, private key, credential cache, owner material, runtime
database/WAL/SHM, image, or disk is included. Public receiver and result
originals are byte-for-byte copies. Derived excerpts are explicitly marked and
map back to the unchanged protected source hash.
