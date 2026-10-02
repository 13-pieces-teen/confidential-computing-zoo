"""Cost recipes preserve failures and operate on explicit original evidence."""
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cost_trials as cost
from common import atomic, digest, read, sha


def test_unknown_control_is_not_replayed(tmp_path):
    calls = []
    def failed(argv, **kwargs):
        calls.append(argv)
        raise subprocess.TimeoutExpired(argv, 1)
    directory = tmp_path/'control'
    result = cost.command_once(['approved-control'], directory, 1, failed)
    assert result['state'] == 'SUBMISSION_UNKNOWN' and len(calls) == 1
    with pytest.raises(ValueError, match='intent'):
        cost.command_once(['approved-control'], directory, 1, failed)
    assert len(calls) == 1


def history_fixture(tmp_path):
    target_file = tmp_path/'target.json'; atomic(target_file, {'container_id': 'a'*64, 'launch_id': 'launch'})
    growth = tmp_path/'growth.json'; atomic(growth, [['approved-unrelated-lifecycle']])
    points = [dict(id=name, expected_records=count, **({'growth_argv_file': str(growth)} if i else {}))
              for i, (name, count) in enumerate(zip(('small','medium','large'), (2,4,6)))]
    policy=tmp_path/'approved.rego'; policy.write_text('approved policy bytes')
    etc=tmp_path/'etc'; etc.mkdir(); (etc/'fixed_cpu.rego').write_bytes(policy.read_bytes())
    deployed = {'approved':{'policy_id':'fixed'}, 'trucon_socket_path':'/root/socket',
                'approved_policy_artifact':{'path':str(policy),'sha256':sha(policy)}}
    deployment=tmp_path/'protected.json'; atomic(deployment,deployed)
    config = {'schema':'argus.cost-trial.v1', 'group':'full_argus', 'run_id':'cost-test',
              'deployment':str(deployment), 'mode':'formal', 'frozen':True, 'history_points':points}
    source = tmp_path/'config.json'; atomic(source, config)
    runtime = SimpleNamespace(Deployment=lambda c: SimpleNamespace(target=target_file,etc=etc), protected_file=Path,
                              policy_bytes=lambda c:Path(c['approved_policy_artifact']['path']).read_bytes())
    return source, config, lambda c: (runtime, deployed)


def test_three_points_use_actual_counts_and_keep_capacity_failure(tmp_path):
    source, _, runtime = history_fixture(tmp_path)
    counts = iter([2,2,4,4,None])
    def snapshot(path):
        count = next(counts)
        if count is None:
            return {'result':'UNAVAILABLE', 'http_status':409, 'detail':'capacity'}
        return {'result':'OBSERVED', 'record_count':count, 'reference_bytes':count*4,
                'snapshot': {'log_ids':[str(i) for i in range(count)], 'sequence_num':count}}
    calls = []
    def invoke(argv, **kwargs):
        calls.append(argv)
        if 'attempt' in argv:
            dest = Path(argv[argv.index('--output')+1])
            atomic(dest/'attempt.json', {'run_id':'cost-test', 'group':'full_argus',
                                        'deployment_sha256':digest(read(read(source)['deployment'])),
                                        'target':{'container_id':'a'*64,'launch_id':'launch'}, 'result':'ADMITTED', 'complete':True})
        return SimpleNamespace(returncode=0)
    result = cost.history_series(source, tmp_path/'out', runtime_loader=runtime, snapshot=snapshot, invoke=invoke)
    assert [r['result'] for r in result['points']] == ['OBSERVED','OBSERVED','UNAVAILABLE']
    assert [r.get('actual_record_count') for r in result['points']] == [2,4,None]
    assert sum('attempt' in a for a in calls) == 2
    assert result['points'][2]['chain_before']['detail'] == 'capacity'
    with pytest.raises(ValueError, match='never automatically replay'):
        cost.history_series(source, tmp_path/'out', runtime_loader=runtime, snapshot=snapshot, invoke=invoke)


