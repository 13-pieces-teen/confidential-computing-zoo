import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import atomic
from continuous_gate import gate_receipt


def runner(condition='no_fault'):
    result = {
        'schema':'argus.continuous-result.v1','scenario':'work-item-v1',
        'run_id':'e4p2-h1-test','operation_id':'operation','condition':condition,
        'planned_tasks':6,'steps':[
            {'step_id':'s00','committed':True,'completed_at_ms':100,
             'dispatch_started_at_ms':50},
            {'step_id':'s01','committed':True,'completed_at_ms':200,
             'dispatch_started_at_ms':150},
            {'step_id':'s02','committed':True,'completed_at_ms':350,
             'dispatch_started_at_ms':320},
            {'step_id':'s03','committed':True,'completed_at_ms':500,
             'dispatch_started_at_ms':450},
            {'step_id':'s04','committed':True,'completed_at_ms':700,
             'dispatch_started_at_ms':650},
            {'step_id':'s05','committed':True,'completed_at_ms':800,
             'dispatch_started_at_ms':750}],
        'controls':{
            'fault':{'started_at_ms':250,'completed_at_ms':300,'status':'completed','returncode':0},
            'recovery':{'started_at_ms':600,'completed_at_ms':620,'status':'completed','returncode':0}},
        'gates':{'window_completion':'PASS','service_recovery':
                 'NOT_APPLICABLE' if condition == 'no_fault' else 'PASS',
                 'task_correctness':'PASS','receiver_coverage':'UNKNOWN'},
        'work_items':[{'complete_task_result':'PASS','continuation_result':'PASS',
                       'unresolved_proposals':[]}]}
    for index, step in enumerate(result['steps']):
        step.update(task_result='PASS',attempted=True,request_ids=[f'request-{index}'],
                    transport_evidence=[{'request_id':f'request-{index}','http_status':200}],
                    tool_events=[{'event':'tool_started','tool_name':'memory_store'}])
    return result


def server():
    return {'schema':'argus.e4-server-receipt.v1','run_id':'e4p2-h1-test',
            'operation_id':'operation','receiver_coverage':'PASS','request_count':12,
            'receiver_sha256':'a'*64,'correlation_sha256':'b'*64,
            'legal_recovery_result':'PASS',
            'matched_request_ids':[f'request-{index}' for index in range(6)]}


def write_inputs(tmp_path, result, receipt):
    result_path, server_path = tmp_path/'result.json', tmp_path/'server.json'
    atomic(result_path,result); atomic(server_path,receipt)
    return result_path,server_path


def test_healthy_gate_binds_complete_task_and_receiver(tmp_path):
    paths=write_inputs(tmp_path,runner(),server())
    receipt=gate_receipt(*paths)
    assert receipt['schema']=='argus.healthy-pilot-gate.v1'
    assert receipt['service_recovery']=='NOT_APPLICABLE'
    assert receipt['task_correctness']==receipt['receiver_coverage']=='PASS'
    assert len(receipt['runner_result_sha256'])==len(receipt['server_receipt_sha256'])==64


def test_fault_gate_requires_pre_fault_state_post_fault_attempt_and_recovery(tmp_path):
    paths=write_inputs(tmp_path,runner('fault'),server())
    receipt=gate_receipt(*paths)
    assert receipt['schema']=='argus.fault-pilot-gate.v1'
    assert all(receipt[name]=='PASS' for name in
               ('pre_fault_confirmed_state','post_fault_attempt','legal_recovery',
                'correct_continuation','service_recovery'))
    assert receipt['unresolved_proposals']==[]


def test_missing_receiver_or_unknown_proposal_cannot_pass(tmp_path):
    result, receipt=runner('fault'),server()
    result['work_items'][0]['unresolved_proposals']=['s02']
    receipt['request_count']=0
    paths=write_inputs(tmp_path,result,receipt)
    gate=gate_receipt(*paths)
    assert gate['receiver_coverage']=='UNKNOWN'
    assert gate['correct_continuation']=='UNKNOWN'
    assert gate['unresolved_proposals']==['s02']


def test_no_post_fault_dispatch_cannot_pass(tmp_path):
    result=runner('fault')
    for step in result['steps']:
        step['dispatch_started_at_ms']=700
    paths=write_inputs(tmp_path,result,server())
    assert gate_receipt(*paths)['post_fault_attempt']=='UNKNOWN'


def test_unmatched_transport_request_cannot_pass_receiver_gate(tmp_path):
    receipt=server()
    receipt['matched_request_ids'].remove('request-5')
    paths=write_inputs(tmp_path,runner(),receipt)
    gate=gate_receipt(*paths)
    assert gate['task_correctness']=='PASS'
    assert gate['receiver_coverage']=='UNKNOWN'
