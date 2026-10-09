# IP1 -> IP2: E4 formal manifest (seeds 101 / 102 / 103)

schema: argus.e4.ip1-formal-manifest.v1
status: FORMAL_MANIFEST_DELIVERED_AWAITING_PER_RUN_PREFLIGHT
issued_at: 2026-10-08T17:26:14Z
responds_to: E4-PILOT-PAIR-CLOSURE.json (PILOT_PAIR_CONFIRMED_FORMAL_MANIFEST_REQUIRED)

## Frozen contract (as requested, unchanged)

- profile: `argus-recovery-work-item-v1`; scenario: `work-item-v1`; six steps;
  group `full_argus`; scale 1; max_concurrency/queue_limit 1; task_retries 0;
  fault_kind `helper-freeze`; fault_scope `shared_service`; capture_mode formal.
- model: `siliconflow/deepseek-ai/DeepSeek-V3.2`;
  model_config_sha256 `cffc15f6a4a29b47c2695431e69d75a6cd747c1ddace3aaeb633a80bb646b76f`.
- schedule (frozen=true): release 90 s; deadline 180 s; qa_timeout 180 s;
  stop_budget 10 s; window 630 s; fault@180 s (2x release); recovery@360 s (4x release).
- run budget: run_timeout_s 1200; step `--timeout` 1170.

## The six formal runs (manifest order = run-order.json)

| # | Seed | Condition | Run ID | User | Block | Output dir | Gen | Secret-seed sha256 |
|---|---|---|---|---|---|---|---:|---|
| 1 | 101 | fault | `e4p1-eeacfeaf71560d84a0c8168b7a2c20b2` | `e4p1-s101-recovery` | `continuous-n1-s101` | `/secure/e4-formal/runs/e4p1-eeacfeaf71560d84a0c8168b7a2c20b2` | 1 | `fca4cbf99ad7bc2f57231baf6a0b053cb0435e879c264d18c317d37fd77e2579` |
| 2 | 101 | no_fault | `e4p1-861c0d0a872e0831d93271cd6292aa98` | `e4p1-s101-healthy` | `continuous-n1-s101` | `/secure/e4-formal/runs/e4p1-861c0d0a872e0831d93271cd6292aa98` | 1 | `d89071f6522639fc4e4c26e9784e84f708ccf5d2ca1a59d77927ca4a29eca672` |
| 3 | 102 | no_fault | `e4p1-94e9cd04e6e58ab00041228fadf8da5c` | `e4p1-s102-healthy` | `continuous-n1-s102` | `/secure/e4-formal/runs/e4p1-94e9cd04e6e58ab00041228fadf8da5c` | 1 | `731caa8e598c0b207ec642f21aafef0a1cd1794ab9896dc870b4c2b110ea4446` |
| 4 | 102 | fault | `e4p1-cd119c89ea1d8995ffb995b87b6ed3dc` | `e4p1-s102-recovery` | `continuous-n1-s102` | `/secure/e4-formal/runs/e4p1-cd119c89ea1d8995ffb995b87b6ed3dc` | 1 | `af94843b3a3e2f77d45036b959c5fdcd222b858b77b8b208f100d2067415c79a` |
| 5 | 103 | fault | `e4p1-7998d97419ea05161cc33525d2b1b3c3` | `e4p1-s103-recovery` | `continuous-n1-s103` | `/secure/e4-formal/runs/e4p1-7998d97419ea05161cc33525d2b1b3c3` | 1 | `640549c6f74f173da4ac3412a84a7c4b6921ad634e83c6d2ba1eb25721fc17a2` |
| 6 | 103 | no_fault | `e4p1-a719364175d67653b560bf1b386f8539` | `e4p1-s103-healthy` | `continuous-n1-s103` | `/secure/e4-formal/runs/e4p1-a719364175d67653b560bf1b386f8539` | 1 | `25863d46961fc2c38ce00913a0e42f37375629d96a29d047983a288a5bae2d2d` |

Six distinct ordinary private users (account `argus-eval`), six distinct secret
seeds. Only hashes are delivered; values are retained solely in the protected
configs at `/secure/e4-formal/` (root, mode 0600). No API keys in this package.

## Controls contract

- healthy / no_fault runs (2, 3, 6): both control slots `argv=[]` — no fault or
  recovery operation is sent. Empty-argv sha256 (over `[]`):
  `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`.
- fault runs (1, 4, 5): argv arrays byte-identical to the owner-verified
  `E4-PILOT-CONTROL-OVERRIDES.json` arrays (`{run_id}` placeholder form, compact
  JSON sha256): fault `b0f4d271bdce19b39a31251af24304e1f24477805c0ba6278c8dff1edad01030` (remote_acceptance.py fault
  --execute-fault --hold-recovery); recovery `06123d651d427888a43a844e773034d3424f5561dce46509a9ced31e901cd415`
  (e4_owner_recovery.py). `{run_id}` is substituted at execution. Control
  timeout_s 120 (unchanged from the pilot override); at_s 180/360 per the freeze.

## Gateway / identity contract (unchanged)

- container `argus-oc-paper01full`, docker_user `21001:21001`, gateway config
  `/home/node/.openclaw/openclaw.json`, agent `main`, client `e4c1`;
- client SPIFFE `spiffe://argus.local/agent/openclaw/experiment/paper02/full`;
  server SPIFFE `spiffe://argus.local/service/openviking-cmem/experiment/paper02/full`;
- account `argus-eval`; initialization_generation 1 for all six runs.

## Suite artifacts

- `suite.json`: capture_mode formal; per-seed pair protocol digest
  `1f069d67625b95bbcbdf53040ce2c59c945ccd0a4d619d17bb8a32cff6a4123f`
  (identical across seeds 101/102/103); `secrets` section empty.
- `run-order.json`: six runs, 6 planned tasks each, in the manifest order above.
- `CONFIG-SUMMARIES.json`: full field-by-field configs except `secret_seed`.

## Next gate

Per the agreed formal_start_gate: per-run IP2 preflight follows this manifest
(IP2 creates the per-run users, receivers and launch). IP1 will then install
the per-run user gateway config with the per-run API key IP2 delivers, and
execute each run. IP1 executes no run before IP2's per-run preflight, and sends
no fault/recovery control outside the run window.

## Files

CONFIG-SUMMARIES.json, IP2-E4-FORMAL-MANIFEST-20261008.md, suite.json,
run-order.json (SHA256SUMS). No API keys, no secret-seed values, no tokens.