def test_growth_timeout_retains_later_not_run(tmp_path):
    source, _, runtime = history_fixture(tmp_path)
    def snapshot(path):
        return {'result':'OBSERVED','record_count':2,'snapshot':{'log_ids':['0','1']}}
    def invoke(argv, **kwargs):
        if argv == ['approved-unrelated-lifecycle']:
            raise subprocess.TimeoutExpired(argv, 1)
        atomic(Path(argv[argv.index('--output')+1])/'attempt.json', {'run_id':'cost-test','group':'full_argus',
              'deployment_sha256':digest(read(read(source)['deployment'])),
              'target':{'container_id':'a'*64,'launch_id':'launch'},'complete':True,'result':'ADMITTED'})
        return SimpleNamespace(returncode=0)
    result = cost.history_series(source, tmp_path/'out', runtime_loader=runtime, snapshot=snapshot, invoke=invoke)
    assert [r['result'] for r in result['points']] == ['OBSERVED','UNKNOWN','NOT_RUN']


def test_formal_lengths_frozen_and_distinct(tmp_path):
    source, config, _ = history_fixture(tmp_path)
    config['history_points'][2]['expected_records'] = 4
    atomic(source, config)
    with pytest.raises(ValueError, match='strictly increase'):
        cost.configuration(source)


def test_unknown_admission_does_not_start_later_growth_or_subscription(tmp_path):
    source, _, runtime = history_fixture(tmp_path)
    calls = []
    def snapshot(path):
        return {'result':'OBSERVED','record_count':2,'snapshot':{'log_ids':['0','1']}}
    def invoke(argv, **kwargs):
        calls.append(argv)
        atomic(Path(argv[argv.index('--output')+1])/'attempt.json', {
            'run_id':'cost-test','group':'full_argus',
            'deployment_sha256':digest(read(read(source)['deployment'])),
            'target':{'container_id':'a'*64,'launch_id':'launch'},
            'complete':False,'result':'UNKNOWN','command_error_class':'TimeoutExpired'})
        return SimpleNamespace(returncode=2)
    result = cost.history_series(source, tmp_path/'out', runtime_loader=runtime, snapshot=snapshot, invoke=invoke)
    assert [r['result'] for r in result['points']] == ['UNKNOWN','NOT_RUN','NOT_RUN']
    assert len(calls) == 1 and 'attempt' in calls[0]
    assert 'unresolved' in result['points'][0]['reason']


def test_timing_requires_capture_hash_and_nonce(tmp_path):
    directory = tmp_path/'nonce'
    atomic(directory/'history-timing.json', {'schema':'argus.history-timing.v1','fetch_ms':1.5})
    atomic(directory/'capture.json', {'nonce':'nonce','artifacts':{'history-timing.json':sha(directory/'history-timing.json')}})
    assert cost.captured_timing(tmp_path, 'nonce')['result'] == 'OBSERVED'
    atomic(directory/'history-timing.json', {'schema':'argus.history-timing.v1','fetch_ms':0})
    assert cost.captured_timing(tmp_path, 'nonce')['result'] == 'UNKNOWN'


def shared_fixture(tmp_path):
    paths = []
    for index, phase in enumerate(('before','pending','confirmed')):
        start = index*1000+100
        value = {'schema':'argus.shared-cost-snapshot.v1', 'run_id':'shared-1', 'group':'full_argus',
                 'phase':phase,'configuration_sha256':'same','target_id':'spiffe://test/b',
                 'complete':True,
                 'target':{'container_id':'b'},'target_after':{'container_id':'b'},'workload_a':'a','workload_b':'b',
                 'started_at_ms':start,'completed_at_ms':start+500,
                 'status':{'helper_invocation_id':'same-helper'},'status_after':{'helper_invocation_id':'same-helper'},
                 'chain':{'http_status':409 if phase == 'pending' else 200,'result':'UNAVAILABLE' if phase == 'pending' else 'OBSERVED'},
                 'probe':{'schema':'argus.subscription-probe.v1','run_id':'shared-1','result':'UNKNOWN'}}
        if phase == 'pending':
            for name, offset in (('mutation_before',10),('mutation_after',490)):
                value[name] = {'pending_observed':True,'mutation_matches_target':True,'run_id':'shared-1',
                               'workload_id':'a','chain_id':'default','mutation_id':'mutation-a','observed_at_ms':start+offset}
        path = tmp_path/(phase+'.json'); atomic(path,value); paths.append(path)
    requests = [{'run_id':'shared-1','started_at_ms':s,'completed_at_ms':s+10,'connection_id':cid,
                 'connection_reused':reused,'outcome':'success','memory_result':'nonempty'}
                for s,cid,reused in ((300,'old',False),(1200,'old',True),(1300,'new',False))]
    directory = tmp_path/'load'; directory.mkdir()
    (directory/'requests.jsonl').write_text('\n'.join(json.dumps(r) for r in requests))
    atomic(directory/'load-result.json',{'run_id':'shared-1','server_id':'spiffe://test/b',
                                       'measurement_complete':True, 'requests_sha256':sha(directory/'requests.jsonl')})
    return paths,directory


