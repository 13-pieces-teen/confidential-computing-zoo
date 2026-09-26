import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis import analyze, summarize_requests
from common import atomic, read, sha


class AnalysisEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = {"run_id": "trial", "block_id": "seed1", "case": "load", "scale": 1, "group": "full_argus", "result": "PASS"}
        atomic(self.root / "verdicts.json", {"runs": [self.run]})
        self.directory = self.root / "runs/trial"
        self.directory.mkdir(parents=True)
        self.trace = self.directory / "requests.jsonl"
        self.trace.write_text(json.dumps({"run_id": "trial", "request_id": "one", "phase": "measurement", "outcome": "success", "latency_ms": 5}) + "\n")
        self.result = {"run_id": "trial", "measurement_complete": True, "measurement_seconds": 2, "requests_sha256": sha(self.trace)}

    def summary(self):
        atomic(self.directory / "load-result.json", self.result)
        analyze(self.root)
        return read(self.root / "analysis.json")["summaries"][0]

    def test_complete_hashed_trace_contributes_goodput(self):
        result = self.summary()
        self.assertEqual(result["api_goodput_rps"]["mean"], .5)
        self.assertEqual(result["verdicts"]["PASS"], 1)

    def test_changed_or_partial_trace_cannot_contribute_metrics(self):
        self.trace.write_text(self.trace.read_text() + "\n")
        result = self.summary()
        self.assertIsNone(result["api_goodput_rps"]["mean"])
        self.assertEqual(result["verdicts"]["UNKNOWN"], 1)
        self.result["requests_sha256"] = sha(self.trace)
        self.result["measurement_complete"] = False
        self.assertEqual(self.summary()["verdicts"]["UNKNOWN"], 1)

    def test_capacity_stop_preserves_not_run(self):
        self.trace.write_text("")
        self.result.update(result="NOT_RUN", reason="CAPACITY_STOP", requested_clients=8,
                           provisioned_clients=3, measurement_complete=False, requests_sha256=sha(self.trace))
        result = self.summary()
        self.assertEqual(result["verdicts"]["NOT_RUN"], 1)
        self.assertIsNone(result["api_goodput_rps"]["mean"])

    def test_nan_and_unknown_outcomes_are_not_silent_statistics(self):
        for row in ({"phase": "measurement", "outcome": "success", "latency_ms": float("nan")},
                    {"phase": "measurement", "outcome": "invented", "latency_ms": 1}):
            with self.assertRaises(ValueError): summarize_requests([row], 1)
        with self.assertRaises(ValueError): summarize_requests([], float("nan"))


if __name__ == "__main__": unittest.main()
