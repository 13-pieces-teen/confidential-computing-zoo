"""Local unit fixtures, not remote Agent evidence. Execute on the validation host."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import atomic, read
from continuous import (Gateway, canonical_session_key, configuration, make_fixture, prepare,
                        prompt_for, score_step, execute, result_for)
from fact_protocol import encode_fact, parse_fact, facts_in


def config():
    return {'schema':'argus.continuous.v1','condition':'no_fault','structure_seed':19,'secret_seed':'a'*64,
        'bindings':[{'client_id':'alice','container':'gateway-a','docker_user':'10001:10001',
            'config_path':'/config/openclaw.json','agent_id':'main','account_id':'eval','user_id':'alice',
            'client_spiffe_id':'spiffe://argus.local/client/a','server_spiffe_id':'spiffe://argus.local/service/memory'}]}


class ContinuousTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.path=self.root/'config.json'
        atomic(self.path,config()); self.c=configuration(self.path)
        self.fixture=make_fixture(self.c,'a'*64); self.step=self.fixture['steps'][0]

    def formal_gate(self, healthy_changes=None, fault_changes=None):
        healthy = {'schema':'argus.healthy-pilot-gate.v1','condition':'no_fault','planned_steps':6,
                   'window_completion':'PASS','service_recovery':'NOT_APPLICABLE',
                   'task_correctness':'PASS','receiver_coverage':'PASS'}
        fault = {'schema':'argus.fault-pilot-gate.v1','condition':'fault','planned_steps':6,
                 'window_completion':'PASS','service_recovery':'PASS','task_correctness':'PASS',
                 'receiver_coverage':'PASS','pre_fault_confirmed_state':'PASS',
                 'post_fault_attempt':'PASS','legal_recovery':'PASS',
                 'correct_continuation':'PASS','unresolved_proposals':[]}
        healthy.update(healthy_changes or {}); fault.update(fault_changes or {})
        healthy_path = self.root/'healthy-pilot-gate.json'; atomic(healthy_path,healthy)
        fault_path = self.root/'fault-pilot-gate.json'; atomic(fault_path,fault)
        return {'healthy_receipt_path':str(healthy_path),
                'healthy_receipt_sha256':hashlib.sha256(healthy_path.read_bytes()).hexdigest(),
                'fault_receipt_path':str(fault_path),
                'fault_receipt_sha256':hashlib.sha256(fault_path.read_bytes()).hexdigest()}

    def test_frame_is_complete_ascii_and_survives_json(self):
        frame=self.step['frame']; observed=parse_fact(frame)
        self.assertEqual(observed['fact_id'],self.step['fact_id'])
        self.assertEqual(observed['amount_cents'],self.step['amount_cents'])
        self.assertIn(frame,json.dumps({'text':frame}))
        self.assertIsNone(parse_fact(frame.replace('|amount=','|amount=9',1)))
        self.assertEqual(facts_in(frame[:-1]),[])
        self.assertEqual(len(facts_in('query '+frame)),1)

    def test_service_fault_domain_never_declares_uninjected_clients(self):
        value=config(); value['fault_kind']='helper-freeze'
        atomic(self.path,value)
        self.assertEqual(configuration(self.path)['fault_scope'],'shared_service')
        value['uninjected_client_ids']=['alice']; atomic(self.path,value)
        with self.assertRaisesRegex(ValueError,'uninjected'):
            configuration(self.path)
        value.pop('uninjected_client_ids'); value['fault_scope']='local_client'; atomic(self.path,value)
        with self.assertRaisesRegex(ValueError,'NOT_RUN'):
            configuration(self.path)

    def test_independent_secrets_preserve_dag_and_business_difficulty(self):
        other=make_fixture(self.c,'b'*64)
        self.assertEqual(len(self.fixture['steps']),18)
        for a,b in zip(self.fixture['steps'],other['steps']):
            self.assertEqual((a['phase'],a['predecessor'],a['amount_cents']),(b['phase'],b['predecessor'],b['amount_cents']))
            self.assertNotEqual(a['fact_id'],b['fact_id']); self.assertNotEqual(a['event_ref'],b['event_ref'])
        self.assertIsNone(self.fixture['steps'][12]['predecessor'])
        self.assertEqual(self.fixture['steps'][15]['predecessor'],'s12')

    def test_prompt_has_no_rule_or_predecessor_answer(self):
        successor=self.fixture['steps'][3]; prompt=prompt_for(successor)
        self.assertIn(successor['frame'],prompt)
        self.assertIn(successor['predecessor_fact_id'],prompt)
        self.assertNotIn(successor['expected']['previous_event_ref'],prompt)
        for rule in self.fixture['rules']:
            self.assertNotIn(rule['normal_code'],prompt); self.assertNotIn(rule['review_code'],prompt)

    def test_binding_order_does_not_change_client_task_difficulty(self):
        c=copy.deepcopy(self.c)
        bob=copy.deepcopy(c['bindings'][0]); bob.update(client_id='bob',container='gateway-b',user_id='bob',
                                                       client_spiffe_id='spiffe://argus.local/client/b')
        c['bindings'].append(bob)
        first=make_fixture(c,'a'*64)
        c['bindings'].reverse()
        self.assertEqual(first,make_fixture(c,'a'*64))

    def evidence(self):
        h=hashlib.sha256(self.step['frame'].encode()).hexdigest()
        facts=[{'full_fact_sha256':h}]
        audit=[{'event':'tool_started','tool_name':name,'input_facts':facts} for name in ('memory_recall','memory_store')]
        audit += [{'event':'tool_completed','tool_name':'memory_recall','details':{'count':1}},
                  {'event':'tool_completed','tool_name':'memory_store','details':{'status':'completed','memoriesCount':1}},
                  {'event':'request_attempted','request_id':'request-1'},
                  {'event':'request_body','request_id':'request-1','facts':facts}]
        return {'phase':'completed','started_at_ms':100,'answer_at_ms':200,'deadline_at_ms':500,
                'agent':{'result':'OBSERVED','answer':json.dumps(self.step['expected'])},'audit':audit,
                'inspection':{'result':'OBSERVED','session':{'commit_count':1},
                    'archives':[{'messages':[{'parts':[{'type':'text','text':self.step['frame']+'\nDecision: '+json.dumps(self.step['expected'])}]}]}]}}

    def test_goal_requires_correct_answer_actual_calls_and_archive(self):
        entry=self.evidence(); self.assertEqual(score_step(self.step,entry)['task_result'],'PASS')
        entry['inspection']['archives']=[]
        self.assertNotEqual(score_step(self.step,entry)['task_result'],'PASS')
        entry=self.evidence(); entry['agent']['answer']='{"total_cents":0}'
        self.assertEqual(score_step(self.step,entry)['reason'],'ANSWER_INCORRECT')

    def test_empty_extraction_and_missing_full_frame_cannot_pass(self):
        entry=self.evidence(); entry['audit'][3]['details']['memoriesCount']=0
        self.assertEqual(score_step(self.step,entry)['reason'],'EXTRACTION_EMPTY')
        entry=self.evidence(); entry['audit'][0]['input_facts']=[]
        self.assertEqual(score_step(self.step,entry)['reason'],'FULL_FACT_MISSING_FROM_TOOL_INPUT')

    def test_deadline_is_independent_of_receipt_unknown(self):
        entry=self.evidence(); entry['audit']=None; entry['answer_at_ms']=600
        scored=score_step(self.step,entry)
        self.assertEqual(scored['task_result'],'FAIL'); self.assertTrue(scored['deadline_missed'])
        self.assertEqual(scored['attempted'],'UNKNOWN')
        self.assertIsNone(scored['tool_call_count'])

    def test_unknown_commit_can_be_confirmed_without_claiming_old_deadline(self):
        entry=self.evidence(); entry['audit'][3]['details']={'status':'timeout'}
        entry['inspection']['task']={'status':'completed','result':{'memories_extracted':{'facts':1}}}
        entry['inspection_observed_at_ms']=600
        scored=score_step(self.step,entry)
        self.assertTrue(scored['committed']); self.assertEqual(scored['task_result'],'UNKNOWN')
        entry['inspection_observed_at_ms']=300
        self.assertEqual(score_step(self.step,entry)['task_result'],'PASS')

    def test_prepare_keeps_all_tasks_and_secret_values_out_of_manifest(self):
        output=self.root/'out'; manifest=prepare(self.path,output)
        self.assertEqual(len(manifest['facts']),18)
        text=json.dumps(manifest)
        self.assertNotIn(self.step['event_ref'],text)
        self.assertNotIn('normal_code',text)
        state=read(output/'state.json')
        result=result_for(self.c,manifest,read(output/'private-fixture.json'),state,output)
        self.assertEqual(result['planned_tasks'],18); self.assertEqual(result['counts']['NOT_RUN'],18)
        self.assertEqual(result['result'],'NOT_RUN')

    def test_resume_never_invokes_agent_seed_or_control(self):
        output=self.root/'out'; prepare(self.path,output)
        state=read(output/'state.json'); state.update(phase='running',started_at_ms=100)
        entry=self.evidence(); entry.update(submitted_at='2026-09-29T00:00:00Z',session_key='argus-e4:run:alice:s00:1')
        state['steps']['alice/s00']=entry; atomic(output/'state.json',state)
        class QueryGateway:
            def records(self,*args): return entry['audit']
            def call(self,binding,action,**payload):
                if action!='inspect': raise AssertionError('resume replayed '+action)
                return entry['inspection']
        with patch('continuous.subprocess.run',side_effect=AssertionError('control replayed')):
            result=execute(self.path,output,'resume',QueryGateway())
        self.assertFalse(result['measurement_complete']); self.assertEqual(len(result['steps']),18)
        self.assertEqual(result['counts']['NOT_RUN'],17)

    def test_formal_requires_frozen_schedule_and_fault_commands(self):
        value=config(); value['mode']='formal'; atomic(self.path,value)
        with self.assertRaisesRegex(ValueError,'frozen'): configuration(self.path)
        value['schedule']={'frozen':True}; value['condition']='fault'; value['fault_kind']='helper-freeze'
        value['formal_gate']=self.formal_gate(); atomic(self.path,value)
        with self.assertRaisesRegex(ValueError,'commands'): configuration(self.path)

    def test_failed_healthy_pilot_refuses_formal(self):
        value=config(); value.update(mode='formal',condition='fault',fault_kind='helper-freeze',
                                    schedule={'frozen':True},
                                    controls={'fault':{'argv':['true']},'recovery':{'argv':['true']}},
                                    formal_gate=self.formal_gate(healthy_changes={'task_correctness':'FAIL'}))
        atomic(self.path,value)
        with self.assertRaisesRegex(ValueError,'did not satisfy'): configuration(self.path)

    def test_failed_fault_pilot_refuses_formal(self):
        value=config(); value.update(mode='formal',condition='fault',fault_kind='helper-freeze',
                                    schedule={'frozen':True},
                                    controls={'fault':{'argv':['true']},'recovery':{'argv':['true']}},
                                    formal_gate=self.formal_gate(fault_changes={'correct_continuation':'UNKNOWN',
                                                                               'unresolved_proposals':['s02']}))
        atomic(self.path,value)
        with self.assertRaisesRegex(ValueError,'fault pilot did not satisfy'): configuration(self.path)

    def test_session_key_canonicalization_accepts_main_only(self):
        bare = 'argus-e4:run-1:alice:s00:1'
        self.assertEqual(canonical_session_key(bare),bare)
        self.assertEqual(canonical_session_key('agent:main:'+bare),bare)
        self.assertIsNone(canonical_session_key('agent:worker:'+bare))
        self.assertIsNone(canonical_session_key('agent:main:argus-e4:other:alice:s00:0'))

    def test_gateway_records_save_raw_and_canonical_and_isolate_other_agent(self):
        expected = 'argus-e4:run-1:alice:s00:1'
        rows = [
            {'component':'argus-openclaw-task','session_key':expected,'event':'tool_started'},
            {'component':'argus-openclaw-spiffe','ctx':{'sessionKey':'agent:main:'+expected},'event':'tool_completed'},
            {'component':'argus-openclaw-task','session_key':'agent:worker:'+expected,'event':'tool_started'}]
        completed = SimpleNamespace(returncode=0,stdout='\n'.join(json.dumps(v) for v in rows),stderr='')
        with patch('continuous.subprocess.run',return_value=completed):
            records = Gateway().records({'container':'gateway-a'},'2026-10-09T00:00:00Z',expected)
        self.assertEqual(len(records),2)
        self.assertEqual({row['canonical_session_key'] for row in records},{expected})
        self.assertEqual({row['raw_session_key'] for row in records},
                         {expected,'agent:main:'+expected})
        self.assertEqual({row['session_key_source'] for row in records},
                         {'session_key','ctx.sessionKey'})

    def test_gateway_preserves_partial_phase_result_and_private_stderr(self):
        gateway=Gateway(); gateway.set_diagnostic_root(self.root/'private-diagnostics')
        observed={'result':'UNKNOWN','action':'inspect','operation_phase':'context_get',
                  'code':'CONTEXT_GET_FAILED','session':{'commit_count':1},
                  'phase_observations':[{'result':'OBSERVED','operation_phase':'session_get'},
                                        {'result':'UNKNOWN','operation_phase':'context_get',
                                         'code':'CONTEXT_GET_FAILED','http_status':503,
                                         'request_id':'request-1','duration_ms':12}]}
        completed=SimpleNamespace(returncode=1,stdout=json.dumps(observed)+'\n',
                                  stderr='sensitive upstream details')
        binding={'container':'gateway-a','docker_user':'10001:10001','config_path':'/config/openclaw.json',
                 'agent_id':'main','account_id':'eval','user_id':'alice',
                 'client_spiffe_id':'spiffe://argus.local/client/a',
                 'server_spiffe_id':'spiffe://argus.local/service/memory'}
        with patch('continuous.subprocess.run',return_value=completed):
            result=gateway.call(binding,'inspect',ov_session_id='session')
        self.assertEqual(result['session']['commit_count'],1)
        self.assertEqual(result['phase_observations'][1]['http_status'],503)
        self.assertEqual(result['subprocess_exit_code'],1)
        diagnostic=self.root/result['private_stderr']['reference']
        self.assertEqual(diagnostic.read_text(),'sensitive upstream details')
        self.assertEqual(diagnostic.stat().st_mode & 0o077,0)

    def test_protocol_hash_pools_seeds_but_structure_hash_pairs_exact_difficulty(self):
        one=prepare(self.path,self.root/'one')
        other=config(); other['structure_seed']=20; other['secret_seed']='b'*64
        atomic(self.path,other)
        two=prepare(self.path,self.root/'two')
        self.assertEqual(one['protocol_hash'],two['protocol_hash'])
        self.assertNotEqual(one['structure_hash'],two['structure_hash'])

    def test_work_item_retry_uses_events_policy_and_fresh_seed_session(self):
        value=config(); value['scenario']='work-item-v1'; value['initialization_generation']=0
        atomic(self.path,value); first=make_fixture(configuration(self.path),'a'*64)
        value['initialization_generation']=1
        atomic(self.path,value); second=make_fixture(configuration(self.path),'a'*64)
        self.assertNotEqual(first['rules'][0]['ov_session_id'],second['rules'][0]['ov_session_id'])
        self.assertEqual(second['rules'][0]['memory_policy']['memory_types'],['events'])
        self.assertTrue(second['rules'][0]['memory_policy']['working_memory']['enabled'])
        self.assertIn(second['rules'][0]['project_id'],second['rules'][0]['initialization_query'])
        self.assertIn(second['rules'][0]['work_item_id'],second['rules'][0]['initialization_query'])

    def test_releases_continue_when_agent_fails_and_keep_all_deadlines(self):
        value=config(); value['schedule']={'release_interval_s':0.003,'deadline_s':0.025,'stop_budget_s':0.001}
        value['qa_timeout_seconds']=1; atomic(self.path,value)
        fixture=make_fixture(configuration(self.path),'a'*64)
        class FailedAgent:
            def call(self,binding,action,**payload):
                if action=='preflight': return {'result':'OBSERVED'}
                if action=='seed': return {'result':'OBSERVED','commit':{'status':'completed','memories_extracted':{'facts':1},'archive_uri':'x/archive_001'}}
                if action=='find': return {'result':'OBSERVED','memories':fixture['rules']}
                if action=='inspect': return {'result':'OBSERVED','session':{'commit_count':1},
                    'archives':[{'messages':[{'parts':[{'text':r['text']}]} for r in fixture['rules']]}]}
                if action=='agent':
                    time.sleep(0.008)
                    raise OSError('synthetic unavailable Gateway')
                raise AssertionError(action)
            def records(self,*args): raise OSError('synthetic audit read failure')
        result=execute(self.path,self.root/'run','run',FailedAgent())
        self.assertEqual(result['offered_tasks'],18)
        self.assertEqual(len(result['steps']),18)
        self.assertTrue(result['measurement_complete'])
        self.assertEqual(result['controls']['fault']['planned_at_ms'],result['started_at_ms']+18)
        self.assertEqual(result['controls']['recovery']['planned_at_ms'],result['started_at_ms']+36)
        self.assertEqual(sum(result['counts'].values()),18)
        self.assertGreater(result['counts']['UNKNOWN'],0)

    def test_resume_initial_seed_queries_known_task_and_requires_original_archive(self):
        output=self.root/'init'; prepare(self.path,output)
        fixture=read(output/'private-fixture.json'); rule=fixture['rules'][0]
        state=read(output/'state.json'); state['phase']='initializing'
        key=rule['client_id']+'/'+rule['project_id']
        state['initialization'][key]={'status':'submission_unknown','observation':{'commit':{
            'status':'accepted','task_id':'known-task','archive_uri':'x/archive_001'}}}
        atomic(output/'state.json',state)
        class QueryOnly:
            archive=False
            def call(self,binding,action,**payload):
                if action=='inspect':
                    if payload['extraction_task_id']!='known-task': raise AssertionError('lost task ID')
                    return {'result':'OBSERVED','session':{'commit_count':1},
                        'task':{'status':'completed','result':{'memories_extracted':{'facts':1}}},
                        'archives':[{'messages':[{'parts':[{'text':rule['text']}]}]}] if self.archive else []}
                if action=='find': return {'result':'OBSERVED','memories':[rule]}
                raise AssertionError('unexpected mutation')
        gateway=QueryOnly()
        first=execute(self.path,output,'resume',gateway)
        self.assertEqual(first['result'],'NOT_RUN')
        self.assertFalse(read(output/'state.json')['initialization'][key]['confirmed'])
        gateway.archive=True
        second=execute(self.path,output,'resume',gateway)
        saved=read(output/'state.json')
        self.assertTrue(saved['initialization'][key]['confirmed'])
        self.assertEqual(len(saved['initialization']),1)
        self.assertNotIn('started_at_ms',saved)
        self.assertEqual(second['offered_tasks'],0)


if __name__=='__main__': unittest.main()
