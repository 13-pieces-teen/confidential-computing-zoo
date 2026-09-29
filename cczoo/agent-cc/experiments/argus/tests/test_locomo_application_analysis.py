import csv
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis import analyze, mean_ci, paired_difference
from common import atomic, read, sha
from continuous_analysis import fault_contrasts
from locomo_analysis import METRICS, application
from locomo_execution import application_summary


def native_result(*, condition="no_fault", model="model-a", injected=True, timeout=False):
    fixture = {"tasks": [{"task_id": "q1", "source_sample": "a"}, {"task_id": "q2", "source_sample": "b"}]}
    def question(index, injection):
        return dict(status="completed", outcome="completed", source_sample="ab"[index - 1],
                    started_at_ms=1000 + index * 10, finished_at_ms=1200 + index * 10,
                    planned_at_ms=1000, released_at_ms=1000, deadline_at_ms=2000,
                    deadline_missed=False, duration_ms=150, invocation_duration_ms=200,
                    provider="provider", model=model,
                    injection={"code": "INJECTION_OBSERVED" if injection else "CONTEXT_NOT_INJECTED",
                               "request_audit_complete": True,
                               "requests": [{"request_id": "r" + str(index), "outcome": "completed", "duration_ms": 10,
                                             "started_at_ms": 1050, "at_ms": 1060}]})
    status = "completed" if condition == "fault" else "no_fault"
    controls = {key: {"status": status, "returncode": 0, "planned_at_ms": at,
                      "started_at_ms": at, "completed_at_ms": at + 1} for key, at in (("fault", 1001), ("recovery", 1005))}
    state = {"questions": {"q1": question(1, True), "q2": question(2, injected)}, "controls": controls}
    if timeout:
        state["questions"]["q2"].update(status="UNKNOWN", outcome="timeout", injection={"code": "REQUEST_TIMEOUT"})
    score = dict(answered_mean=0, measured=2, eligible=2, all_tasks_zero_for_uncompleted=0)
    result = dict(schema="argus.locomo-result.v1", result="INCOMPLETE" if timeout else "COMPLETE", run_id="trial", operation_id="op",
                  source_sha256="a" * 64, selection={"seed": 1}, protocol="derived-private-read-only", cluster_count=2,
                  conversation_macro_f1=0, overall=dict(tasks=2, status_counts={"completed": 1, "UNKNOWN": 1} if timeout else {"completed": 2},
                  token_f1=score, abstention=score), by_category={}, by_conversation={},
                  application=application_summary(fixture, state), observed_models=[["provider", model]],
                  execution=dict(condition=condition, concurrent_clients=True, schedule={"release_interval_s": 1, "deadline_s": 5},
                                 qa_timeout_seconds=5, control_times={k: {"at_s": i, "timeout_s": 2} for k, i in (("fault", 1), ("recovery", 2))},
                                 controls=controls, qa_phase="complete", qa_started_at_ms=1000, qa_completed_at_ms=1220),
                  questions=[dict(row, task_id=tid, injection=row["injection"]["code"]) for tid, row in state["questions"].items()])
    return result


def bind(root, native):
    native_path = root / "runs/trial/locomo/result.json"
    receipt = root / "runs/trial/step-result.json"
    atomic(native_path, native)
    atomic(receipt, dict(schema="argus.step.v1", tool="locomo", result="PASS", run_id="trial", operation_id="op",
                        evidence_scope="application_workload_completion_not_security", native_result=native["result"],
                        source="locomo/result.json", source_sha256=sha(native_path)))
    atomic(root / "state.json", {"operations": {"trial/locomo": dict(run_id="trial", operation_id="op", phase="complete",
                    evidence="runs/trial/step-result.json", evidence_sha256=sha(receipt))}})
    run = dict(run_id="trial", case="locomo", planned_tasks=2, scale=2, group="full_argus", block_id="seed1",
               condition=native["execution"]["condition"], fault_kind="helper_freeze", result="PASS")
    atomic(root / "verdicts.json", {"runs": [run]})
    return run


