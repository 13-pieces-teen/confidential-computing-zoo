import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import milestone
import runner
from common import atomic


def evidence(tmp_path):
    root = tmp_path / 'business'; root.mkdir()
    child = root / 'alice'; child.mkdir()
    identity = {'client_spiffe_id':'spiffe://argus.local/agent/alice', 'server_spiffe_id':'spiffe://argus.local/service/backend'}
    atomic(root/'result.json', {'run_id':'run', 'config_sha256':'a'*64, 'scope':'shared-node independent gateways', 'instances':{'alice':{'result':'UNKNOWN'}}})
    atomic(child/'run.json', {'run_id':'run-alice', 'session_key':'write', 'agent_id':'main'})
    atomic(child/'identity.json', identity)
    state = {'schema_version':1, 'scope':dict(identity, session_key='write', agent_id='main'),
             'session_id':'s1', 'task_id':'t1', 'plugin_readback':True, 'archive':True, 'commit_count':1,
             'extraction':{'by_category':{'profile':1},'total':1}}
    (child/'processing-events.jsonl').write_text(json.dumps(state)+'\n')
    write = dict(identity, component='argus-openclaw-spiffe', path='/api/v1/sessions/s1/messages', method='POST',
                 http_status=200, request_id='write', client_serial='1', server_serial='2', generation='generation-test')
    write = {'component':write.pop('component'), **write}
    (child/'gateway-write.log').write_text(json.dumps(write,separators=(',',':'))+'\n')
    fact = 'ARGUS_FACT_'+'A'*32; fact_hash = hashlib.sha256(fact.encode()).hexdigest()
    (child/'fact.txt').write_text(fact+'\n'); (child/'recall-session-key.txt').write_text('write-recall-new\n')
    (child/'recall-prompt.txt').write_text('What was the code for the synthetic marker?')
    recall = {'component':'argus-openclaw-recall', 'session_key':'write-recall-new', 'event':'completed',
              'input_fact_hashes':[], 'recall_fact_hashes':[fact_hash], 'output_fact_hashes':[fact_hash],
              'request_ids':['search'], 'context_span_id':'span'}
    transport = dict(write, method='POST', path='/api/v1/search/find', request_id='search', context_span_id='span')
    (child/'gateway.log').write_text('\n'.join(json.dumps(v,separators=(',',':')) for v in [write,transport,recall]))
    atomic(child/'recall-response.json', {'status':'ok','runId':'fresh','result':{'payloads':[{'text':fact}]}})
    return root, child, state


def test_real_source_milestones_and_append_only_processing_growth(tmp_path):
    root, child, state = evidence(tmp_path)
    values = [milestone.derive(root,'alice',stage,'run') for stage in milestone.STAGES]
    assert all(v['reached'] and milestone.verify(v,'run') for v in values)
    with (child/'processing-events.jsonl').open('a') as stream: stream.write(json.dumps(state)+'\n')
    assert milestone.verify(values[0],'run')
    assert 'ARGUS_FACT_' not in json.dumps(values)


def test_empty_extraction_wrong_answer_and_leaked_prompt_are_distinct(tmp_path):
    root, child, state = evidence(tmp_path)
    state['extraction'] = {'by_category':{},'total':0}
    (child/'processing-events.jsonl').write_text(json.dumps(state)+'\n')
    assert milestone.derive(root,'alice','archive','run')['reached']
    assert not milestone.derive(root,'alice','nonempty-extraction','run')['reached']
    state['extraction'] = {'by_category':{'profile':1},'total':1}
    (child/'processing-events.jsonl').write_text(json.dumps(state)+'\n')
    atomic(child/'recall-response.json', {'status':'ok','runId':'fresh','result':{'payloads':[{'text':'wrong'}]}})
    assert milestone.derive(root,'alice','injection','run')['reached']
    assert not milestone.derive(root,'alice','answer','run')['reached']
    (child/'recall-prompt.txt').write_text((child/'fact.txt').read_text())
    with pytest.raises(ValueError,match='leaked'): milestone.derive(root,'alice','injection','run')


def test_tampering_old_run_and_manual_flags_do_not_open_fault_gate(tmp_path):
    root, child, state = evidence(tmp_path)
    value = milestone.derive(root,'alice','archive','run')
    state['archive'] = False
    (child/'processing-events.jsonl').write_text(json.dumps(state)+'\n')
    with pytest.raises(ValueError,match='changed'): milestone.verify(value,'run')
    with pytest.raises(ValueError,match='binding'): milestone.derive(root,'alice','archive','another')
    trace = tmp_path/'trace.jsonl'
    trace.write_text('\n'.join(json.dumps({'type':'request','run_id':'run','lane':lane,'request_id':f'{lane}{n}',
                                         'ok':True,'tls_connections':1}) for lane in ('new','existing') for n in range(3)))
    flag = tmp_path/'milestone.json'; atomic(flag, {'run_id':'run','milestone':'archive','reached':True})
    with pytest.raises(ValueError,match='derived'): runner.fault_ready(trace,flag,'run')
