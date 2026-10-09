# As-run validator provenance

The eight files in this directory are the **exact working-tree copies** of the
validator/acceptance scripts that executed the six frozen E4 formal runs
(2026-10-08/09). They are exported for offline reading and re-tabulation
reference; they are **not** a standalone runnable subset.

- Source repository commit (the run host's checkout HEAD):
  `3cf25a87a2ba1ab4f6ff4ac0821ca515570fa5c7` (branch `feat/argus-spiffe-v2-val`).
- The working tree carried uncommitted modifications relative to that commit;
  `as-run-diff.patch` is the complete `git diff 3cf25a87 -- <these 8 files>`
  for the files below (225 lines, no secret material).
- `sha256sum` of each file as executed (identical to the copies in this
  directory):

| file | sha256 |
|---|---|
| continuous.py | `eff14fb46cc3f7851402c25593620264e1f4b2fdd75c1139bcc4a47abf477720` |
| analysis.py | `7540e1e47d8042b0d81b6e9b9ec81aa1af0cfc79941c222230ae3179a3c21850` |
| continuous_analysis.py | `9d52107dfdf4b836bc40ad2bedfaba5570c4134523159b00b8929dfa7d49fd63` |
| continuous_gateway.mjs | `d09b3a62c64939528cd90911f978bcd7b49e74e64b7eea58fe6270c83155d254` |
| continuous_work_item.py | `cea1608bfd3bc59fb3a930490b15b66e6ce655fd741cb098290dff3c2d4f196a` |
| continuous_proposal.py | `8d43895bfa7f1c3806c53a5d6869fffe5f8c95c761bc5b33a6a989afe16a2503` |
| locomo_run.py | `a52ece66e0c74023ae50b6bc2b722c9d2399aae93d693bdfd398530ab97d08b4` |
| step.py | `570e1108dc44afd4c271fef6b292fb0d185360f6a78d66061ce81abdd37aeead` |

- Runtime: Python 3.9.25, standard library only for the exported files.
- Dependencies: the Python files import sibling modules that live next to them
  in the repository (`common.py`, `continuous_proposal.py`,
  `continuous_work_item.py`, `fact_protocol.py`, `locomo_analysis.py`,
  `locomo_execution.py`, `locomo_run.py`). Reproducing the as-run environment
  requires the repository tree at commit `3cf25a87` plus `as-run-diff.patch`
  applied. These modules are not exported here to keep this package focused.
- `continuous_gateway.mjs` runs on Node.js inside the experiment gateway
  container (invoked by the Python runner).

No other validator version was used for the six formal runs; the verdicts in
the frozen `result.json` files were produced by exactly these bytes.
