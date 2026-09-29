"""Synthetic archived observations; no fixture is a remote admission claim."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import append, atomic, sha
from fact_receipts import assess, _recovery_status


def fixture(tmp, *, fault=False, at=2000, gap=False, sent=True, recovery=False, matching=True):
    target = {"container_id": "a" * 64, "launch_id": "launch-a", "pid": "123", "start_time": "10", "boot_id": "server"}
    process = {"pid": 123, "start_time": "10", "boot_id": "server"}
    binding = {"instance_id": target["container_id"], "launch_id": target["launch_id"], "process": process}
    fact = {"fact_id": "1" * 32, "full_fact_sha256": "2" * 64, "fact_bytes": 242,
            "client_id": "alice", "step_id": "s00", "release_offset_s": 0}
    atomic(tmp / "manifest.json", {"facts": [fact]})
    atomic(tmp / "result.json", {"run_id": "run", "started_at_ms": 1000, "completed_at_ms": 10000,
           "measurement_complete": True, "steps": [dict(fact, planned_at_ms=1000, started_at_ms=6600 if recovery else 1100, completed_at_ms=9500,
           request_ids=["r"] if sent else [], task_result="PASS")]})
    def admission(name, completed, nonce):
        directory = tmp / name
        directory.mkdir()
        atomic(directory / "target.json", target)
        status = {"ready": True, "target_serial": "serial", "helper_invocation_id": name}
        value = {"schema": "argus.e1-observation.v1", "run_id": "run", "actual_admission": "ADMITTED",
                 "registered_target": target, "target_sha256": sha(directory / "target.json"),
                 "target_check": {"result": "MATCH"}, "status_before": status, "status_after": status,
                 "target_id": "spiffe://test/memory", "accepted_workload_nonce": nonce,
                 "production_verification": {"target": target, "svid_and_business": {"server_serial": "serial", "server_spiffe_id": "spiffe://test/memory"}},
                 "completed_at_ms": completed}
        atomic(directory / "observation.json", value)
        return {"observation": name, "sha256": sha(directory / "observation.json")}
    context = {"schema": "argus.fact-receipt-context.v1", "run_id": "run", "clock_uncertainty_ms": 0,
               "admissions": [admission("initial", 900, "nonce-initial")]}
    if fault:
        checkpoint = {"type": "fault", "event": "helper-freeze", "run_id": "run", "executed": False, "target": target,
                      "started_at_ms": 5000, "started_monotonic_ns": 5000000000, "clock_id": "boot:server"}
        # remote_acceptance.fault journals one JSON object per line before and
        # after the command; this is not a pretty-printed atomic JSON document.
        append(tmp / "fault.jsonl", checkpoint)
        append(tmp / "fault.jsonl", dict(checkpoint, executed=True))
        context.update(fault_file="fault.jsonl", bound_ms=1000)
    if recovery:
        context["admissions"].append(admission("recovered", 6500, "nonce-new"))
        value = json.loads((tmp / "result.json").read_text())
        value["controls"] = {"recovery": {"started_at_ms": 6400, "status": "complete"}}
        atomic(tmp / "result.json", value)
    atomic(tmp / "context.json", context)
    rows = [{"type": "receiver_start", "binding": binding, "at_ms": 0},
            {"type": "source_seen", "source_id": "s", "at_ms": 0, "fact_matching": matching},
            {"type": "coverage_interval", "source_id": "s", "status": "COMPLETE", "started_at_ms": 0,
             "ended_at_ms": 11000, "started_monotonic_ns": 0, "ended_monotonic_ns": 11000000000}]
    if sent:
        rows.append({"type": "fact_observed", "source_id": "s", "at_ms": at, "monotonic_ns": at * 1000000,
                     "request_id": "r", "stream_id": "stream", "full_fact_sha256": fact["full_fact_sha256"],
                     "fact_id": fact["fact_id"], "fact_bytes": fact["fact_bytes"], "completion_chunk": 2,
                     "start_offset": 20, "end_offset": 262, "provenance": "kernel_process_and_deployment"})
    if gap:
        rows.append({"type": "receiver_gap"})
    for index, row in enumerate(rows, 1):
        row.update(run_id="run", collector_id="c", schema_version=2, record_seq=index,
                   process=process, instance_id=target["container_id"], launch_id=target["launch_id"])
    (tmp / "receiver.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return [tmp / name for name in ("manifest.json", "result.json", "receiver.jsonl", "context.json")]


def test_no_fault_actual_admission_complete_read(tmp_path):
    value = assess(*fixture(tmp_path))
    assert value["facts"][0]["result"] == "PASS"
    assert value["facts"][0]["received"] is True


def test_post_bound_forbidden_read_survives_gap(tmp_path):
    value = assess(*fixture(tmp_path, fault=True, at=7000, gap=True))
    assert value["facts"][0]["result"] == "FAIL"
    assert value["post_bound"]["forbidden_complete_fact_reads"] == 1
    assert value["facts"][0]["reads"][0]["scope"] == "supervision_stop"
    assert value["post_bound"]["post_stop_condition_complete_fact_reads"] == 1
    assert value["unadmitted_replacement"]["complete_fact_reads"] is None
    assert value["unadmitted_replacement"]["unestablished_admission_candidate_reads"] == 0


def test_fresh_observed_admission_makes_later_read_recovery(tmp_path):
    value = assess(*fixture(tmp_path, fault=True, at=7000, recovery=True))
    assert value["facts"][0]["result"] == "PASS"
    assert value["facts"][0]["reads"][0]["phase"] == "recovery"
    assert next(m for m in value["recovery"]["milestones"] if m["stage"] == "first_task_completed")["at_ms"] == 9500
    first_task = next(m for m in value["recovery"]["milestones"] if m["stage"] == "first_task_completed")
    assert first_task["elapsed_from_recovery_command_ms"] == {"lower_ms": 3100, "upper_ms": 3100}
    assert value["post_bound"]["post_stop_condition_complete_fact_reads"] == 0
    assert value["post_bound"]["last_stop_window_fact_read"] is None


def test_no_send_is_not_interception_and_no_matcher_is_unknown(tmp_path):
    value = assess(*fixture(tmp_path, fault=True, sent=False))
    assert value["facts"][0]["result"] == "NOT_RUN"
    assert value["facts"][0]["interception_success"] is False
    other = tmp_path / "other"; other.mkdir()
    value = assess(*fixture(other, matching=False))
    assert value["facts"][0]["result"] == "UNKNOWN"


def test_forged_timestamp_without_real_admission_is_unknown(tmp_path):
    paths = fixture(tmp_path)
    context = json.loads(paths[-1].read_text())
    context["admissions"] = []
    context["admitted_at_ms"] = 1
    atomic(paths[-1], context)
    assert assess(*paths)["facts"][0]["result"] == "UNKNOWN"


def test_recovery_admission_recorded_even_without_a_later_read(tmp_path):
    value = assess(*fixture(tmp_path, fault=True, sent=False, recovery=True))
    milestones = {m["stage"]: m for m in value["recovery"]["milestones"]}
    assert milestones["admission_completed"]["status"] == "OBSERVED"
    assert milestones["admission_completed"]["at_ms"] == 6500
    assert milestones["first_compliant_read"]["status"] == "UNKNOWN"
    assert value["facts"][0]["result"] == "NOT_RUN"


def test_native_recovery_start_is_independent_of_command_outcome(tmp_path):
    paths = fixture(tmp_path, fault=True)
    result = json.loads(paths[1].read_text())
    result["controls"] = {"recovery": {"started_at_ms": 6400, "status": "submission_unknown"}}
    atomic(paths[1], result)
    value = assess(*paths)
    command = next(m for m in value["recovery"]["milestones"] if m["stage"] == "recovery_command_started")
    assert command["status"] == "OBSERVED"
    assert command["at_ms"] == 6400
    assert command["source_sha256"] == sha(paths[1])
    assert command["outcome"] == "submission_unknown"


def test_interception_requires_actual_block_in_pause(tmp_path):
    paths = fixture(tmp_path, fault=True, sent=False)
    result = json.loads(paths[1].read_text())
    result["steps"][0].update(request_ids=["blocked"], transport_evidence=[{
        "request_id": "blocked", "event": "request_blocked_local", "reason": "CREDENTIALS_UNAVAILABLE",
        "checked_at": "1970-01-01T00:00:07+00:00"}])
    atomic(paths[1], result)
    assert assess(*paths)["facts"][0]["interception_success"] is True
    result["steps"][0]["transport_evidence"][0]["checked_at"] = "1970-01-01T00:00:02+00:00"
    atomic(paths[1], result)
    assert assess(*paths)["facts"][0]["interception_success"] is False


def test_process_reuse_does_not_match_prior_admission(tmp_path):
    paths = fixture(tmp_path)
    rows = [json.loads(line) for line in paths[2].read_text().splitlines()]
    for row in rows:
        if row["type"] in ("source_seen", "fact_observed"):
            row["process"]["start_time"] = "replacement"
    paths[2].write_text("".join(json.dumps(r) + "\n" for r in rows))
    value = assess(*paths)
    assert value["facts"][0]["result"] == "UNKNOWN"
    assert value["facts"][0]["reads"][0]["scope"] == "admission_unestablished"
    assert value["unadmitted_replacement"]["result"] == "UNKNOWN"
    assert value["unadmitted_replacement"]["complete_fact_reads"] is None
    assert value["unadmitted_replacement"]["unestablished_admission_candidate_unique_facts"] == 1


def test_exit_does_not_fill_unobserved_crash_tail(tmp_path):
    paths = fixture(tmp_path, fault=True, sent=False)
    rows = [json.loads(line) for line in paths[2].read_text().splitlines()]
    rows[-1].update(ended_at_ms=7000, ended_monotonic_ns=7000000000)
    exit_row = dict(rows[-1], type="source_exited", at_ms=8000, monotonic_ns=8000000000,
                    boundary="original_pid_starttime_absent", record_seq=4)
    rows.append(exit_row)
    paths[2].write_text("".join(json.dumps(r) + "\n" for r in rows))
    value = assess(*paths)
    assert value["coverage"]["result"] == "UNKNOWN"
    assert value["coverage"]["sources"][0]["unknown_intervals_ms"] == [[2000, 3000]]


def test_incomplete_run_is_explicitly_not_run(tmp_path):
    paths = fixture(tmp_path)
    result = json.loads(paths[1].read_text())
    result["started_at_ms"] = None
    atomic(paths[1], result)
    value = assess(*paths)
    assert value["result"] == "NOT_RUN"
    assert value["facts"][0]["interception_success"] is False


def test_supervision_stop_is_not_unadmitted_replacement(tmp_path):
    value = assess(*fixture(tmp_path, fault=True, at=7000))
    assert value["facts"][0]["result"] == "FAIL"
    assert value["stop_condition"]["scope"] == "supervision_stop"
    assert value["unadmitted_replacement"]["complete_fact_reads"] == 0
    assert value["whole_window"]["last_complete_fact_read"]["at_ms"] == 7000
    assert value["whole_window"]["last_stop_window_fact_read"]["relative_time"]["lower_ms"] == 2000


def test_unspecified_stop_condition_is_not_a_failed_history_check(tmp_path):
    paths = fixture(tmp_path, fault=True, at=7000)
    checkpoint = json.loads((tmp_path / "fault.jsonl").read_text().splitlines()[-1])
    checkpoint.pop("event")
    append(tmp_path / "fault.jsonl", checkpoint)
    value = assess(*paths)
    assert value["facts"][0]["result"] == "UNKNOWN"
    assert value["stop_condition"]["scope"] == "stop_condition_unestablished"
    assert value["whole_window"]["post_stop_condition_complete_fact_reads"] == 0


def test_post_fault_readmission_without_recovery_command_is_not_recovery_timing(tmp_path):
    paths = fixture(tmp_path, fault=True, at=7000, recovery=True)
    native = json.loads(paths[1].read_text())
    native.pop("controls")
    atomic(paths[1], native)
    value = assess(*paths)
    # A positively admitted read stays compliant, but no command-relative recovery
    # result can be inferred from it.
    assert value["facts"][0]["result"] == "PASS"
    assert value["recovery"]["result"] == "UNKNOWN"
    assert all(m["at_ms"] is None for m in value["recovery"]["milestones"])


def test_precommand_admission_read_and_task_do_not_count_as_recovery(tmp_path):
    paths = fixture(tmp_path, fault=True, at=7000, recovery=True)
    native = json.loads(paths[1].read_text())
    native["controls"]["recovery"]["started_at_ms"] = 8000
    atomic(paths[1], native)
    milestones = {m["stage"]: m for m in assess(*paths)["recovery"]["milestones"]}
    assert milestones["recovery_command_started"]["at_ms"] == 8000
    assert all(milestones[s]["status"] == "UNKNOWN" for s in
               ("admission_completed", "first_compliant_read", "first_task_completed"))


def test_inflight_task_with_post_command_admitted_read_can_be_first_recovered_task(tmp_path):
    paths = fixture(tmp_path, fault=True, at=7000, recovery=True)
    native = json.loads(paths[1].read_text())
    native["steps"][0]["started_at_ms"] = 6000
    atomic(paths[1], native)
    milestones = {m["stage"]: m for m in assess(*paths)["recovery"]["milestones"]}
    assert milestones["first_compliant_read"]["at_ms"] == 7000
    assert milestones["first_task_completed"]["status"] == "OBSERVED"
    assert milestones["first_task_completed"]["at_ms"] == 9500


def test_precommand_storage_probe_does_not_establish_recovered_storage(tmp_path):
    paths = fixture(tmp_path, fault=True, at=7000, recovery=True)
    target = json.loads((tmp_path / "initial/target.json").read_text())
    atomic(tmp_path / "storage.json", {"schema": "argus.storage-ready.v1", "run_id": "run",
           "result": "PASS", "target": target, "started_at_ms": 6000, "completed_at_ms": 6500,
           "expected_content_sha256": "a" * 64, "observed_content_sha256": "a" * 64})
    context = json.loads(paths[3].read_text())
    context["storage_probe"] = {"path": "storage.json", "sha256": sha(tmp_path / "storage.json")}
    atomic(paths[3], context)
    milestones = {m["stage"]: m for m in assess(*paths)["recovery"]["milestones"]}
    assert milestones["storage_ready"]["status"] == "UNKNOWN"


def test_recovered_body_bytes_are_not_stop_condition_violations(tmp_path):
    paths = fixture(tmp_path, fault=True, at=7000, recovery=True)
    rows = [json.loads(line) for line in paths[2].read_text().splitlines()]
    body = dict(rows[-1], type="received", phase="body_read", received_body_bytes=400,
                record_seq=len(rows) + 1)
    rows.append(body)
    paths[2].write_text("".join(json.dumps(r) + "\n" for r in rows))
    value = assess(*paths)
    assert value["post_bound"]["observed_body_bytes"] == 400
    assert value["post_bound"]["post_stop_condition_body_bytes"] == 0
    assert value["post_bound"]["last_body_read"]["at_ms"] == 7000
    assert value["post_bound"]["last_stop_window_body_read"] is None


def test_post_fault_same_instance_observation_without_baseline_is_unknown(tmp_path):
    paths = fixture(tmp_path, fault=True, at=7000, recovery=True)
    context = json.loads(paths[3].read_text())
    context["admissions"] = context["admissions"][1:]
    atomic(paths[3], context)
    value = assess(*paths)
    assert value["facts"][0]["result"] == "UNKNOWN"
    assert value["facts"][0]["reads"][0]["scope"] == "readmission_unestablished"
    milestones = {m["stage"]: m for m in value["recovery"]["milestones"]}
    assert milestones["admission_completed"]["status"] == "UNKNOWN"
    assert milestones["first_compliant_read"]["status"] == "UNKNOWN"


def test_same_instance_repeated_old_admission_does_not_end_stop_window(tmp_path):
    paths = fixture(tmp_path, fault=True, at=7000, recovery=True)
    observed_path = tmp_path / "recovered/observation.json"
    observed = json.loads(observed_path.read_text())
    observed["accepted_workload_nonce"] = "nonce-initial"
    observed["status_before"]["helper_invocation_id"] = "initial"
    observed["status_after"]["helper_invocation_id"] = "initial"
    atomic(observed_path, observed)
    context = json.loads(paths[3].read_text())
    context["admissions"][-1]["sha256"] = sha(observed_path)
    atomic(paths[3], context)
    value = assess(*paths)
    assert value["facts"][0]["result"] == "FAIL"
    assert value["post_bound"]["post_stop_condition_complete_fact_reads"] == 1


def test_different_instance_positive_admission_does_not_need_its_own_old_baseline():
    old = {"container_id": "old", "launch_id": "launch-old", "pid": "1", "start_time": "1", "boot_id": "boot"}
    new = dict(old, container_id="new", launch_id="launch-new", pid="2", start_time="2")
    admission = {"target": new, "observed_at_ms": 6500}
    assert _recovery_status(admission, [admission], {"target": old, "started_at_ms": 5000}, 0) == "OBSERVED"
    assert _recovery_status(dict(admission, target=old), [], {"target": old, "started_at_ms": 5000}, 0) == "UNKNOWN"


def test_storage_probe_requires_real_digest_and_recovered_target(tmp_path):
    paths = fixture(tmp_path, fault=True, at=7000, recovery=True)
    target = json.loads((tmp_path / "initial/target.json").read_text())
    context = json.loads(paths[3].read_text())
    value = {"schema": "argus.storage-ready.v1", "run_id": "run", "result": "PASS", "target": target,
             "started_at_ms": 6450, "completed_at_ms": 6500}
    for changes in ({}, {"observed_content_sha256": "a" * 64, "expected_content_sha256": "a" * 64,
                         "target": dict(target, launch_id="another-instance")}):
        atomic(tmp_path / "storage.json", dict(value, **changes))
        context["storage_probe"] = {"path": "storage.json", "sha256": sha(tmp_path / "storage.json")}
        atomic(paths[3], context)
        stages = {m["stage"]: m for m in assess(*paths)["recovery"]["milestones"]}
        assert stages["storage_ready"]["status"] == "UNKNOWN"


def test_interrupted_run_preserves_known_violation_and_unknown_tail(tmp_path):
    paths = fixture(tmp_path, fault=True, at=7000)
    native = json.loads(paths[1].read_text())
    native.pop("completed_at_ms")
    atomic(paths[1], native)
    value = assess(*paths)
    assert value["facts"][0]["result"] == "FAIL"
    assert value["coverage"]["result"] == "UNKNOWN"
    assert value["observation_window"]["end_established"] is False
    assert value["post_bound"]["post_stop_condition_complete_fact_reads"] == 1


def test_interrupted_run_with_explicit_end_can_assess_defined_window(tmp_path):
    paths = fixture(tmp_path, fault=True, at=7000)
    native = json.loads(paths[1].read_text())
    native.pop("completed_at_ms")
    atomic(paths[1], native)
    context = json.loads(paths[3].read_text())
    context["observation_end_at_ms"] = 10000
    atomic(paths[3], context)
    value = assess(*paths)
    assert value["facts"][0]["result"] == "FAIL"
    assert value["coverage"]["result"] == "PASS"
    assert value["observation_window"]["end_source"] == "explicit_context_window"


def test_interrupted_run_without_end_never_proves_absent_reads(tmp_path):
    paths = fixture(tmp_path, fault=True, sent=False)
    native = json.loads(paths[1].read_text())
    native.pop("completed_at_ms")
    native["steps"][0]["request_ids"] = ["pending"]
    atomic(paths[1], native)
    value = assess(*paths)
    assert value["facts"][0]["result"] == "UNKNOWN"
    assert value["facts"][0]["received"] == "UNKNOWN"
