#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]

def contiguous(values):
    return values == list(range(values[0], values[-1] + 1))

rules = json.loads((ROOT / "CROP-RULES.json").read_text())
experiment = rows(ROOT / rules["experiment"]["crop_export"])
recovery = rows(ROOT / rules["recovery"]["crop_export"])
assert sha(ROOT / rules["experiment"]["crop_export"]) == rules["experiment"]["crop_sha256"]
assert sha(ROOT / rules["recovery"]["crop_export"]) == rules["recovery"]["crop_sha256"]
assert contiguous([row["record_seq"] for row in experiment])
assert contiguous([row["record_seq"] for row in recovery])
assert experiment[-1]["type"] == "receiver_stop" and experiment[-1]["complete"] is False
assert experiment[-2]["type"] == "coverage_interval"
assert experiment[-2]["status"] == "UNKNOWN"
assert experiment[-2]["reason"] == "uncovered_final_or_crash_tail"
for selected, expected in ((experiment, rules["experiment"]["inflight_request_id"]),
                           (recovery, rules["recovery"]["request_ids"][0]),
                           (recovery, rules["recovery"]["request_ids"][1])):
    matched = [row for row in selected if row.get("request_id") == expected]
    assert matched and matched[0].get("phase") == "request_enter"
    assert any(row["type"] == "request_end" for row in matched)
watermarks = [row["source_seq"] for row in recovery if row["type"] == "source_watermark"]
assert watermarks == sorted(watermarks)
assert any(row["type"] == "coverage_interval" and row["status"] == "COMPLETE" for row in recovery)
print("PASS: E2 r4 crops, request lifecycles, watermarks, and preserved UNKNOWN tail verified")