def test_shared_counts_only_connections_present_before_pending(tmp_path):
    paths, directory = shared_fixture(tmp_path)
    result = cost.shared_collect(paths, directory, tmp_path/'result.json', 1)
    assert result['existing_traffic']['result'] == 'OBSERVED'
    assert result['existing_traffic']['requests_in_observed_window'] == 2
    assert result['existing_traffic']['preexisting_connection_requests'] == 1
    assert result['new_subscription']['pending']['result'] == 'UNKNOWN'
    assert result['policy_invalid_receiver'] == 'UNKNOWN'


def test_restart_existing_helper_invalidates_continuity_claim(tmp_path):
    paths, directory = shared_fixture(tmp_path)
    middle = read(paths[1]); middle['status_after']['helper_invocation_id']='restarted'; atomic(paths[1],middle)
    result = cost.shared_collect(paths, directory, tmp_path/'result.json', 1)
    assert result['existing_traffic']['result'] == 'UNKNOWN'


def test_409_without_correlated_a_mutation_does_not_prove_shared_blocking(tmp_path):
    paths, directory = shared_fixture(tmp_path)
    middle = read(paths[1]); middle.pop('mutation_before'); atomic(paths[1],middle)
    result = cost.shared_collect(paths, directory, tmp_path/'result.json', 1)
    assert result['pending_mutation_association'] == 'UNKNOWN'
    assert result['existing_traffic']['result'] == 'UNKNOWN'


def test_shared_rejects_tampered_requests(tmp_path):
    paths, directory = shared_fixture(tmp_path)
    with (directory/'requests.jsonl').open('a') as stream:
        stream.write('\n{}')
    with pytest.raises(ValueError, match='source changed'):
        cost.shared_collect(paths, directory, tmp_path/'result.json', 1)


def test_provider_timing_binds_the_accepted_nonce_not_the_last_success():
    def row(nonce,time):
        return {'component':'provider','stage':'provider','status':'ALLOW','nonce':nonce,'attempt_id':nonce,
                'timings':{'total_ms':time}}
    selected=row('accepted',11)
    attempt={'accepted_nonce':'accepted','provider_timings':[selected,row('later-unrelated',99)]}
    result=cost.accepted_provider_timing(attempt)
    assert result['result']=='OBSERVED' and result['timings']['total_ms']==11
    attempt['provider_timings'].append(row('accepted',12))
    assert cost.accepted_provider_timing(attempt)['result']=='UNKNOWN'
    assert cost.accepted_provider_timing({'provider_timings':[selected]})['result']=='UNKNOWN'
    attempt={'accepted_nonce':'accepted','provider_timings':[dict(selected,attempt_id='different')]}
    assert cost.accepted_provider_timing(attempt)['result']=='UNKNOWN'


def repeat_snapshot():
    counts=iter([2,2,4,4,6,6])
    def snapshot(path):
        n=next(counts)
        return {'result':'OBSERVED','record_count':n,'snapshot':{'log_ids':[str(i) for i in range(n)]}}
    return snapshot


