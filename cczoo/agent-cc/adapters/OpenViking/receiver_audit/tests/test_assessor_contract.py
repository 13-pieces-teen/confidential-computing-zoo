"""Producer/consumer contract: real Collector v2 output -> real assessor.

Controlled source clocks and kernel-peer arguments make the fault window
deterministic. Actual datagram/SCM_CREDENTIALS behavior is tested in test_audit.
These fixtures do not stand in for remote systemd or business acceptance.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

OPENVIKING = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(OPENVIKING))
from receiver_audit.collector import Collector, process_facts

ASSESSOR = OPENVIKING.parents[1] / "core/spire/workload/scripts/remote_acceptance.py"
spec = importlib.util.spec_from_file_location("receiver_contract_assessor", ASSESSOR)
remote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(remote)


@unittest.skipUnless(sys.platform.startswith("linux"), "real Linux /proc source association required")
class ReceiverAssessorContractTests(unittest.TestCase):
    def generated_trial(self, directory, *, late_body):
        facts = process_facts(os.getpid())
        binding = {"schema_version": 2, "run_id": "contract-run", "instance_id": "a" * 64,
                   "launch_id": "launch-contract", "target_sha256": "b" * 64, "process": facts}
        output = Path(directory) / "receiver.jsonl"
        collector = Collector(binding, output)
        # Controlled positive source clocks also work immediately after WSL boot.
        # A controlled finalization clock adds the genuinely unknown tail later
        # than the measured interval, without mutating any generated records.
        wall = time.time_ns() // 1000000 - 10000
        mono = 1000000000
        sequence = 0

        def emit(op, offset, **fields):
            nonlocal sequence
            sequence += 1
            frame = dict(schema_version=2, run_id=binding["run_id"], source_id="contract-source",
                         source_seq=sequence, dropped=0, at_ms=wall + offset,
                         monotonic_ns=mono + offset * 1000000, source_started_at_ms=wall - 50,
                         source_started_monotonic_ns=mono - 50 * 1000000, op=op, **fields)
            # Feed the Collector at its kernel-peer boundary. Its production
            # resolver still verifies this real PID/UID/starttime against /proc.
            collector.accept(json.dumps(frame).encode(), os.getpid(), os.getuid())

        probe = [dict(type="probe_start", run_id=binding["run_id"], at_ms=wall,
                      inflight_required=True)]
        emit("watermark", 0)
        for index in range(3):
            for lane_offset, lane in enumerate(("existing", "new")):
                start = 100 + 100 * index + lane_offset * 10
                rid = f"{lane}-baseline-{index}"
                base = dict(request_id=rid, stream_id="stream-" + rid)
                emit("begin", start, correlated=True, **base)
                emit("pending", start + 1, chunk_seq=1, **base)
                emit("observed", start + 2, chunk_seq=1, body_bytes=3,
                     message_type="http.request", more_body=False, **base)
                emit("end", start + 3, **base)
                probe.append(dict(type="request", run_id=binding["run_id"], lane=lane,
                    request_id=rid, started_at_ms=wall + start, completed_at_ms=wall + start + 5,
                    ok=True, tls_connections=1))
        slow = dict(request_id="slow-before-fault", stream_id="stream-slow")
        probe.append(dict(type="stream_start", run_id=binding["run_id"], lane="inflight",
                          request_id=slow["request_id"], started_at_ms=wall + 500))
        emit("begin", 500, correlated=True, **slow)
        emit("pending", 501, chunk_seq=1, **slow)
        emit("observed", 502, chunk_seq=1, body_bytes=5, message_type="http.request", more_body=True, **slow)
        emit("pending", 900, chunk_seq=2, **slow)
        emit("watermark", 950)
        emit("watermark", 1200)
        if late_body:
            emit("observed", 1300, chunk_seq=2, body_bytes=7, message_type="http.request", more_body=False, **slow)
            emit("end", 1301, **slow)
        else:
            emit("observed", 1610, chunk_seq=2, body_bytes=0, message_type="http.disconnect", more_body=False, **slow)
            emit("end", 1611, **slow)
        emit("watermark", 1800)
        emit("watermark", 2000)
        for index in range(3):
            for lane in ("existing", "new"):
                start = 1200 + index * 200
                probe.append(dict(type="request", run_id=binding["run_id"], lane=lane,
                    request_id=f"{lane}-blocked-{index}", started_at_ms=wall + start,
                    completed_at_ms=wall + start + 20, ok=False, tls_connections=1))
        probe.append(dict(type="request", run_id=binding["run_id"], lane="inflight",
            request_id=slow["request_id"], started_at_ms=wall + 500, completed_at_ms=wall + 1650, ok=False))
        probe.append(dict(type="probe_stop", run_id=binding["run_id"], at_ms=wall + 1700, complete=True))
        event = dict(run_id=binding["run_id"], executed=True, started_at_ms=wall + 1000,
                     started_monotonic_ns=mono + 1000 * 1000000,
                     recovery_hold={"verified": True, "restart": "no"},
                     target={"container_id": binding["instance_id"], "launch_id": binding["launch_id"],
                             "pid": facts["pid"], "start_time": facts["start_time"]})
        with patch("receiver_audit.collector.clocks", return_value={
                "at_ms": wall + 3000, "monotonic_ns": mono + 3000 * 1000000}):
            collector.finish()
        logs = remote.receiver_rows(output, binding["run_id"])
        result = remote.assess(probe, event, logs, bound_ms=100, clock_uncertainty_ms=0)
        return result, logs, probe, event

    def test_actual_collector_complete_window_passes_despite_later_unknown_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            result, logs, _, _ = self.generated_trial(directory, late_body=False)
        self.assertTrue(any(r.get("status") == "COMPLETE" for r in logs))
        self.assertTrue(any(r.get("reason") == "uncovered_final_or_crash_tail" for r in logs))
        self.assertFalse(logs[-1]["complete"])
        self.assertEqual(result["result"], "PASS", result)
        self.assertEqual(result["receiver_delivery"], "PASS", result)
        self.assertEqual(result["inflight_delivery"], "PASS", result)

    def test_actual_collector_late_body_of_prefault_request_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            result, logs, probe, event = self.generated_trial(directory, late_body=True)
        slow = next(r for r in probe if r.get("type") == "stream_start")
        self.assertLess(slow["started_at_ms"], event["started_at_ms"])
        late = next(r for r in logs if r.get("phase") == "body_read" and r.get("received_body_bytes") == 7)
        self.assertGreater(late["monotonic_ns"], event["started_monotonic_ns"] + 100 * 1000000)
        self.assertEqual(result["result"], "FAIL", result)
        self.assertEqual(result["receiver_delivery"], "FAIL", result)
        self.assertEqual(result["inflight_delivery"], "FAIL", result)
        self.assertEqual(result["received_body_bytes"], 7)


if __name__ == "__main__": unittest.main()
