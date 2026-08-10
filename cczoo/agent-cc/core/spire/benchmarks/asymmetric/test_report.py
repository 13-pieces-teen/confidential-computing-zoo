import json
import tempfile
import unittest
from pathlib import Path

from report import aggregate_case, amortization, percentile


class ReportTests(unittest.TestCase):
    def test_percentile_uses_linear_interpolation(self):
        self.assertEqual(percentile([1, 2, 3, 4, 5], 0.5), 3)
        self.assertAlmostEqual(percentile([1, 2, 3, 4], 0.95), 3.85)
        self.assertIsNone(percentile([], 0.95))

    def test_case_aggregation_joins_request_transport_and_resource_records(self):
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory)
            (case / "metadata.json").write_text(
                json.dumps({"experiment": "E5", "profile": "e5-qps-10", "concurrency": 4}),
                encoding="utf-8",
            )
            records = [
                {"type": "request", "ok": True, "duration_ms": 10},
                {"type": "request", "ok": False, "duration_ms": 20},
                {
                    "type": "spiffe_transport",
                    "outcome": "response_headers",
                    "guard_decision_ms": 1,
                    "guard_to_send_ms": 2,
                    "transport_headers_ms": 3,
                    "guarded_request_headers_ms": 6,
                },
                {"type": "summary", "achieved_qps": 9.5, "concurrency": 4},
            ]
            (case / "requests.jsonl").write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            resource = {
                "type": "resource",
                "errors": [],
                "host": {"cpu_percent": 25},
                "connections": {"target_total": 4},
                "host_containers": [{
                    "label": "guard",
                    "cpu_percent": 5,
                    "memory_used_bytes": 1024,
                    "fd_count": 9,
                    "pids": 2,
                }],
                "svids": {"openviking": {"serial_number": "01"}},
            }
            (case / "resources.jsonl").write_text(json.dumps(resource) + "\n", encoding="utf-8")

            summary = aggregate_case(case)

            self.assertEqual(summary["successful"], 1)
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(summary["success_rate"], 0.5)
            self.assertEqual(summary["guard_decision_ms"]["p95"], 1)
            self.assertEqual(summary["resource"]["components"]["guard"]["cpu_percent"]["max"], 5)
            self.assertEqual(summary["resource"]["components"]["guard"]["fd_count"]["max"], 9)

    def test_amortization_reports_steady_state_counter_deltas(self):
        cases = [
            {
                "experiment": "E5",
                "successful": 900,
                "resource": {"svid_rotations": {}},
            },
            {
                "experiment": "E6",
                "successful": 100,
                "resource": {"svid_rotations": {"openviking": 3}},
            },
        ]
        before = {
            'spire_server_argus_nodeattestor_attempts{result="success",side="server"}': 1,
            'spire_server_argus_nodeattestor_trustee_requests{result="success"}': 1,
        }
        after = dict(before)

        result = amortization(cases, before, after)

        self.assertEqual(result["successful_business_requests"], 1000)
        self.assertEqual(result["new_attestations_during_business_benchmark"], 0)
        self.assertEqual(result["new_trustee_requests_during_business_benchmark"], 0)
        self.assertEqual(result["business_requests_per_attestation"], 1000)
        self.assertEqual(result["trustee_requests_per_1000_business_requests"], 1)
        self.assertEqual(result["trustee_requests_per_svid_rotation"], 0)


if __name__ == "__main__":
    unittest.main()
