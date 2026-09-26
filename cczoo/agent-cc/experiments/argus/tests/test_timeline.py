"""Local metadata tests; no real TDX, systemd shutdown bound or model result."""
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import timeline


def evidence():
    process = dict(pid=123, start_time="42", boot_id="server")
    fault = dict(type="fault", run_id="run", executed=True, started_at_ms=5000, started_monotonic_ns=5_000_000_000,
                 completed_at_ms=5010, completed_monotonic_ns=5_010_000_000, clock_id="boot:server",
                 target=dict(container_id="a" * 64, launch_id="launch", **process),
                 recovery_hold=dict(verified=True, restart="no"))
    binding = dict(instance_id="a" * 64, launch_id="launch", process=process)
    receiver = [dict(type="receiver_start", binding=binding, at_ms=0),
                dict(type="source_seen", source_id="source", process=process, at_ms=0)]
    probe = [dict(type="probe_start", run_id="run", at_ms=0)]
    for lane in ("existing", "new"):
        for i, at in enumerate((1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000)):
            rid = lane + str(i)
            probe.append(dict(type="request", run_id="run", request_id=rid, lane=lane,
                              started_at_ms=at, completed_at_ms=at+20, ok=at < 5000, tls_connections=1))
            if at < 5000:
                receiver.append(dict(type="received", phase="body_read", source_id="source", process=process,
                                     request_id=rid, at_ms=at, monotonic_ns=at*1_000_000, received_body_bytes=12,
                                     message_type="http.request", provenance="kernel_process_and_deployment"))
    receiver.extend([dict(type="coverage_interval", status="COMPLETE", source_id="source", started_at_ms=0,
                          ended_at_ms=10000, started_monotonic_ns=0, ended_monotonic_ns=10_000_000_000),
                     dict(type="receiver_stop", at_ms=10000)])
    for i, row in enumerate(receiver, 1):
        row.update(schema_version=2, record_seq=i, collector_id="collector", run_id="run", instance_id="a" * 64, launch_id="launch")
    probe.append(dict(type="probe_stop", run_id="run", at_ms=9000, complete=True))
    return fault, receiver, probe


def summary(fault, receiver, probe, lifecycle=(), uncertainty=0):
    return timeline.summarize(fault, receiver, probe, lifecycle, bound_ms=1000, clock_uncertainty_ms=uncertainty)


def test_complete_coverage_is_finite_and_missing_lifecycle_is_unknown():
    result = summary(*evidence())
    assert result["detection"]["status"] == result["entry_stop"]["status"] == "UNKNOWN"
    application = result["application_reads"]
    assert application["post_bound_verdict"] == "PASS"
    assert application["coverage"][0]["status"] == "COMPLETE"
    assert application["last_observed_read"]["upper_ms"] == -1000
    assert application["observation_end_ms"] == 3020
    assert "future" in application["absence_claim"]


def test_positive_reads_survive_crash_tail_and_wrong_instance_does_not_count():
    fault, receiver, probe = evidence()
    row = receiver[2] | dict(at_ms=6700, monotonic_ns=6_700_000_000, received_body_bytes=19)
    receiver.append(row)
    receiver[-3]["ended_monotonic_ns"] = 6_000_000_000
    for i, record in enumerate(receiver, 1):
        record["record_seq"] = i
    result = summary(fault, receiver, probe)["application_reads"]
    assert result["post_bound"]["bytes"] == 19
    assert result["post_bound_verdict"] == "FAIL"
    assert result["coverage"][0]["status"] == "UNKNOWN"
    row["instance_id"] = "b" * 64
    assert summary(fault, receiver, probe)["application_reads"]["post_bound"]["bytes"] == 0


def test_source_gap_remains_unknown_even_when_watermarks_overlap():
    fault, receiver, probe = evidence()
    receiver.append(receiver[-2] | dict(status="UNKNOWN", started_at_ms=6500, ended_at_ms=7000,
                                      started_monotonic_ns=6_500_000_000, ended_monotonic_ns=7_000_000_000))
    for i, row in enumerate(receiver, 1):
        row["record_seq"] = i
    application = summary(fault, receiver, probe)["application_reads"]
    assert application["coverage"][0]["unknown_intervals_ms"] == [[1500.0, 2000.0]]
    assert application["post_bound_verdict"] == "UNKNOWN"