def read_csv(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def test_application_metrics_not_f1_or_security_and_export_actual_timelines(tmp_path):
    bind(tmp_path, native_result(injected=False))
    analyze(tmp_path)
    data = read(tmp_path / "analysis.json")
    summary = data["summaries"][0]
    assert summary["locomo_valid_completion_rate"]["mean"] == .5
    assert summary["locomo_completion_rate"]["mean"] == 1
    assert summary["locomo_task_mean_ms"]["mean"] == 200  # adapter E2E, not the 150-ms CLI inner duration
    assert summary["locomo_request_p95_ms"]["mean"] == 10
    assert summary["locomo_max_in_flight"]["mean"] == 2
    assert summary["locomo_conversation_macro_f1"]["mean"] == 0
    assert summary["pass_value"]["mean"] is None
    assert data["locomo_results"][0]["delivery_compliance"] == "NOT_ASSESSED_BY_QA"
    assert data["locomo_totals"]["all_planned_tasks_known"] == 2
    tasks = read_csv(tmp_path / "locomo-task-timeline.csv")
    assert len(tasks) == 2 and tasks[1]["injection"] == "CONTEXT_NOT_INJECTED"
    assert {r["control"] for r in read_csv(tmp_path / "locomo-control-timeline.csv")} == {"fault", "recovery"}
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert report.index("LoCoMo application measurements") < report.index("Auxiliary QA quality")
    assert "measurement window completed" in report


def as_row(native, **extra):
    row = dict(run_id="trial", case="locomo", scale=2, group="full_argus", block_id="seed1",
               workload_kind="locomo_derived", connection_mode="not_applicable", fault_kind="helper_freeze")
    row.update(extra)
    metrics, _ = application(native, row)
    return row | metrics


def test_pairing_requires_actual_model_schedule_and_control_timeout():
    full = as_row(native_result())
    equal = as_row(native_result(), group="native_spire_guarded")
    metric = "locomo_valid_completion_rate"
    assert paired_difference([full, equal], "native_spire_guarded", metric)["n_runs"] == 1
    for changed in ("model", "schedule", "control_timeout"):
        native = native_result()
        if changed == "model": native["observed_models"] = [["provider", "model-b"]]
        if changed == "schedule": native["execution"]["schedule"]["deadline_s"] = 6
        if changed == "control_timeout": native["execution"]["control_times"]["fault"]["timeout_s"] = 3
        other = as_row(native, group="native_spire_guarded")
        assert paired_difference([full, other], "native_spire_guarded", metric)["n_runs"] == 0


def test_same_arm_fault_contrast_matches_protocol_but_retains_ineligible_observations(tmp_path):
    control = as_row(native_result())
    fault = as_row(native_result(condition="fault", injected=False))
    contrast = fault_contrasts([control, fault], ["locomo_valid_completion_rate"], mean_ci)[0]
    assert contrast["n_runs"] == 1 and contrast["mean"] == -.5
    for bad_status in ("failed", "submission_unknown", None):
        native = native_result(condition="fault", injected=False)
        native["execution"]["controls"]["fault"]["status"] = bad_status
        row = as_row(native)
        assert row["locomo_valid_completion_rate"] == .5 and row["comparison_eligible"] is False
        assert fault_contrasts([control, row], ["locomo_valid_completion_rate"], mean_ci)[0]["n_runs"] == 0
    native = native_result(condition="fault", injected=False)
    native["observed_models"] = []
    bind(tmp_path, native)
    analyze(tmp_path)
    summary = read(tmp_path / "analysis.json")["summaries"][0]
    assert summary["comparison_excluded_runs"] == 1
    assert summary["locomo_valid_completion_rate"]["mean"] == .5


def test_complete_measurement_window_with_failed_task_is_not_all_tasks_pass(tmp_path):
    bind(tmp_path, native_result(timeout=True))
    analyze(tmp_path)
    data = read(tmp_path / "analysis.json")
    summary = data["summaries"][0]
    assert summary["verdicts"]["PASS"] == 1  # The runner finished measuring.
    assert summary["locomo_completion"]["INCOMPLETE"] == 1
    assert summary["locomo_completion_rate"]["mean"] == .5
    assert summary["locomo_valid_completion_rate"]["mean"] == .5
    assert summary["pass_value"]["mean"] is None
    row = read_csv(tmp_path / "statistics.csv")[0]
    assert row["locomo_outcome_timeout"] == "1"


@pytest.mark.parametrize("status", ["NOT_RUN", "UNKNOWN"])
def test_missing_native_retains_all_planned_tasks_without_fabricated_metrics(tmp_path, status):
    atomic(tmp_path / "verdicts.json", {"runs": [dict(run_id="absent", case="locomo", scale=3, group="full_argus",
        block_id="seed1", result=status, planned_tasks=6)]})
    analyze(tmp_path)
    data = read(tmp_path / "analysis.json")
    assert data["locomo_totals"]["all_planned_tasks_known"] == 6
    assert data["locomo_totals"]["tasks_without_bound_run_evidence"] == 6
    assert data["summaries"][0]["locomo_valid_completion_rate"]["mean"] is None
    tasks = read_csv(tmp_path / "locomo-task-timeline.csv")
    assert len(tasks) == 6 and {r["status"] for r in tasks} == {status}
    assert all(not r["task_id"] and not r["started_at_ms"] for r in tasks)


def test_legacy_results_keep_qa_but_do_not_invent_application_measurements(tmp_path):
    native = native_result()
    native.pop("application")
    bind(tmp_path, native)
    analyze(tmp_path)
    data = read(tmp_path / "analysis.json")
    assert data["summaries"][0]["locomo_conversation_macro_f1"]["mean"] == 0
    assert all(data["summaries"][0][name]["mean"] is None for name in METRICS)
    assert data["locomo_totals"]["tasks_without_application_measurements"] == 2


def test_application_count_tampering_and_original_hash_binding_are_rejected(tmp_path):
    native = native_result()
    native["application"]["planned"] = 3
    bind(tmp_path, native)
    analyze(tmp_path)
    summary = read(tmp_path / "analysis.json")["summaries"][0]
    assert summary["locomo_completion"]["UNKNOWN"] == 1
    assert summary["locomo_valid_completion_rate"]["mean"] is None
    bind(tmp_path, native_result())
    path = tmp_path / "runs/trial/locomo/result.json"
    changed = read(path)
    changed["application"]["invocation_latency"]["mean_ms"] = 0
    atomic(path, changed)
    analyze(tmp_path)
    assert read(tmp_path / "analysis.json")["summaries"][0]["locomo_task_mean_ms"]["mean"] is None


def test_application_timeline_plots_regenerate_from_analysis(tmp_path):
    pytest.importorskip("matplotlib")
    from plot import render
    bind(tmp_path, native_result(condition="fault"))
    analyze(tmp_path)
    # Keep this test about timeline and one quantitative figure rather than every metric.
    data = read(tmp_path / "analysis.json")
    for metric in list(data["summaries"][0]):
        if isinstance(data["summaries"][0][metric], dict) and "n_runs" in data["summaries"][0][metric] and metric != "locomo_task_mean_ms":
            del data["summaries"][0][metric]
    atomic(tmp_path / "analysis.json", data)
    figures = render(tmp_path)
    assert figures["result"] == "GENERATED"
    assert len(figures["files"]) == 4
    assert any("locomo-timeline" in item["path"] for item in figures["files"])
    assert all(sha(tmp_path / item["path"]) == item["sha256"] for item in figures["files"])
