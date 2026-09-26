import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lifecycle_trial as trial
import load
from common import atomic, sha
from test_load_tls import tls, args


def observed(tmp_path, case='agent-renewal'):
    directory = tmp_path/'server'; directory.mkdir()
    value = {'schema':'argus.lifecycle-trial.v1','run_id':'run','case':case,'target_id':'spiffe://argus.local/service/test',
             'complete':True,'started_at_ms':1000,'completed_at_ms':2000,'before':{},'after':{}}
    base = {'version':1,'agent_id':'spiffe://argus.local/agent/node','metrics_url':'http://127.0.0.1:9988/metrics',
            'metric_names':{'attempts':'node_attempts','quote_samples':'node_quotes','process_start':'agent_start'},
            'attempts':1,'quote_samples':1,'process_start':.1,
            'agent':{'present':True,'serial':'1','expires_at':10000,'attestation_type':'argus_tdx','can_reattest':False}}
    for phase, start, serial in [('before',1000,'1'),('after',1900,'2')]:
        node = copy.deepcopy(base); node.update(started_at_ms=start, completed_at_ms=start+100, agent_observed_at_ms=start+50)
        node['agent']['serial'] = serial
        path = directory/('node-'+phase+'.json'); atomic(path,node)
        value[phase]['node'] = {'result':'OBSERVED','source':path.name,'sha256':sha(path)}
    atomic(directory/'observation.json', value)
    return directory, value


def probes(tmp_path, observation, *, failed=False, gap=False, workload_kind='memory_query'):
    directory = tmp_path/'client'; directory.mkdir()
    stamps = [800,900,1000,1100,1200,1300,1400,1500,1600,1700,1800,1900,2000,2100,2200]
    if gap: stamps = [800,900,1000,1100,1900,2000,2100,2200]
    rows = [{'run_id':'run','request_id':'q-'+str(index),'phase':'measurement','started_at_ms':stamp,'completed_at_ms':stamp+10,
             'outcome':'success','memory_result':'nonempty','peer_identity_verified':True,
             'peer_spiffe_id':observation['target_id'],'http_status':200} for index,stamp in enumerate(stamps)]
    if failed:
        rows[3].update(outcome='timeout',memory_result=None)
    trace = directory/'requests.jsonl'; trace.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    path = directory/'load-result.json'
    atomic(path,{'run_id':'run','workload_kind':workload_kind,'measurement_complete':True,'requests_sha256':sha(trace),
                 'measurement_started_at_ms':800,'measurement_ended_at_ms':2200,'server_id':observation['target_id']})
    return path


def test_collect_real_node_deltas_separately_from_workload_and_business(tmp_path):
    server, observation = observed(tmp_path)
    load_result = probes(tmp_path, observation)
    result = trial.collect(server,load_result,clock_uncertainty_ms=0,max_probe_gap_ms=200)
    assert result['result'] == result['business_continuity']['result'] == 'PASS'
    assert result['node_quote_samples'] == {'result':'OBSERVED','count':0}
    assert result['workload_quote_samples']['result'] == 'UNKNOWN'
    assert result['workload_quote_samples']['count'] is None
    assert result['node']['observed_serial_transitions_min'] == 1
    assert result['node']['can_reattest_observed'] is False
    assert result['business_continuity']['coverage'] == 'COMPLETE'
    assert result['automatic_mutations'] is False


@pytest.mark.parametrize('change', ['missing','restart','increment'])
def test_node_metrics_missing_restart_and_actual_increment_remain_distinct(tmp_path, change):
    server, observation = observed(tmp_path)
    path = server/'node-after.json'; node = json.loads(path.read_text())
    if change == 'missing': node['quote_samples'] = None
    if change == 'restart': node['process_start'] = .9
    if change == 'increment': node['quote_samples'] = 3
    atomic(path,node); observation['after']['node']['sha256'] = sha(path); atomic(server/'observation.json',observation)
    result = trial.collect(server)
    assert result['result'] == ('FAIL' if change == 'increment' else 'UNKNOWN')
    assert result['node_quote_samples']['count'] == (2 if change == 'increment' else None)
    assert result['business_continuity']['result'] == 'NOT_RUN'


