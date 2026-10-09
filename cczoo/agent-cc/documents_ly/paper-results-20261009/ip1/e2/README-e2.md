# E2 (helper-freeze fault trial) — exported materials

- configs/ — note: e2-config*.json are held on server (R2); see MISSING.md.
- scripts/ — e2-chain-r4.sh (the r4 execution chain) and buffering-test.py.
- runs/r1, retry1, r3 — NOT_RUN diagnoses: result/state/trace/releases/
  lifecycle records + diagnostics; r4 — the PASS run: result/state/trace/
  releases/lifecycle/fault.jsonl/fact-* records (receiver.jsonl held on
  server, R2).
- handoff/ — the full IP1↔IP2 E2 handoff chain (md receipts/closures +
  evidence json): r1 buffering fix, retry1/r3 diagnoses, helper-freeze prep,
  r4 ready/arm/rearm/window-expired/recovery/result/final-closure packages.

E2 r4 outcome: fault injected with verified hold, result PASS; IP2 final
closure CONFIRMED. Configs and credentials intentionally not exported.