@pytest.mark.parametrize('change',['deployment','approved_artifact','installed_policy'])
def test_policy_or_deployment_file_change_during_admission_retains_unknown_and_stops(tmp_path,change):
    source,config,runtime=history_fixture(tmp_path)
    calls=[]
    def invoke(argv,**kwargs):
        calls.append(argv)
        deployment=read(config['deployment'])
        atomic(Path(argv[argv.index('--output')+1])/'attempt.json',{'run_id':'cost-test','group':'full_argus',
          'deployment_sha256':digest(deployment),'target':{'container_id':'a'*64,'launch_id':'launch'},
          'result':'ADMITTED','complete':True})
        if change=='deployment':
            deployment['approved']['changed']='after-start'; atomic(config['deployment'],deployment)
        elif change=='approved_artifact': Path(deployment['approved_policy_artifact']['path']).write_text('changed policy')
        else: (tmp_path/'etc/fixed_cpu.rego').write_text('changed installed policy')
        return SimpleNamespace(returncode=0)
    result=cost.history_series(source,tmp_path/'out',runtime_loader=runtime,snapshot=repeat_snapshot(),invoke=invoke)
    assert [p['result'] for p in result['points']]==['UNKNOWN','NOT_RUN','NOT_RUN']
    assert result['points'][0]['deployment_policy_stable'] is False
    assert len(calls)==1  # no later growth and no retry after changed bindings


def test_wrong_attempt_deployment_hash_cannot_be_a_history_point_success(tmp_path):
    source,config,runtime=history_fixture(tmp_path)
    def invoke(argv,**kwargs):
        if 'attempt' in argv:
            atomic(Path(argv[argv.index('--output')+1])/'attempt.json',{'run_id':'cost-test','group':'full_argus',
              'deployment_sha256':'not-the-frozen-deployment','target':{'container_id':'a'*64,'launch_id':'launch'},
              'result':'ADMITTED','complete':True})
        return SimpleNamespace(returncode=0)
    result=cost.history_series(source,tmp_path/'out',runtime_loader=runtime,snapshot=repeat_snapshot(),invoke=invoke)
    assert [p['result'] for p in result['points']] == ['UNKNOWN','NOT_RUN','NOT_RUN']
    assert result['points'][0]['error_class'] == 'ValueError'
    assert all('error_class' not in p for p in result['points'][1:])


def test_deployment_change_between_points_is_checked_before_any_more_growth(tmp_path,monkeypatch):
    source,config,runtime=history_fixture(tmp_path)
    original=cost.deployment_binding
    snapshots=0
    def binding(*args):
        nonlocal snapshots
        snapshots+=1
        if snapshots==5:  # initial + three small-point checks; now medium-before-growth
            deployment=read(config['deployment']); deployment['approved']['changed']='between-points'
            atomic(config['deployment'],deployment)
        return original(*args)
    monkeypatch.setattr(cost,'deployment_binding',binding)
    calls=[]
    def invoke(argv,**kwargs):
        calls.append(argv)
        atomic(Path(argv[argv.index('--output')+1])/'attempt.json',{'run_id':'cost-test','group':'full_argus',
          'deployment_sha256':digest(read(config['deployment'])),'target':{'container_id':'a'*64,'launch_id':'launch'},
          'result':'ADMITTED','complete':True})
        return SimpleNamespace(returncode=0)
    result=cost.history_series(source,tmp_path/'out',runtime_loader=runtime,snapshot=repeat_snapshot(),invoke=invoke)
    assert [p['result'] for p in result['points']]==['OBSERVED','UNKNOWN','NOT_RUN']
    assert len(calls)==1 and result['points'][1]['growth']==[]


@pytest.mark.parametrize('change',['late_connection','unknown_before','before_pending_sample','boundary'])
def test_pending_existing_connection_requires_a_verified_normal_before_window(tmp_path,change):
    paths,directory=shared_fixture(tmp_path)
    requests=[json.loads(line) for line in (directory/'requests.jsonl').read_text().splitlines()]
    if change=='late_connection':
        requests[0].update(started_at_ms=1101,completed_at_ms=1105)
    elif change=='boundary':
        requests[0].update(started_at_ms=590,completed_at_ms=600)
    else:
        before=read(paths[0])
        if change=='unknown_before': before['chain']['result']='UNKNOWN'
        else: before['chain_samples']=[before['chain'],{'result':'UNAVAILABLE','http_status':409}]
        atomic(paths[0],before)
    (directory/'requests.jsonl').write_text('\n'.join(json.dumps(r) for r in requests))
    load=read(directory/'load-result.json'); load['requests_sha256']=sha(directory/'requests.jsonl'); atomic(directory/'load-result.json',load)
    result=cost.shared_collect(paths,directory,tmp_path/'result.json',1)
    assert result['existing_traffic']['result']=='UNKNOWN'
    assert result['existing_traffic']['preexisting_connection_requests']==0
