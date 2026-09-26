import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fleet_fault as target
from common import atomic, read, sha


def fixture(monkeypatch, tmp_path):
    c = {'run_id': 'trial', 'event': 'client-stop', 'instance': 'a', 'baseline_rounds': 3,
         'observation_rounds': 5, 'recovery_rounds': 3, 'interval_seconds': 0, 'settle_seconds': 0}
    items = [{'name': n} for n in ('a', 'b', 'c')]
    keys = {n: 'never-output-this' for n in ('a', 'b', 'c')}
    facts = {n: {} for n in ('a', 'b', 'c')}
    monkeypatch.setattr(target, 'settings', lambda _: (c, items, keys, facts, 'fingerprint'))
    return c


def test_unknown_fault_cannot_be_replayed(monkeypatch, tmp_path):
    c = fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(target, 'observe', lambda i, *args: {'run_id': 'trial', 'instance_id': i['name'],
        'phase': args[3], 'sequence': args[4], 'started_at_ms': 10, 'completed_at_ms': 11, 'outcome': 'available'})
    calls = []
    def fail(*args):
        calls.append(1)
        raise TimeoutError('unknown remote outcome')
    monkeypatch.setattr(target, 'inject', fail)
    with pytest.raises(TimeoutError): target.run('unused', tmp_path, execute_fault=True)
    assert read(tmp_path / 'state.json')['phase'] == 'fault_submission_unknown'
    assert target.run('unused', tmp_path, 'collect')['result'] == 'UNKNOWN'
    with pytest.raises(ValueError): target.run('unused', tmp_path, execute_fault=True)
    with pytest.raises(ValueError): target.run('unused', tmp_path, 'observe-recovery')
    assert calls == [1]


def test_parallel_clients_and_explicit_recovery_keep_fault_result(monkeypatch, tmp_path):
    c = fixture(monkeypatch, tmp_path)
    faulted = False
    observed = []
    def probe(item, key, fact, run_id, phase, number):
        observed.append((item['name'], phase))
        return {'run_id': run_id, 'instance_id': item['name'], 'phase': phase, 'sequence': number,
                'started_at_ms': target.now(), 'completed_at_ms': target.now(),
                'outcome': 'unknown' if faulted and phase == 'fault' and item['name'] == 'a' else 'available'}
    def inject(*args):
        nonlocal faulted
        faulted = True
        return {'verified': True, 'completed_at_ms': 0}
    monkeypatch.setattr(target, 'observe', probe)
    monkeypatch.setattr(target, 'inject', inject)
    monkeypatch.setattr(target, 'question', lambda *args: {'result': 'FAIL'})
    value = target.run('unused', tmp_path, execute_fault=True)
    assert value['delivery_compliance'] == 'NOT_RUN'
    assert {n for n, p in observed if p == 'fault'} == {'a', 'b', 'c'}
    value = target.run('unused', tmp_path, 'observe-recovery')
    assert value['result'] == 'FAIL'
    assert value['phase'] == 'completed'
    assert not any('never-output-this' in p.read_text() for p in tmp_path.rglob('*.json'))


def test_unknown_question_is_not_submitted_again(tmp_path):
    atomic(tmp_path / 'question.json', {'result': 'UNKNOWN', 'phase': 'submission_unknown'})
    assert target.question({}, '', {}, 'trial', tmp_path)['result'] == 'UNKNOWN'


@pytest.mark.parametrize('change,outcome', [
    ({}, 'unavailable'),
    ({'network_error':'ECONNRESET'}, 'unavailable'),
    ({'phase':'response_body', 'network_error':'ECONNRESET'}, 'unavailable'),
    ({'phase':'response_body'}, 'unknown'),
    ({'network_error':'ETIMEDOUT'}, 'unknown'),
    ({'network_error':'CERT_HAS_EXPIRED'}, 'unknown'),
    ({'phase':'configuration'}, 'unknown'),
    ({'result':'UNKNOWN'}, 'unknown'),
    ({'request_id':None}, 'unknown'),
])
def test_only_native_observed_connection_failures_are_unavailable(monkeypatch, change, outcome):
    observation = {'result':'UNAVAILABLE', 'code':'CONNECTION_UNAVAILABLE', 'network_error':'ECONNREFUSED',
                   'phase':'https_request', 'request_id':'native-request-id'} | change
    monkeypatch.setattr(target.business, 'probe', lambda *args, **kwargs: observation)
    row = target.observe({'name':'a'}, 'secret', {'marker':'marker', 'fact_sha256':'a'*64}, 'trial', 'fault', 0)
    assert row['outcome'] == outcome
    assert row['network_error'] == (observation['network_error'] if outcome == 'unavailable' else None)


