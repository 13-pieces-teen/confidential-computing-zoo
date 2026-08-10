import json
import unittest

from collector import parse_docker_stats_line, parse_prometheus, parse_size


class CollectorParsingTests(unittest.TestCase):
    def test_parse_binary_and_decimal_sizes(self):
        self.assertEqual(parse_size("1.5MiB"), 1572864)
        self.assertEqual(parse_size("2 GB"), 2000000000)
        self.assertIsNone(parse_size("unknown"))

    def test_parse_docker_stats_keeps_normalized_resource_fields(self):
        line = json.dumps({
            "Name": "argus-v2-guard",
            "CPUPerc": "12.50%",
            "MemUsage": "40MiB / 1GiB",
            "MemPerc": "3.91%",
            "NetIO": "10kB / 20kB",
            "BlockIO": "1MB / 2MB",
            "PIDs": "7",
        })

        sample = parse_docker_stats_line(line, {"argus-v2-guard": "guard"})

        self.assertEqual(sample["label"], "guard")
        self.assertEqual(sample["cpu_percent"], 12.5)
        self.assertEqual(sample["memory_used_bytes"], 40 * 1024 * 1024)
        self.assertEqual(sample["network"]["read_bytes"], 10000)
        self.assertEqual(sample["pids"], 7)

    def test_prometheus_parser_keeps_only_argus_and_process_metrics(self):
        samples = parse_prometheus("""
# HELP ignored ignored
argus_guard_requests_total{decision="allow"} 12
spire_server_argus_nodeattestor_trustee_requests{result="success"} 1
unrelated_metric 99
process_resident_memory_bytes 1024
""")

        self.assertEqual(samples['argus_guard_requests_total{decision="allow"}'], 12)
        self.assertEqual(
            samples['spire_server_argus_nodeattestor_trustee_requests{result="success"}'],
            1,
        )
        self.assertNotIn("unrelated_metric", samples)


if __name__ == "__main__":
    unittest.main()
