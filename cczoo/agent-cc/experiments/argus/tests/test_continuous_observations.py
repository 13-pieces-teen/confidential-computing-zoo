import copy
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from continuous_observations import annotate_phases, join_work_items


def test_late_dispatch_and_tool_call_do_not_inherit_planned_fault_phase():
    step={'phase':'pause','released_at_ms':2500,'tool_events':[{
        'event':'tool_started','tool_name':'memory_store','checked_at':'1970-01-01T00:00:02.600Z'}]}
    value={'condition':'fault','controls':{'recovery':{'started_at_ms':2000}}}
    result=annotate_phases([step],value,{'fault_started_at_ms':1000},10)[0]
    assert result['planned_phase']=='pause'
    assert result['actual_dispatch_phase']=='after_recovery_command'
    assert result['actual_tool_phases']==['after_recovery_command']
    assert annotate_phases([step],value,{},10)[0]['actual_dispatch_phase']=='UNKNOWN'
    assert annotate_phases([step],value,{'fault_started_at_ms':1000},None)[0]['actual_dispatch_phase']=='UNKNOWN'
    step['released_at_ms']=2005
    assert annotate_phases([step],value,{'fault_started_at_ms':1000},10)[0]['actual_dispatch_phase']=='boundary'


def recovery_fixture():
    value={'run_id':'r','condition':'fault','work_items':[{'work_item_id':'w','client_id':'c',
           'complete_task_result':'FAIL','continuation_result':'PASS'}],
           'steps':[{'work_item_id':'w','client_id':'c','step_id':'s04','fact_id':'f','task_result':'PASS',
                     'committed':True,'request_ids':['req'],'answer_at_ms':3500,
                     'completed_at_ms':3500,'goal_confirmed_at_ms':4000}]}
    receipt={'run_id':'r','recovery':{'anchor_at_ms':1000,'clock_uncertainty_ms':10,
             'milestones':[{'stage':'admission_completed','status':'OBSERVED','at_ms':2000,'source_sha256':'a'}]},
             'facts':[{'client_id':'c','step_id':'s04','fact_id':'f','reads':[{'result':'PASS','phase':'recovery',
                       'at_ms':3000,'request_id':'req','instance_id':'instance-new','admission_source_sha256':'a'}]}]}
    return value,receipt,{'result':'OBSERVED'}


def test_legal_continuation_reuses_admission_read_and_task_without_repairing_requirement_loss():
    value,receipt,condition=recovery_fixture()
    original=copy.deepcopy(value)
    result=join_work_items(value,receipt,condition)[0]
    assert result['legal_recovery_result']=='PASS'
    assert result['complete_task_result']=='FAIL'
    assert result['recovery_intervals']['admission_to_first_legal_read_ms']=={'lower_ms':980,'upper_ms':1020}
    assert result['legal_recovery_evidence']['step_id']=='s04'
    assert result['legal_recovery_evidence']['correct_continuation_at_ms']==4000
    assert value==original


@pytest.mark.parametrize('mismatch',['run','client','step','request','missing_request','admission','missing_admission',
                                    'task','commit','clock','condition','confirmation','answer','confirmation_before_answer'])
def test_legal_recovery_missing_or_mismatched_evidence_remains_unknown(mismatch):
    value,receipt,condition=recovery_fixture()
    if mismatch=='run': receipt['run_id']='other'
    if mismatch=='client': receipt['facts'][0]['client_id']='other'
    if mismatch=='step': receipt['facts'][0]['step_id']='other'
    if mismatch=='request': value['steps'][0]['request_ids']=['different-request']
    if mismatch=='missing_request': value['steps'][0].pop('request_ids')
    if mismatch=='admission': receipt['facts'][0]['reads'][0]['admission_source_sha256']='other'
    if mismatch=='missing_admission': receipt['recovery']['milestones']=[]
    if mismatch=='task': value['steps'][0]['task_result']='FAIL'
    if mismatch=='commit': value['steps'][0]['committed']='UNKNOWN'
    if mismatch=='clock': receipt['recovery']['clock_uncertainty_ms']=None
    if mismatch=='condition': condition['result']='UNKNOWN'
    if mismatch=='confirmation': value['steps'][0]['goal_confirmed_at_ms']=None
    if mismatch=='answer': value['steps'][0].pop('answer_at_ms')
    if mismatch=='confirmation_before_answer': value['steps'][0]['goal_confirmed_at_ms']=3499
    assert join_work_items(value,receipt,condition)[0]['legal_recovery_result']=='UNKNOWN'


@pytest.mark.parametrize('answer_at', [500, 1500, 2500, 3000, 3019])
def test_late_archive_confirmation_cannot_upgrade_an_old_or_unordered_answer(answer_at):
    value,receipt,condition=recovery_fixture()
    value['steps'][0].update(answer_at_ms=answer_at,completed_at_ms=answer_at)
    result=join_work_items(value,receipt,condition)[0]
    assert result['legal_recovery_result']=='UNKNOWN'
    assert not result['recovery_intervals']


@pytest.mark.parametrize('at_ms,expected', [(2015,'UNKNOWN'),(2019,'UNKNOWN'),(2020,'PASS')])
def test_admission_to_read_order_requires_two_endpoint_clock_bounds(at_ms,expected):
    value,receipt,condition=recovery_fixture()
    receipt['facts'][0]['reads'][0]['at_ms']=at_ms
    assert join_work_items(value,receipt,condition)[0]['legal_recovery_result']==expected


def test_answer_after_linked_read_uses_clock_bound_but_confirmation_for_latency():
    value,receipt,condition=recovery_fixture()
    value['steps'][0]['answer_at_ms']=3020
    result=join_work_items(value,receipt,condition)[0]
    assert result['legal_recovery_result']=='PASS'
    assert result['legal_recovery_evidence']['answer_at_ms']==3020
    assert result['legal_recovery_evidence']['correct_continuation_at_ms']==4000
    assert result['recovery_intervals']['first_legal_read_to_correct_continuation_ms']=={'lower_ms':980,'upper_ms':1020}


def test_no_fault_has_no_invented_recovery_success():
    value,receipt,condition=recovery_fixture(); value['condition']='no_fault'
    result=join_work_items(value,receipt,condition)[0]
    assert result['legal_recovery_result']=='NOT_RUN' and not result['recovery_intervals']


def test_export_keeps_work_item_and_tool_evidence_as_separate_tables(tmp_path):
    import csv
    import json
    from continuous_analysis import export
    value,receipt,condition=recovery_fixture()
    item=join_work_items(value,receipt,condition)[0]
    step=dict(value['steps'][0],phase='pause',planned_phase='pause',actual_dispatch_phase='after_recovery_command',
              actual_tool_phases=['after_recovery_command'],tool_events=[{'event':'tool_started','tool_name':'memory_store',
               'at_ms':3000,'actual_phase':'after_recovery_command'}])
    export(tmp_path,[dict(run_id='r',group='full_argus',condition='fault',block_id='b',steps=[step],work_items=[item])])
    with (tmp_path/'continuous-work-items.csv').open(newline='') as stream: work,=list(csv.DictReader(stream))
    assert work['complete_task_result']=='FAIL' and work['legal_recovery_result']=='PASS'
    assert json.loads(work['recovery_intervals'])['admission_to_first_legal_read_ms']['lower_ms']==980
    with (tmp_path/'continuous-tool-events.csv').open(newline='') as stream: event,=list(csv.DictReader(stream))
    assert event['planned_phase']=='pause' and event['actual_phase']=='after_recovery_command'