def test_partial_observation_not_counted_as_zero(monkeypatch, tmp_path):
    c = fixture(monkeypatch, tmp_path)
    (tmp_path / 'fault.jsonl').write_text(json.dumps({'run_id': 'trial'}) + '\n')
    value = target.summarize(c, tmp_path, {'phase': 'observing', 'fault': {'verified': True}})
    assert value['coverage']['fault'] == 'UNKNOWN'
    assert value['result'] == 'UNKNOWN'


def sealed(directory, phase, outcomes, *, start=100, missing=None):
    rows = []
    for number in range(3):
        for name, outcome in outcomes.items():
            if missing == (name,number): continue
            rows.append({'run_id':'trial','instance_id':name,'phase':phase,'sequence':number,
                         'started_at_ms':start+number*10,'completed_at_ms':start+number*10+1,'outcome':outcome})
    path = directory/(phase+'.jsonl'); path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    atomic(str(path)+'.complete.json',{'run_id':'trial','phase':phase,'complete':True,'rows':len(rows),'sha256':sha(path)})


def shared_fault(directory, event='helper-crash', *, last_unknown=False, truncated=False):
    receipt = {'type':'fault','run_id':'trial','event':event,'executed':True,'target':{'container_id':'a'*64},
               'started_at_ms':200,'completed_at_ms':201,'recovery_hold':{'verified':True}}
    if event in ('config-change','same-container-restart'): receipt['phase'] = 'mutation_observed'
    else: receipt['exit_code'] = 0
    path = directory/'server-fault.jsonl'; path.write_text(json.dumps(receipt)+'\n')
    if last_unknown:
        receipt.update(executed=False,phase='mutation_unknown')
        with path.open('a') as stream: stream.write(json.dumps(receipt)+'\n')
    if truncated:
        with path.open('a') as stream: stream.write('{"type":"fault","executed":')
    return path


@pytest.mark.parametrize('event',['helper-crash','config-change','same-container-restart'])
def test_shared_fault_uses_actual_final_receipt_contract(tmp_path,event):
    c = {'run_id':'trial','_server':{'event':event}}
    path = shared_fault(tmp_path,event)
    assert target.shared_receipt(c,path)['executed'] is True
    shared_fault(tmp_path,event,last_unknown=True)
    with pytest.raises(ValueError): target.shared_receipt(c,path)
    shared_fault(tmp_path,event,truncated=True)
    with pytest.raises(ValueError,match='incomplete tail'): target.shared_receipt(c,path)


def test_missing_instance_round_and_too_few_settled_probes_stay_unknown(tmp_path):
    c = {'run_id':'trial','event':'client-stop','instance':'a','settle_seconds':0}
    state = {'phase':'awaiting_explicit_recovery','instances':['a','b','c'],'fault':{'verified':True,'completed_at_ms':200}}
    sealed(tmp_path,'baseline',dict.fromkeys(('a','b','c'),'available'))
    sealed(tmp_path,'fault',{'a':'unknown','b':'available','c':'available'},start=300,missing=('c',2))
    value = target.summarize(c,tmp_path,state)
    assert value['result'] == 'UNKNOWN' and value['coverage']['fault'] == 'UNKNOWN'
    sealed(tmp_path,'fault',{'a':'unknown','b':'available','c':'available'},start=190)
    value = target.summarize(c,tmp_path,state)
    assert value['result'] == 'UNKNOWN' and value['coverage']['fault'] == 'UNKNOWN'


