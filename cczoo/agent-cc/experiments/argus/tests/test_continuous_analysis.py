"""Remote test material: task correctness and receiver coverage are independent."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis import mean_ci, paired_difference
from continuous_analysis import summarize, fault_contrasts, bound_result, condition_evidence, collateral_losses
from common import atomic, sha


def step(name, result="PASS", **kwargs):
    return dict(fact_id=name, task_result=result, client_id="c1", step_id=name,
                offered=True, attempted=True, tool_call_count=2, committed=True, recalled=True, **kwargs)


def test_explicit_task_failure_survives_missing_receiver_evidence():
    metrics, result = summarize([step("a", "FAIL"), step("b")])
    assert result["axes"]["task"]["FAIL"] == 1
    assert result["axes"]["receipt"]["UNKNOWN"] == 2
    assert metrics["continuous_task_success_rate"] == .5
    assert metrics["continuous_joint_success_rate"] == 0


def test_all_planned_tasks_and_two_axis_four_cells():
    tasks = [step("a"), step("b", "FAIL"), step("c"), step("d", "FAIL"),
             dict(step("e", "NOT_RUN"), offered=False, attempted=False, tool_call_count=0)]
    receipts = {"facts": [dict(fact_id=name, result=r, received=True, reads=[{}])
                           for name, r in zip("abcd", ("PASS", "PASS", "FAIL", "FAIL"))]}
    metrics, result = summarize(tasks, receipts)
    assert result["joint_receipt_task"] == {"PASS/PASS": 1, "PASS/FAIL": 1, "FAIL/PASS": 1, "FAIL/FAIL": 1}
    assert result["planned_tasks"] == 5
    assert metrics["continuous_joint_success_rate"] == .2
    assert result["counts"]["offered"] == 4


def test_no_tool_invocation_cannot_be_successful_block():
    task = dict(step("a", "FAIL"), tool_call_count=0, attempted=False)
    _, result = summarize([task], {"facts": [{"fact_id": "a", "result": "PASS", "received": False}]})
    assert result["steps"][0]["receipt_result"] == "NOT_RUN"
    assert result["steps"][0]["control_exercised"] is False
    assert result["joint_receipt_task"]["PASS/FAIL"] == 0


def test_pairing_keeps_conditions_and_same_arm_controls_separate():
    row = dict(case="continuous", scale=1, group="full_argus", block_id="s1", workload_kind="continuous_agent_tools",
               workload_spec="protocol", condition="fault", score=.2)
    rows = [row, row | dict(condition="no_fault", score=.8),
            row | dict(group="native_spire_guarded", score=.4),
            row | dict(group="native_spire_guarded", condition="no_fault", score=1)]
    assert paired_difference(rows[:1] + rows[-1:], "native_spire_guarded", "score")["n_runs"] == 0
    contrasts = fault_contrasts(rows, ["score"], mean_ci)
    assert len(contrasts) == 2 and all(c["mean"] == pytest.approx(-.6) and c["n_runs"] == 1 for c in contrasts)
    assert fault_contrasts(rows[:1], ["score"], mean_ci)[0]["n_unpaired_fault"] == 1


def test_continuous_analysis_requires_bound_native_and_full_plan(tmp_path):
    directory = tmp_path / "runs/r"
    path = directory / "continuous/result.json"
    facts = [step("a") | dict(full_fact_sha256="a"*64, fact_bytes=100)]
    atomic(path, dict(schema="argus.continuous-result.v1", run_id="r", operation_id="op", steps=facts, protocol_digest="p"))
    atomic(directory / "continuous/manifest.json", dict(run_id="r", facts=facts))
    value = __import__('common').read(path)
    value['manifest_sha256'] = sha(directory / 'continuous/manifest.json')
    atomic(path, value)
    receipt = directory / "step-result.json"
    atomic(receipt, dict(schema="argus.step.v1", tool="continuous", run_id="r", operation_id="op", source="continuous/result.json", source_sha256=sha(path)))
    atomic(tmp_path / "state.json", {"operations": {"r/op": dict(run_id="r", operation_id="op", phase="submission_unknown",
        evidence="runs/r/step-result.json", evidence_sha256=sha(receipt))}})
    assert bound_result(tmp_path, directory, {"run_id": "r"})["steps"] == facts
    atomic(path, dict(schema="argus.continuous-result.v1", run_id="r", operation_id="op", steps=[], protocol_digest="p"))
    with pytest.raises(ValueError, match="receipt differs"):
        bound_result(tmp_path, directory, {"run_id": "r"})


def test_fault_condition_requires_real_matching_event_in_control_window(tmp_path, monkeypatch):
    import timeline
    path = tmp_path / 'receipt-context.json'
    run = dict(run_id='r', condition='fault', fault_kind='helper-freeze')
    native = dict(fault_kind='helper-freeze', controls={'fault': dict(status='completed', returncode=0,
                                                                 started_at_ms=1000, completed_at_ms=1500)})
    assert condition_evidence(native, run, path)['result'] == 'UNKNOWN'
    atomic(path, dict(fault_file='fault.json', clock_uncertainty_ms=10))
    atomic(tmp_path/'fault.json', {})
    event = dict(run_id='r', executed=True, event='helper-freeze', started_at_ms=1200)
    monkeypatch.setattr(timeline.acceptance, 'fault_checkpoint', lambda p: event)
    assert condition_evidence(native, run, path)['result'] == 'OBSERVED'
    event['event'] = 'target-exit'
    assert condition_evidence(native, run, path)['result'] == 'UNKNOWN'
    event.update(event='helper-freeze', started_at_ms=1800)
    assert condition_evidence(native, run, path)['result'] == 'UNKNOWN'


def test_unexecuted_or_mismatched_condition_is_excluded_from_paired_estimates():
    row = dict(case='continuous', scale=1, group='full_argus', block_id='s1', workload_kind='continuous_agent_tools',
               workload_spec='p', condition='fault', score=1, comparison_eligible=False)
    other = row | dict(condition='no_fault', comparison_eligible=True)
    result = fault_contrasts([row, other], ['score'], mean_ci)[0]
    assert result['n_runs'] == 0 and result['n_unpaired_control'] == 1


def test_unverified_fault_keeps_task_scores_and_receiver_fail_but_not_pass(tmp_path, monkeypatch):
    import continuous_analysis as module
    import fact_receipts
    directory = tmp_path/'runs/r'
    native = dict(result='PASS', protocol_digest='p', steps=[step('a'), step('b')])
    atomic(directory/'continuous/result.json', native)
    atomic(directory/'continuous/receipt-context.json', {'fault_file':'fault.json'})
    (directory/'continuous/receiver.jsonl').write_text('')
    monkeypatch.setattr(module, 'bound_result', lambda *args: native)
    monkeypatch.setattr(module, 'condition_evidence', lambda *args: {'result':'UNKNOWN','reason':'wrong event'})
    raw = {'facts':[{'fact_id':'a','result':'PASS','received':True,'reads':[{}]},
                    {'fact_id':'b','result':'FAIL','received':True,'reads':[{}]}], 'whole_window':None}
    monkeypatch.setattr(fact_receipts, 'assess', lambda *args: raw)
    metrics, detail = module.evidence(tmp_path, directory, dict(run_id='r', group='full_argus', block_id='s1',
        scale=1, condition='fault', fault_kind='helper-freeze'))
    assert metrics['continuous_task_success_rate'] == 1 and not metrics['comparison_eligible']
    assert detail['joint_receipt_task']['PASS/PASS'] == 0
    assert detail['joint_receipt_task']['FAIL/PASS'] == 1
    assert detail['axes']['receipt']['UNKNOWN'] == 1
    assert detail['receiver']['facts'][0]['result'] == 'PASS'  # original diagnostic retained
    assert metrics['unadmitted_replacement_evidence'] == 'UNKNOWN'
    assert metrics['unadmitted_replacement_unique_facts'] is None


def test_shared_fault_never_produces_uninjected_loss():
    row = dict(case='continuous', scale=3, group='full_argus', block_id='s1', workload_kind='continuous_agent_tools',
               workload_spec='p', client_id='c2', condition='fault', fault_kind='helper-freeze', fault_scope='shared_service',
               continuous_task_success_rate=.2, comparison_eligible=True)
    assert collateral_losses([row, row | dict(condition='no_fault', continuous_task_success_rate=.8)], mean_ci) == []


def test_model_mismatch_retains_scores_but_excludes_paired_estimates(tmp_path, monkeypatch):
    import continuous_analysis as module
    directory = tmp_path / 'runs/r'
    native = dict(result='UNKNOWN', protocol_digest='p', steps=[step('a')], model_mismatches=['a'])
    atomic(directory / 'continuous/result.json', native)
    monkeypatch.setattr(module, 'bound_result', lambda *args: native)
    monkeypatch.setattr(module, 'condition_evidence', lambda *args: {'result': 'OBSERVED'})
    metrics, detail = module.evidence(tmp_path, directory, dict(run_id='r', group='full_argus',
        block_id='s1', scale=1, condition='no_fault', fault_kind='helper-freeze'))
    assert metrics['continuous_task_success_rate'] == 1
    assert not metrics['comparison_eligible'] and not detail['comparison_eligible']
    assert detail['comparison_exclusion_reasons'] == ['observed_model_mismatch']
    assert detail['axes']['task']['PASS'] == 1


def test_collateral_loss_is_positive_pp_only_for_predeclared_local_uninjected_client():
    row = dict(case='continuous', scale=3, group='full_argus', block_id='s1', workload_kind='continuous_agent_tools',
               workload_spec='p', client_id='c2', condition='fault', fault_kind='client-stop', fault_scope='local_client',
               injected_client_id='c1', uninjected_client_ids=['c2','c3'], fault_assignment_predeclared=True,
               continuous_task_success_rate=.2, comparison_eligible=True)
    rows = [row, row | dict(condition='no_fault', continuous_task_success_rate=.8)]
    loss = collateral_losses(rows, mean_ci)[0]
    assert loss['mean'] == pytest.approx(60) and loss['unit'] == 'percentage_points'
    assert loss['paired_blocks'][0]['difference'] == pytest.approx(60)
    assert collateral_losses([r | dict(fault_assignment_predeclared=False) for r in rows], mean_ci) == []
    assert collateral_losses([r | dict(client_id='c1') for r in rows], mean_ci) == []
    assert collateral_losses([rows[0], rows[1] | dict(injected_client_id='c3', uninjected_client_ids=['c1','c2'])], mean_ci)[0]['n_runs'] == 0
