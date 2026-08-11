import json
import tempfile
import unittest
from pathlib import Path

import report


class AgentTaskReportTests(unittest.TestCase):
    def test_nearest_rank_percentile(self):
        self.assertEqual(report.percentile(range(1, 11), 0.95), 10.0)
        self.assertIsNone(report.percentile([], 0.95))

    def test_case_uses_wall_clock_tasks_per_minute_and_keeps_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary) / "C2"
            case.mkdir()
            (case / "metadata.json").write_text(
                json.dumps({"openclaw_units": 2}), encoding="utf-8"
            )
            tasks = [
                {
                    "measured": True,
                    "unit_id": "openclaw-01",
                    "status": "completed",
                    "started_unix_ms": 1_000,
                    "finished_unix_ms": 31_000,
                    "agent_task_e2e_ms": 30_000,
                    "agent_turn_ms": 10_000,
                    "capture_first_observed_ms": 12_000,
                    "commit_to_archive_ms": 18_000,
                    "elapsed_ms": 30_000,
                },
                {
                    "measured": True,
                    "unit_id": "openclaw-02",
                    "status": "failed",
                    "failure_stage": "openviking_archive",
                    "error_class": "timeout",
                    "started_unix_ms": 1_000,
                    "finished_unix_ms": 61_000,
                    "elapsed_ms": 60_000,
                },
            ]
            (case / "tasks.jsonl").write_text(
                "".join(json.dumps(task) + "\n" for task in tasks), encoding="utf-8"
            )
            summary = report.aggregate_case(case)
            self.assertEqual(summary["completed"], 1)
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(summary["tasks_per_minute"], 1.0)
            self.assertEqual(summary["failure_stages"], {"openviking_archive": 1})
            self.assertEqual(summary["task_finalization_ms"]["count"], 2)
            self.assertEqual(summary["task_finalization_ms"]["p95"], 60_000.0)
            self.assertEqual(summary["failed_task_elapsed_ms"]["count"], 1)
            self.assertEqual(summary["failed_task_elapsed_ms"]["p50"], 60_000.0)
            self.assertEqual(summary["failure_stage_elapsed_ms"]["openviking_archive"]["p50"], 60_000.0)

    def test_zero_denominators_stay_null(self):
        self.assertIsNone(report.safe_ratio(10, 0))
        self.assertIsNone(report.safe_ratio(10, None))

    def test_scaling_efficiency_uses_c1_completed_throughput(self):
        cases = [
            {"case": "C1", "openclaw_units": 1, "tasks_per_minute": 2.0},
            {"case": "C4", "openclaw_units": 4, "tasks_per_minute": 6.0},
        ]
        report.apply_scaling(cases)
        self.assertEqual(cases[0]["scaling_efficiency"], 1.0)
        self.assertEqual(cases[1]["scaling_efficiency"], 0.75)

    def test_formal_matrix_is_complete_only_with_nonempty_c1_c2_c4_c8(self):
        pilot = [{"case": "P0", "launched": 2}]
        incomplete = report.formal_matrix_status(pilot)
        self.assertFalse(incomplete["complete"])
        self.assertEqual(incomplete["missing_cases"], ["C1", "C2", "C4", "C8"])

        formal = [
            {"case": name, "launched": 10}
            for name in ("C1", "C2", "C4", "C8")
        ]
        complete = report.formal_matrix_status(formal)
        self.assertTrue(complete["complete"])
        self.assertEqual(complete["missing_cases"], [])


if __name__ == "__main__":
    unittest.main()