def test_local_fault_connection_failure_on_unaffected_client_is_failure(tmp_path):
    c = {'run_id':'trial','event':'client-stop','instance':'a','settle_seconds':0}
    state = {'phase':'awaiting_explicit_recovery','instances':['a','b','c'],
             'fault':{'verified':True,'completed_at_ms':200}}
    sealed(tmp_path,'baseline',dict.fromkeys(('a','b','c'),'available'))
    sealed(tmp_path,'fault',{'a':'unavailable','b':'unavailable','c':'available'},start=300)
    value = target.summarize(c,tmp_path,state)
    assert value['result'] == 'FAIL'
    assert value['counts']['fault']['b']['unavailable'] == 3
    assert value['delivery_compliance'] == 'NOT_RUN'


@pytest.mark.parametrize('outcome', ['unknown', 'unavailable'])
def test_shared_connection_failure_and_unknown_remain_distinct_after_recovery(tmp_path, outcome):
    c = {'run_id':'trial','event':'shared-service-fault','settle_seconds':0,'_server':{'event':'helper-crash'}}
    path = shared_fault(tmp_path)
    state = {'phase':'completed','instances':['a','b','c'],
             'fault':{'verified':True,'completed_at_ms':200,'server_evidence_sha256':sha(path)},
             'questions':{n:{'result':'PASS'} for n in ('a','b','c')}}
    for phase, sample_outcome, start in [('baseline','available',100),('fault',outcome,300),('recovery','available',500)]:
        sealed(tmp_path,phase,dict.fromkeys(('a','b','c'),sample_outcome),start=start)
    value = target.summarize(c,tmp_path,state)
    assert value['recovery_result'] == 'PASS'
    assert value['fault_availability'] == ('OBSERVED_UNAVAILABLE' if outcome == 'unavailable' else 'UNKNOWN')
    assert value['result'] == ('PASS' if outcome == 'unavailable' else 'UNKNOWN')
    assert value['delivery_compliance'] == 'NOT_RUN'
    path.write_text(path.read_text()+'{"type":"fault"')
    value = target.summarize(c,tmp_path,state)
    assert value['fault_verified'] is False and value['coverage']['fault'] == 'UNKNOWN'


def test_local_fault_targets_only_selected_bound_container(monkeypatch):
    calls = []
    item = {'name':'a','deployment':{'container_name':'gateway-a','image_config_digest':'sha256:image'}}
    info = {'Id':'a'*64,'Image':'sha256:image','Config':{'Labels':{target.business.deploy.INSTANCE_LABEL:'a'}},'State':{'Running':True}}
    def execute(argv):
        calls.append(argv)
        if argv[:2] == ['docker','stop']: info['State']['Running'] = False; return ''
        return json.dumps([info])
    monkeypatch.setattr(target.business.deploy,'run',execute)
    monkeypatch.setattr(target.business.fleet,'check_mounts',lambda *a: None)
    result = target.stop_client(item)
    assert result['verified'] and result['instance_id'] == 'a'
    assert calls == [['docker','inspect','gateway-a'],['docker','stop','--time','2','a'*64],['docker','inspect','a'*64]]


def test_wrong_recovery_answer_survives_missing_injection_audit(monkeypatch,tmp_path):
    from types import SimpleNamespace
    item = {'name':'a','agent_id':'main','deployment':{'gateway_uid':1001,'gateway_gid':1001,'container_name':'gateway-a'}}
    monkeypatch.setattr(target.business,'environment',lambda *a: {})
    def execute(argv,**kwargs):
        if argv[1] == 'exec': return SimpleNamespace(returncode=0,stdout=json.dumps({'status':'ok','runId':'model-run','result':{'payloads':[{'text':'WRONG'}]}}))
        return SimpleNamespace(returncode=1,stdout='',stderr='')
    monkeypatch.setattr(target.subprocess,'run',execute)
    fact = {'marker':'project-marker','fact':'ARGUS_FACT_0123456789ABCDEF0123456789ABCDEF'}
    value = target.question(item,'secret',fact,'trial',tmp_path)
    assert value['result'] == 'FAIL' and value['reason'] == 'ANSWER_OR_INJECTION_EVIDENCE_INCOMPLETE'
    assert 'WRONG' not in (tmp_path/'question.json').read_text()
