import copy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis import summarize_resources


def test_resource_windows_are_process_bound_and_no_negative_cpu():
    rows = [{"run_id": "r", "pid": 123, "start_ticks": 10, "at_ms": 1000 + i * 1000,
             "cpu_seconds": i / 2, "rss_bytes": (i + 1) * 100} for i in range(3)]
    result = summarize_resources(rows, "r")
    assert result["process_cpu_seconds"] == 1
    assert result["sum_process_peak_rss_bytes"] == 300
    bad = copy.deepcopy(rows); bad[-1]["start_ticks"] = 20
    assert summarize_resources(bad, "r")["resources_result"] == "UNKNOWN"
    bad = copy.deepcopy(rows); bad[-1]["cpu_seconds"] = .1
    assert summarize_resources(bad, "r")["resources_result"] == "UNKNOWN"
    assert summarize_resources(rows, "other")["resources_result"] == "UNKNOWN"
