import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis import analyze, paired_difference, summarize_requests
from common import atomic, read, sha


def request(mode="new", kind="status_api", **extra):
    return dict(phase="measurement", outcome="success", latency_ms=10,
                connection_mode=mode, workload_kind=kind, **extra)


def test_request_strata_cannot_mix_status_memory_or_new_reuse():
    for rows in ([request(), request("reuse")], [request(), request(kind="memory_query")]):
        with pytest.raises(ValueError, match="mixed"): summarize_requests(rows, 1)
    with pytest.raises(ValueError, match="mixed"):
        summarize_requests([request(), {"phase": "measurement", "outcome": "success", "latency_ms": 5}], 1)


def test_nonempty_goodput_and_connection_cost_exclude_empty_replies_and_reuse_zero():
    fields = dict(connection_attempted=True, connection_created=True, connection_reused=False, reconnect=False,
                  tcp_connect_ms=2, tls_handshake_ms=3, connect_ms=6, api_ms=4)
    first = request(kind="memory_query", memory_result="nonempty", memory_leaf_count=2, **fields)
    reused = first | dict(connection_attempted=False, connection_created=False, connection_reused=True,
                         tcp_connect_ms=0, tls_handshake_ms=0, connect_ms=0, memory_result="empty", memory_leaf_count=0)
    result = summarize_requests([first, reused], 2)
    assert result["api_goodput_rps"] == 1
    assert result["memory_nonempty_goodput_rps"] == .5
    assert result["tcp_connect_p50_ms"] == 2
    assert result["tcp_connect_p50_ms_samples"] == 1
    assert result["connection_reuse_fraction"] == .5
    with pytest.raises(ValueError, match="leaf count"):
        summarize_requests([first | dict(memory_leaf_count=0)], 2)


def test_pairing_cannot_match_different_connections_or_workloads():
    full = dict(block_id="seed1", case="load", scale=1, group="full_argus", v=10,
                connection_mode="new", workload_kind="status_api")
    other = full | dict(group="native_spire_guarded", v=1, connection_mode="reuse")
    assert paired_difference([full, other], "native_spire_guarded", "v")["n_runs"] == 0
    other["connection_mode"] = "new"
    assert paired_difference([full, other], "native_spire_guarded", "v")["mean"] == 9


def prepare_locomo(root, complete=True):
    directory = root / "runs/trial"
    native = directory / "locomo/result.json"
    receipt = directory / "step-result.json"
    measured = 2 if complete else 1
    scoring = dict(answered_mean=0.0, measured=measured, eligible=2, all_tasks_zero_for_uncompleted=0.0)
    abstention = dict(answered_mean=None, measured=0, eligible=0, all_tasks_zero_for_uncompleted=None)
    result = dict(schema="argus.locomo-result.v1", result="COMPLETE" if complete else "INCOMPLETE", run_id="trial", operation_id="op",
                  source_sha256="a" * 64, selection={"seed": 1}, cluster_count=2, conversation_macro_f1=0.0,
                  overall=dict(tasks=2, status_counts={"completed": measured, "NOT_RUN": 2-measured}, token_f1=scoring, abstention=abstention),
                  by_category={"1": scoring}, by_conversation={"a": scoring}, conversation_bootstrap_95=[0, 0])
    atomic(native, result)
    envelope = dict(schema="argus.step.v1", tool="locomo", result="PASS" if complete else "UNKNOWN",
                    run_id="trial", operation_id="op", native_result=result["result"],
                    evidence_scope="application_workload_completion_not_security", source="locomo/result.json", source_sha256=sha(native))
    atomic(receipt, envelope)
    atomic(root / "state.json", {"operations": {"trial/locomo": dict(run_id="trial", operation_id="op", phase="complete" if complete else "submission_unknown",
            evidence="runs/trial/step-result.json", evidence_sha256=sha(receipt))}})
    atomic(root / "verdicts.json", {"runs": [dict(run_id="trial", case="memory", scale=1, group="full_argus", block_id="seed1", result=envelope["result"])]})
    return native, receipt


def test_locomo_step_pass_is_completion_not_qa_accuracy_or_security(tmp_path):
    prepare_locomo(tmp_path)
    analyze(tmp_path)
    result = read(tmp_path / "analysis.json")
    summary = result["summaries"][0]
    assert summary["locomo_completion"]["COMPLETE"] == 1
    assert summary["locomo_conversation_macro_f1"]["mean"] == 0
    assert summary["locomo_coverage"]["mean"] == 1
    assert summary["pass_value"]["mean"] is None
    assert summary["verdict_scope"] == "application_workload_completion_not_security"
    assert result["locomo_results"][0]["delivery_compliance"] == "NOT_ASSESSED_BY_QA"


def test_incomplete_locomo_keeps_explicit_coverage_and_measured_scores(tmp_path):
    prepare_locomo(tmp_path, False)
    analyze(tmp_path)
    summary = read(tmp_path / "analysis.json")["summaries"][0]
    assert summary["locomo_completion"]["INCOMPLETE"] == 1
    assert summary["locomo_coverage"]["mean"] == .5
    assert summary["locomo_conversation_macro_f1"]["mean"] == 0
    assert summary["verdicts"]["UNKNOWN"] == 1
    assert summary["pass_value"]["mean"] is None


@pytest.mark.parametrize("change", ["native", "runner", "operation", "receipt"])
def test_stale_or_unbound_locomo_never_contributes_scores(tmp_path, change):
    native, receipt = prepare_locomo(tmp_path)
    if change == "native": atomic(native, read(native) | {"conversation_macro_f1": 1})
    if change == "runner": (tmp_path / "state.json").unlink()
    if change == "operation":
        state = read(tmp_path / "state.json"); state["operations"]["trial/locomo"]["operation_id"] = "different"; atomic(tmp_path / "state.json", state)
    if change == "receipt": atomic(receipt, read(receipt) | {"source_sha256": "b" * 64})
    analyze(tmp_path)
    summary = read(tmp_path / "analysis.json")["summaries"][0]
    assert summary["locomo_completion"]["UNKNOWN"] == 1
    assert summary["locomo_conversation_macro_f1"]["mean"] is None


def test_analysis_does_not_merge_load_strata_with_same_case_and_seed(tmp_path):
    runs = []
    for mode in ("new", "reuse"):
        rid = "trial-" + mode
        directory = tmp_path / "runs" / rid
        directory.mkdir(parents=True)
        raw = directory / "requests.jsonl"
        raw.write_text(json.dumps(request(mode) | dict(run_id=rid, request_id=rid)) + "\n")
        atomic(directory / "load-result.json", dict(run_id=rid, connection_mode=mode, workload_kind="status_api",
                requests_sha256=sha(raw), measurement_complete=True, measurement_seconds=1))
        runs.append(dict(run_id=rid, block_id="seed1", case="load", scale=1, group="full_argus", result="PASS"))
    atomic(tmp_path / "verdicts.json", {"runs": runs})
    analyze(tmp_path)
    summaries = read(tmp_path / "analysis.json")["summaries"]
    assert len(summaries) == 2
    assert {s["connection_mode"] for s in summaries} == {"new", "reuse"}