def test_polling_intervals_and_clock_uncertainty_do_not_become_exact_times():
    fault, receiver, probe = evidence()
    lifecycle = [dict(type="detected", run_id="run", verified=True, source="poll", timing="poll_interval",
                      boundary="readiness_withdrawal", observed_after_at_ms=5150, at_ms=5250),
                 dict(type="entry_stopped", run_id="run", verified=True, source="poll", timing="poll_interval",
                      observed_after_at_ms=5200, at_ms=5400)]
    result = summary(fault, receiver, probe, lifecycle, uncertainty=20)
    assert result["detection"]["lower_ms"] == 130
    assert result["detection"]["upper_ms"] == 270
    assert result["close_after_detection"]["lower_ms"] == -90
    assert result["close_after_detection"]["upper_ms"] == 290
    lifecycle[0]["run_id"] = "another-run"
    assert summary(fault, receiver, probe, lifecycle)["detection"]["status"] == "UNKNOWN"


def test_monotonic_clock_requires_same_boot_for_client_and_lifecycle():
    fault, _, _ = evidence()
    row = dict(at_ms=6005, monotonic_ns=900_000_000_000, clock_id="boot:client")
    assert timeline.relative(row, fault, uncertainty_ms=10) == {
        "lower_ms": 995, "upper_ms": 1015, "clock": "wall_with_uncertainty"}
    row.update(clock_id="boot:server", monotonic_ns=6_005_000_000)
    assert timeline.relative(row, fault, uncertainty_ms=10)["lower_ms"] == 1005


def test_receiver_record_loss_prevents_absence_verdict():
    fault, receiver, probe = evidence()
    receiver.pop(2)
    result = summary(fault, receiver, probe)
    assert result["application_reads"]["post_bound_verdict"] == "UNKNOWN"
    assert result["application_reads"]["post_bound"]["bytes"] == 0


def test_invalid_fault_or_negative_clock_uncertainty_is_rejected():
    fault, receiver, probe = evidence()
    with pytest.raises(ValueError):
        summary(fault | dict(executed=False), receiver, probe)
    with pytest.raises(ValueError):
        summary(fault, receiver, probe, uncertainty=-1)


def test_observer_records_real_poll_brackets_and_never_mutates_services(tmp_path):
    states = [dict(ready=True, entry_active=True, entry_stopped=False, helper_active=True),
              dict(ready=False, entry_active=False, entry_stopped=True, helper_active=False)]
    calls = []
    def sample(deployment):
        calls.append(deployment)
        return states[min(len(calls)-1, 1)]
    with patch.object(timeline.time, "monotonic", side_effect=[0, 0, .1, .2, .3, 2]), patch.object(timeline.time, "sleep"):
        timeline.observe("fixture", tmp_path / "events.jsonl", "run", duration=1, sample=sample)
    rows = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert len(calls) == 2
    assert {r["type"] for r in rows} >= {"observer_ready", "detected", "entry_stopped", "observer_stop"}
    detected = next(r for r in rows if r["type"] == "detected")
    assert detected["timing"] == "poll_interval"
    assert detected["observed_after_at_ms"] <= detected["at_ms"]
    assert detected["boundary"] == "readiness_withdrawal_or_expiry"


def test_observer_does_not_infer_a_transition_without_a_healthy_baseline(tmp_path):
    state = dict(ready=False, entry_active=False, entry_stopped=True, helper_active=False)
    with patch.object(timeline.time, "monotonic", side_effect=[0, 0, .1, 2]), patch.object(timeline.time, "sleep"):
        timeline.observe("fixture", tmp_path / "events.jsonl", "run", duration=1, sample=lambda _: state)
    rows = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert not any(r["type"] in ("detected", "entry_stopped") for r in rows)
    assert rows[-1]["healthy_baseline"] is False
