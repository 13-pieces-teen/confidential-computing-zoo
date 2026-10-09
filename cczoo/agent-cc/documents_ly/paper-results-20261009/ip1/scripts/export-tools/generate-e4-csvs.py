#!/usr/bin/env python3
"""EXPORT-TIME tabulation utility (NOT an as-run validator).

Deterministically tabulates the six frozen E4 formal result.json +
events.jsonl originals into three CSV files. It performs NO scoring and
NO re-verification: every cell is a verbatim field of the frozen originals
(or a simple derived offset). Verdict semantics are explained in
e4-formal/csv/README-csv.md and in the delivered results-analysis document.

Usage:
  python3 generate-e4-csvs.py <runs-dir> <csv-out-dir>

Where <runs-dir> contains r1..r6 (each with continuous/result.json,
continuous/state.json, continuous/events.jsonl) and <csv-out-dir> receives
e4-per-step.csv / e4-per-work-item.csv / e4-six-run-summary.csv.
"""
import csv
import json
import os
import sys

RUNS = [("r1", "101", "fault"), ("r2", "101", "healthy"),
        ("r3", "102", "healthy"), ("r4", "102", "fault"),
        ("r5", "103", "fault"), ("r6", "103", "healthy")]

STEP_COLS = ["run", "seed", "condition", "run_id", "step", "task_result",
             "reason", "offered", "dispatch_attempted", "planned_at_ms",
             "started_at_ms", "answer_at_ms", "answer_offset_s",
             "completed_at_ms", "deadline_at_ms", "deadline_missed",
             "committed", "write_attempted", "write_outcome",
             "store_tool_failed", "proposal_persisted", "recalled",
             "tool_call_count", "transport_evidence_count",
             "decision_committed", "confirmed_predecessors_recalled",
             "confirmed_proposals_recalled", "gateway_outcome",
             "gateway_error", "prerequisite", "fact_id", "fact_sha256",
             "constraint_key", "work_item_id"]

WORK_ITEM_COLS = ["run", "seed", "condition", "run_id", "work_item_id",
                  "complete_task_result", "continuation_result",
                  "legal_recovery_result", "receipt_result",
                  "unresolved_proposals", "unapplied_required_proposals",
                  "planned_tasks", "offered_tasks", "attempted_tasks"]

SUMMARY_COLS = ["run", "seed", "condition", "run_id", "operation_id",
                "result", "evidence_scope", "counts_PASS", "counts_FAIL",
                "counts_UNKNOWN", "counts_NOT_RUN", "planned_tasks",
                "attempted_tasks", "offered_tasks", "measurement_complete",
                "model_mismatches", "model", "provider",
                "protocol_digest", "manifest_sha256", "structure_hash",
                "started_at_ms", "completed_at_ms",
                "fault_status", "fault_returncode", "fault_argv_sha256",
                "recovery_status", "recovery_returncode", "recovery_argv_sha256"]


def load(run_dir):
    with open(os.path.join(run_dir, "continuous", "result.json")) as f:
        r = json.load(f)
    with open(os.path.join(run_dir, "continuous", "state.json")) as f:
        st = json.load(f)
    ev = []
    p = os.path.join(run_dir, "continuous", "events.jsonl")
    if os.path.exists(p):
        with open(p) as f:
            for line in f:
                line = line.strip()
                if line:
                    ev.append(json.loads(line))
    return r, st, ev


def main():
    runs_dir, out_dir = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    step_rows, wi_rows, sum_rows = [], [], []

    for name, seed, cond in RUNS:
        rd = os.path.join(runs_dir, name)
        r, st, ev = load(rd)
        win_start = r.get("started_at_ms")
        counts = r.get("counts", {})
        controls = r.get("controls", {})
        models = r.get("model_observations", {})

        for s in r.get("steps", []):
            ans = s.get("answer_at_ms")
            offs = "" if ans is None or win_start is None else round((ans - win_start) / 1000.0, 1)
            step_rows.append({k: s.get(k, "") for k in STEP_COLS if k not in
                              ("run", "seed", "condition", "run_id", "step",
                               "answer_offset_s")} |
                             {"run": name, "seed": seed, "condition": cond,
                              "run_id": r.get("run_id", ""), "step": s.get("step_id", ""),
                              "answer_offset_s": offs,
                              "transport_evidence_count": len(s.get("transport_evidence") or [])})

        for w in r.get("work_items", []):
            wi_rows.append({
                "run": name, "seed": seed, "condition": cond,
                "run_id": r.get("run_id", ""),
                "work_item_id": w.get("work_item_id", ""),
                "complete_task_result": w.get("complete_task_result", ""),
                "continuation_result": w.get("continuation_result", ""),
                "legal_recovery_result": w.get("legal_recovery_result", ""),
                "receipt_result": w.get("receipt_result", ""),
                "unresolved_proposals": ",".join(w.get("unresolved_proposals") or []),
                "unapplied_required_proposals": ",".join(w.get("unapplied_required_proposals") or []),
                "planned_tasks": r.get("planned_tasks", ""),
                "offered_tasks": r.get("offered_tasks", ""),
                "attempted_tasks": r.get("attempted_tasks", "")})

        fc = controls.get("fault", {})
        rc = controls.get("recovery", {})
        model_info = models.get("e4c1", {})
        sum_rows.append({
            "run": name, "seed": seed, "condition": cond,
            "run_id": r.get("run_id", ""),
            "operation_id": r.get("operation_id", ""),
            "result": r.get("result", ""),
            "evidence_scope": r.get("evidence_scope", ""),
            "counts_PASS": counts.get("PASS", ""), "counts_FAIL": counts.get("FAIL", ""),
            "counts_UNKNOWN": counts.get("UNKNOWN", ""), "counts_NOT_RUN": counts.get("NOT_RUN", ""),
            "planned_tasks": r.get("planned_tasks", ""),
            "attempted_tasks": r.get("attempted_tasks", ""),
            "offered_tasks": r.get("offered_tasks", ""),
            "measurement_complete": r.get("measurement_complete", ""),
            "model_mismatches": len(r.get("model_mismatches") or []),
            "model": model_info.get("configured_model", ""),
            "provider": st.get("preflight", {}).get("e4c1", {}).get("model", {}).get("provider", "")
                        if isinstance(st.get("preflight", {}).get("e4c1", {}).get("model"), dict) else "",
            "protocol_digest": r.get("protocol_digest", ""),
            "manifest_sha256": r.get("manifest_sha256", ""),
            "structure_hash": r.get("structure_hash", ""),
            "started_at_ms": r.get("started_at_ms", ""),
            "completed_at_ms": r.get("completed_at_ms", ""),
            "fault_status": fc.get("status", ""), "fault_returncode": fc.get("returncode", ""),
            "fault_argv_sha256": fc.get("argv_sha256", ""),
            "recovery_status": rc.get("status", ""), "recovery_returncode": rc.get("returncode", ""),
            "recovery_argv_sha256": rc.get("argv_sha256", "")})

    for fname, cols, rows in (("e4-per-step.csv", STEP_COLS, step_rows),
                              ("e4-per-work-item.csv", WORK_ITEM_COLS, wi_rows),
                              ("e4-six-run-summary.csv", SUMMARY_COLS, sum_rows)):
        with open(os.path.join(out_dir, fname), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(fname, len(rows), "rows")


if __name__ == "__main__":
    main()