def test_probe_gaps_do_not_establish_continuity_and_observed_failure_survives(tmp_path):
    _, observation = observed(tmp_path)
    path = probes(tmp_path,observation,gap=True,failed=True)
    result = trial.business_continuity(path,observation,0,200)
    assert result['coverage'] == 'UNKNOWN' and result['result'] == 'FAIL'
    episode = result['clients'][0]['interruption_episodes'][0]
    assert episode['first_failed_request_ms'] == 1100
    assert episode['sampled_recovery_ms'] == 800


def test_empty_time_coverage_status_api_and_modified_trace_are_unknown(tmp_path):
    _, observation = observed(tmp_path)
    path = probes(tmp_path,observation,gap=True)
    assert trial.business_continuity(path,observation,0,200)['result'] == 'UNKNOWN'
    receipt = json.loads(path.read_text()); receipt['workload_kind'] = 'status_api'; atomic(path,receipt)
    assert trial.business_continuity(path,observation,0,1000)['result'] == 'UNKNOWN'
    receipt['workload_kind'] = 'memory_query'; atomic(path,receipt)
    with (path.parent/'requests.jsonl').open('a') as stream: stream.write('{}\n')
    assert trial.business_continuity(path,observation,0,1000)['result'] == 'UNKNOWN'


def test_incomplete_observation_never_replays_an_operation(tmp_path, monkeypatch):
    server, observation = observed(tmp_path)
    observation['complete'] = False; atomic(server/'observation.json',observation)
    monkeypatch.setattr(trial.subprocess,'run',lambda *a,**k: (_ for _ in ()).throw(AssertionError('must not invoke a command')))
    result = trial.collect(server)
    assert result['result'] == 'UNKNOWN' and result['automatic_mutations'] is False


def test_failed_snapshot_is_unknown_not_unrun(tmp_path):
    server, observation = observed(tmp_path)
    observation['before']['node']['result'] = 'UNKNOWN'
    atomic(server/'observation.json',observation)
    result = trial.collect(server)
    assert result['result'] == result['node_quote_samples']['result'] == 'UNKNOWN'


def test_probe_scope_mismatch_cannot_claim_business_continuity(tmp_path):
    _, observation = observed(tmp_path)
    path = probes(tmp_path,observation)
    observation['target_id'] = 'spiffe://argus.local/service/other'
    assert trial.business_continuity(path,observation,0,200)['result'] == 'UNKNOWN'


def test_capture_invokes_only_existing_read_only_snapshot_commands(tmp_path, monkeypatch):
    commands = []
    node = {key:'test-'+key for key in trial.NODE_FIELDS}; node['metrics_url'] = 'http://127.0.0.1:9988/metrics'
    def execute(argv, **kwargs):
        commands.append(argv)
        atomic(argv[-1], {'observed':'actual-tool-contract'})
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(trial.subprocess,'run',execute)
    captured = trial.capture_phase({'node':node,'workload_config':'/etc/argus/workload.json'},tmp_path,'before')
    assert len(commands) == 2 and all(argv[2] == 'snapshot' for argv in commands)
    assert all(record['result'] == 'OBSERVED' for record in captured.values())
    assert all('resume-launch' not in argv and 'renew' not in argv for argv in commands)


def test_real_mtls_memory_trace_can_be_collected_without_self_reported_continuity(tls):
    query = tls.root/'query.json'; query.write_text('{"query":"remember"}')
    options = args(tls,tls.root/'real-probe',workload_kind='memory_query',connection_mode='reuse',
                   concurrency=1,rate=20,measure=.7)
    options.url = tls.url+'/api/v1/search/find'; options.body_file = str(query); options.run_id = 'run'
    sampled = load.run(options)
    path = tls.root/'real-probe/load-result.json'
    rows = [json.loads(line) for line in (path.parent/'requests.jsonl').read_text().splitlines()]
    observation = {'run_id':'run','target_id':options.server_id,'started_at_ms':rows[2]['started_at_ms'],
                   'completed_at_ms':rows[-3]['started_at_ms']}
    result = trial.business_continuity(path,observation,0,200)
    assert sampled['result'] == 'PASS' and len(tls.seen) == len(rows)
    assert result['result'] == 'PASS' and result['coverage'] == 'COMPLETE'
    assert result['clients'][0]['nonempty_successes'] >= 3
    assert len({r['peer_port'] for r in tls.seen}) == 1
