#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "e4" / "runs"


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def count_phase(rows, start, end):
    selected = [row for row in rows if start <= row.get("at_ms", -1) < end]
    enters = [
        row for row in selected
        if row.get("type") == "received"
        and row.get("phase") == "request_enter"
    ]
    ends = [row for row in selected if row.get("type") == "request_end"]
    intervals = [
        row for row in selected
        if row.get("type") == "coverage_interval"
        and row.get("status") == "COMPLETE"
    ]
    return {
        "request_enter": len(enters),
        "request_end": len(ends),
        "unique_request_ids": len({
            row["request_id"] for row in enters + ends if "request_id" in row
        }),
        "complete_intervals": len(intervals),
    }


def main():
    reports = []
    for directory in sorted(RUNS.iterdir()):
        correlation_path = directory / "window-correlation.json"
        receiver_path = directory / "receiver.jsonl"
        correlation = json.loads(correlation_path.read_text())
        receiver_sha256 = digest(receiver_path)
        if receiver_sha256 != correlation["receiver_sha256"]:
            raise SystemExit(f"{directory.name}: receiver SHA-256 mismatch")
        rows = [json.loads(line) for line in receiver_path.read_text().splitlines()]
        totals = {
            "request_enter": 0,
            "request_end": 0,
            "unique_request_ids": 0,
            "coverage_intervals": 0,
        }
        phases = {}
        for name, expected in correlation["phases"].items():
            observed = count_phase(rows, expected["start_ms"], expected["end_ms"])
            expected_intervals = expected.get("coverage_status", {}).get("COMPLETE", 0)
            checks = {
                "request_enter": expected["request_enter"],
                "request_end": expected["request_end"],
                "unique_request_ids": expected["unique_request_ids"],
                "complete_intervals": expected_intervals,
            }
            if observed != checks:
                raise SystemExit(
                    f"{directory.name}/{name}: observed {observed}, expected {checks}"
                )
            phases[name] = observed
            totals["request_enter"] += observed["request_enter"]
            totals["request_end"] += observed["request_end"]
            totals["unique_request_ids"] += observed["unique_request_ids"]
            totals["coverage_intervals"] += observed["complete_intervals"]
        if totals != correlation["window_totals"]:
            raise SystemExit(
                f"{directory.name}: totals {totals}, expected "
                f"{correlation['window_totals']}"
            )
        reports.append({
            "directory": directory.name,
            "run_id": correlation["run_id"],
            "condition": correlation["condition"],
            "receiver_sha256": receiver_sha256,
            "phases": phases,
            "totals": totals,
        })
    print(json.dumps({"schema": "argus.e4.public-recount.v1", "runs": reports}, indent=2))


if __name__ == "__main__":
    main()
