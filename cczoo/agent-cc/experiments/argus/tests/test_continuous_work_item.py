"""Local orchestration fixtures, never real Agent/TDX/receiver evidence."""
import hashlib
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import atomic, read
from continuous import configuration, execute, make_fixture, now_ms, prepare, prompt_for
from continuous_work_item import decision
from continuous_proposal import SCHEMA, UNITS, encode, proposal_hash, record_updates, proposals_in
from fact_protocol import encode_fact
from test_continuous import config


def configured(tmp_path):
    value = config()
    value.update(scenario='work-item-v1', fault_kind='helper-freeze',
                 schedule={'release_interval_s':.2, 'deadline_s':1, 'stop_budget_s':.01},
                 qa_timeout_seconds=1)
    path = tmp_path / 'config.json'
    atomic(path, value)
    return path, configuration(path)


class MemoryGateway:
    """In-memory service with explicit original-task state and passive audit."""
    def __init__(self, fixture, pause='confirmed', resolve_pending=False, missing_recall=False,
                 wrong_decision=False, later_query_failure=False, lose_release_receipt=False,
                 missing_proposal_recall=False, strip_proposal=False):
        self.fixture = fixture
        self.pause, self.resolve_pending, self.missing_recall = pause, resolve_pending, missing_recall
        self.wrong_decision, self.later_query_failure, self.lose_release_receipt = wrong_decision, later_query_failure, lose_release_receipt
        self.missing_proposal_recall, self.strip_proposal = missing_proposal_recall, strip_proposal
        self.agent_calls, self.inspections = [], []
        self.preflights = 0
        self.writes, self.audits, self.effective = {}, {}, {}
        self.seed_records = {}
        self.forbid_writes = False
        self.pending_answer_observed = False

    def call(self, binding, action, **payload):
        if action == 'preflight':
            self.preflights += 1
            return {'result':'OBSERVED'}
        if action == 'seed':
            assert not self.forbid_writes
            self.seed_records[payload['ov_session_id']] = payload['text']
            return {'result':'OBSERVED', 'commit':{'status':'completed', 'memories_extracted':{'facts':1}}}
        if action == 'find':
            return {'result':'OBSERVED', 'memories':self.fixture['rules']}
        if action == 'inspect':
            self.inspections.append((payload['ov_session_id'], payload.get('extraction_task_id')))
            rule = next((r for r in self.fixture['rules'] if r['ov_session_id'] == payload['ov_session_id']), None)
            if rule:
                return {'result':'OBSERVED', 'session':{'commit_count':1},
                        'task':{'status':'completed','result':{'memories_extracted':{'facts':1}}},
                        'archives':[{'messages':[{'parts':[{'text':rule['text']}]}]}]}
            sid = next(s['step_id'] for s in self.fixture['steps'] if s['ov_session_id'] == payload['ov_session_id'])
            if self.later_query_failure and self.preflights > 1 and sid in ('s00','s01'):
                return {'result':'UNKNOWN','code':'TEMPORARY_QUERY_FAILURE'}
            write = self.writes.get(sid)
            if write and write['status'] == 'pending' and self.resolve_pending and self.preflights > 1:
                assert payload.get('extraction_task_id') == 'original-' + sid
                write['status'] = 'confirmed'
            if write and write['status'] == 'rejected':
                return {'result':'OBSERVED','session':{'commit_count':0},'task':{'status':'failed'},'archives':[]}
            if not write:
                return {'result':'OBSERVED','session':{'commit_count':0},'archives':[]}
            if write['status'] == 'pending':
                return {'result':'OBSERVED','session':{'commit_count':1},'task':{'status':'pending'},'archives':[]}
            return {'result':'OBSERVED','session':{'commit_count':1},
                    'task':{'status':'completed','result':{'memories_extracted':{'facts':1}}},
                    'archives':[{'messages':[{'parts':[{'text':write['stored_text']}]}]}]}
        assert action == 'agent' and not self.forbid_writes
        sid = payload['task_id']
        assert sid not in self.agent_calls, 'unknown POST / Agent call replayed'
        self.agent_calls.append(sid)
        # The simulated Agent reconstructs ONLY the submitted input and stored
        # raw records. Private fixture types/order/values never feed its answer.
        step, = record_updates(payload['prompt'])
        step['frame'] = encode_fact(step)
        step['proposal_text'] = encode(step['proposal'])
        step['ov_session_id'] = payload['prompt'].split('sessionId="',1)[1].split('"',1)[0]
        policy = next(json.loads(text.split('行程规则：',1)[1]) for text in self.seed_records.values()
                      if step['work_item_id'] in text)
        confirmed = sorted([update for write in self.writes.values() if write['status'] == 'confirmed'
                            for update in record_updates(write['stored_text'])], key=lambda s:s['index'])
        answer = decision(policy, [*confirmed, step])
        if self.wrong_decision and sid == 's02':
            answer['constraints']['budget_cents'] += 1
        self.effective[sid] = step
        status = 'pending' if sid == 's02' and self.pause == 'unknown' else 'rejected' if sid in ('s02','s03') and self.pause == 'rejected' else 'confirmed'
        self.writes[sid] = {'status':status,'answer':answer,
                           'stored_text':step['frame']+'\n'+step['proposal_text']+'\nDecision: '+json.dumps(answer)}
        if self.strip_proposal and sid == 's00':
            self.writes[sid]['stored_text'] = step['frame']+'\nDecision: '+json.dumps(answer)
        facts = [{'full_fact_sha256':hashlib.sha256(step['frame'].encode()).hexdigest()}]
        prior = [{'full_fact_sha256':hashlib.sha256(s['frame'].encode()).hexdigest()} for s in confirmed]
        proposal = [{'proposal_sha256':proposal_hash(step['proposal'])}]
        prior_proposals = [{'proposal_sha256':proposal_hash(s['proposal'])} for s in confirmed]
        if self.missing_proposal_recall and sid == 's05': prior_proposals = []
        if self.missing_recall and sid == 's05': prior = []
        audit = [{'event':'tool_started','tool_name':'memory_recall','input_facts':facts},
                      {'event':'tool_completed','tool_name':'memory_recall','details':{'count':1},'output_facts':prior,
                       'output_proposals':prior_proposals}]
        if status == 'rejected':
            audit += [{'event':'tool_started','tool_name':'memory_store','input_facts':facts,'input_proposals':proposal},
                      {'event':'tool_completed','tool_name':'memory_store','details':{'status':'failed','memoriesCount':0}}]
        else:
            audit += [{'event':'tool_started','tool_name':'memory_store','input_facts':facts,'input_proposals':proposal},
                      {'event':'tool_completed','tool_name':'memory_store','details':{'status':'completed' if status == 'confirmed' else 'timeout','memoriesCount':1}},
                      {'event':'commit_receipt','ov_session_id':step['ov_session_id'],
                       'extraction_task_id':'original-'+sid,'archive_id':'original-archive-'+sid},
                      {'event':'request_attempted','request_id':'request-'+sid},
                      {'event':'request_body','request_id':'request-'+sid,'facts':facts}]
        self.audits[payload['session_key']] = audit
        result = {'result':'UNKNOWN' if status == 'pending' and not self.pending_answer_observed else 'OBSERVED',
                'answer':json.dumps(answer) if status == 'confirmed' or self.pending_answer_observed else 'UNKNOWN',
                'input_release':{'source':'gateway_agent_dispatch','boundary':'openclaw_cli_input',
                    'at_ms':now_ms(),'task_id':sid,'fact_id':step['fact_id'],
                    'work_item_id':step['work_item_id'],'session_key':payload['session_key'],
                    'prompt_sha256':hashlib.sha256(payload['prompt'].encode()).hexdigest()}}
        if self.lose_release_receipt: result.pop('input_release')
        return result

    def records(self, binding, since, session_key):
        return self.audits.get(session_key, [])


def test_six_steps_one_work_item_plan_and_no_expected_answers_in_prompt(tmp_path):
    path, c = configured(tmp_path)
    fixture = make_fixture(c, 'a'*64)
    assert len(fixture['steps']) == 6 and len(fixture['rules']) == 1
    assert len({s['work_item_id'] for s in fixture['steps']}) == 1
    assert len({s['chain_id'] for s in fixture['steps']}) == 1
    assert c['controls']['fault']['at_s'] == .4 and c['controls']['recovery']['at_s'] == .8
    prompt = prompt_for(fixture['steps'][4])
    assert fixture['steps'][1]['fact_id'] in prompt
    assert fixture['steps'][2]['fact_id'] in prompt  # no dropped pause proposal
    assert fixture['steps'][1]['event_ref'] not in prompt
    assert fixture['rules'][0]['normal_code'] not in prompt
    manifest = prepare(path, tmp_path/'prepared')
    assert len(manifest['facts']) == 6
    assert {f['task_id'] for f in manifest['facts']} == {f's{i:02d}' for i in range(6)}
    assert [f['fact_index'] for f in manifest['facts']] == list(range(6))
    assert manifest['scenario'] == 'work-item-v1'


def test_all_constraints_are_checked_not_only_the_previous_fact():
    work_item = 'trip-'+'a'*24
    policy = {'work_item_id':work_item,'initial_constraints':{'budget_cents':200,'max_walk_minutes':60,'max_travel_minutes':90,'min_indoor_stops':0},
              'unavailable_code':'none','routes':[
                  {'route_code':'cheap','cost_cents':80,'walk_minutes':50,'travel_minutes':80,'indoor_stops':0,'stops':['公园']},
                  {'route_code':'indoor','cost_cents':120,'walk_minutes':20,'travel_minutes':60,'indoor_stops':2,'stops':['博物馆','美术馆']}]}
    def update(i, key, value):
        return {'work_item_id':work_item,'fact_id':f'{i:032x}','event_ref':str(i),'constraint_key':key,'amount_cents':value,
                'proposal':{'schema':SCHEMA,'work_item_id':work_item,'fact_id':f'{i:032x}',
                            'step_index':i,'constraint_key':key,'unit':UNITS[key],'value':value}}
    updates = [update(0,'budget_cents',150),update(1,'max_walk_minutes',30),update(2,'min_indoor_stops',2)]
    answer = decision(policy, updates)
    assert answer['route_code'] == 'indoor' and answer['itinerary'] == ['博物馆','美术馆']
    answer = decision(policy, [*updates,update(3,'max_travel_minutes',50)])
    assert answer['status'] == 'NO_FEASIBLE_ITINERARY' and answer['itinerary'] == []
    assert answer['constraints'] == {'budget_cents':150,'max_walk_minutes':30,'max_travel_minutes':50,'min_indoor_stops':2}


def test_confirmed_pause_update_is_recalled_and_included_after_recovery(tmp_path):
    path, c = configured(tmp_path)
    fixture = make_fixture(c, 'a'*64)
    gateway = MemoryGateway(fixture)
    output = tmp_path/'run'
    result = execute(path, output, gateway=gateway)
    assert result['planned_tasks'] == 6 and result['counts']['PASS'] == 6
    assert result['work_items'][0]['continuation_result'] == 'PASS'
    assert result['work_items'][0]['complete_task_result'] == 'PASS'
    assert result['work_items'][0]['applied_confirmed_proposals'] == [f's{i:02d}' for i in range(6)]
    state = read(output/'state.json')
    assert state['steps']['alice/s04']['effective_step']['expected']['constraints']['budget_cents'] == fixture['steps'][2]['amount_cents']
    assert state['steps']['alice/s04']['effective_step']['expected']['constraints']['max_walk_minutes'] == fixture['steps'][3]['amount_cents']
    assert all(s['released_at_ms'] is not None for s in result['steps'])
    assert len({state['steps']['alice/'+sid]['session_key'] for sid in gateway.agent_calls}) == 6
    assert result['work_items'][0]['receipt_result'] == 'UNKNOWN'
    assert all(s['goal_confirmed_at_ms'] >= s['answer_at_ms'] for s in result['steps'])


def test_unknown_post_resolves_only_by_original_task_query_and_is_not_dropped(tmp_path):
    path, c = configured(tmp_path)
    fixture = make_fixture(c, 'a'*64)
    gateway = MemoryGateway(fixture, pause='unknown', resolve_pending=True)
    result = execute(path, tmp_path/'run', gateway=gateway)
    assert gateway.agent_calls.count('s02') == 1
    assert any(task == 'original-s02' for session, task in gateway.inspections)
    assert result['work_items'][0]['continuation_result'] == 'PASS'
    assert result['work_items'][0]['complete_task_result'] == 'UNKNOWN'
    assert result['work_items'][0]['proposals'][2]['status'] == 'CONFIRMED'
    assert 's02' in result['steps'][4]['confirmed_predecessors']


def test_unresolved_post_prevents_complete_work_item_and_resume_never_replays(tmp_path):
    path, c = configured(tmp_path)
    fixture = make_fixture(c, 'a'*64)
    gateway = MemoryGateway(fixture, pause='unknown')
    output = tmp_path/'run'
    result = execute(path, output, gateway=gateway)
    assert result['work_items'][0]['result'] == 'UNKNOWN'
    assert result['work_items'][0]['unresolved_proposals'] == ['s02']
    assert gateway.agent_calls == ['s00','s01','s02']
    assert result['steps'][3]['reason'] == 'PRIOR_WRITE_UNRESOLVED'
    assert result['steps'][4]['reason'] == 'PRIOR_WRITE_UNRESOLVED'
    assert result['steps'][4]['released_at_ms'] is None
    gateway.forbid_writes = True
    reconciled = execute(path, output, action='resume', gateway=gateway)
    assert gateway.agent_calls == ['s00','s01','s02']
    assert reconciled['work_items'][0]['result'] == 'UNKNOWN'


def test_correct_model_answer_without_actual_predecessor_recall_cannot_pass(tmp_path):
    path, c = configured(tmp_path)
    fixture = make_fixture(c, 'a'*64)
    result = execute(path, tmp_path/'run', gateway=MemoryGateway(fixture, missing_recall=True))
    assert result['steps'][5]['reason'] == 'CONFIRMED_PREDECESSOR_NOT_RECALLED'
    assert result['work_items'][0]['result'] == 'FAIL'


def test_confirmed_rejection_is_reported_not_silently_lost(tmp_path):
    path, c = configured(tmp_path)
    fixture = make_fixture(c, 'a'*64)
    result = execute(path, tmp_path/'run', gateway=MemoryGateway(fixture, pause='rejected'))
    item = result['work_items'][0]
    assert [p['status'] for p in item['proposals']] == ['CONFIRMED','CONFIRMED','REJECTED','REJECTED','CONFIRMED','CONFIRMED']
    assert item['continuation_result'] == 'PASS' and item['complete_task_result'] == 'FAIL'
    assert item['result'] == 'FAIL' and item['applied_confirmed_proposals'] == ['s00','s01','s04','s05']
    assert item['unapplied_required_proposals'] == ['s02','s03']
    assert result['counts']['PASS'] == 4  # planned task denominator still includes rejections


def test_wrong_decision_does_not_erase_a_persisted_user_constraint(tmp_path):
    path, c = configured(tmp_path)
    fixture = make_fixture(c, 'a'*64)
    output = tmp_path/'run'
    result = execute(path, output, gateway=MemoryGateway(fixture, wrong_decision=True))
    assert result['steps'][2]['task_result'] == 'FAIL'
    assert result['steps'][2]['proposal_persisted'] is True
    assert result['work_items'][0]['proposals'][2]['status'] == 'CONFIRMED'
    assert result['work_items'][0]['complete_task_result'] == 'FAIL'
    assert result['work_items'][0]['continuation_result'] == 'PASS'
    effective = read(output/'state.json')['steps']['alice/s04']['effective_step']
    assert effective['expected']['constraints']['budget_cents'] == fixture['steps'][2]['amount_cents']
    assert 's02' in effective['confirmed_predecessors']


def test_later_query_failure_does_not_downgrade_confirmed_commit(tmp_path):
    path, c = configured(tmp_path)
    fixture = make_fixture(c, 'a'*64)
    output = tmp_path/'run'
    result = execute(path, output, gateway=MemoryGateway(fixture, later_query_failure=True))
    assert result['work_items'][0]['complete_task_result'] == 'PASS'
    reconciliation = read(output/'state.json')['steps']['alice/s01']['reconciliation']
    assert reconciliation['committed'] is True and reconciliation['latest_query_committed'] == 'UNKNOWN'
    assert result['steps'][1]['committed'] is True


def test_queue_offer_and_dispatch_intent_do_not_invent_actual_release(tmp_path):
    path, c = configured(tmp_path)
    fixture = make_fixture(c, 'a'*64)
    result = execute(path, tmp_path/'run', gateway=MemoryGateway(fixture, lose_release_receipt=True))
    assert all(s['offered'] for s in result['steps'])
    assert any(s['dispatch_attempted'] for s in result['steps'])
    assert all(s['released_at_ms'] is None for s in result['steps'])


def test_late_first_commit_confirmation_does_not_reuse_early_pending_query_time(tmp_path):
    path, c = configured(tmp_path)
    raw = read(path); raw['schedule'].update(release_interval_s=.4, deadline_s=.6); atomic(path, raw)
    c = configuration(path)
    fixture = make_fixture(c, 'a'*64)
    gateway = MemoryGateway(fixture, pause='unknown', resolve_pending=True)
    gateway.pending_answer_observed = True
    output = tmp_path/'run'
    result = execute(path, output, gateway=gateway)
    row = result['steps'][2]
    assert row['proposal_persisted'] is True and row['task_result'] != 'PASS'
    entry = read(output/'state.json')['steps']['alice/s02']
    assert entry['inspection_observed_at_ms'] > entry['deadline_at_ms']


@pytest.mark.parametrize('field', ['unit','constraint_key','step_index','work_item_id'])
def test_missing_typed_field_cannot_be_recovered_from_frame_or_decision(tmp_path, field):
    _, c = configured(tmp_path)
    step = make_fixture(c,'a'*64)['steps'][0]
    proposal = dict(step['proposal']); proposal.pop(field)
    text = step['frame']+'\nARGUS_PROPOSAL_V1\n'+json.dumps(proposal)+'\nEND_ARGUS_PROPOSAL\nDecision: '+json.dumps(step['expected'])
    assert proposals_in(text) == [] and record_updates(text) == []


def test_units_and_frame_value_and_duplicate_fields_are_checked(tmp_path):
    _, c = configured(tmp_path)
    step = make_fixture(c,'a'*64)['steps'][0]
    invalid = dict(step['proposal'],unit='minute')
    with pytest.raises(ValueError): encode(invalid)
    mismatch = dict(step['proposal'],value=step['amount_cents']+1)
    assert record_updates(step['frame']+'\n'+encode(mismatch)) == []
    duplicate = step['proposal_text'].replace('{','{"step_index":5,',1)
    assert proposals_in(duplicate) == []


def test_frame_only_archive_never_confirms_a_typed_proposal(tmp_path):
    path, c = configured(tmp_path)
    result = execute(path,tmp_path/'run',gateway=MemoryGateway(make_fixture(c,'a'*64),strip_proposal=True))
    assert result['steps'][0]['proposal_persisted'] is False
    assert result['steps'][0]['write_outcome'] == 'INVALID_PERSISTED_INPUT'
    assert result['work_items'][0]['complete_task_result'] != 'PASS'


def test_frame_recall_without_typed_proposal_cannot_pass(tmp_path):
    path, c = configured(tmp_path)
    result = execute(path,tmp_path/'run',gateway=MemoryGateway(make_fixture(c,'a'*64),missing_proposal_recall=True))
    assert result['steps'][5]['reason'] == 'TYPED_PROPOSAL_NOT_RECALLED'
    assert result['work_items'][0]['complete_task_result'] == 'FAIL'


def test_raw_memory_drives_recomputation_even_if_fixture_metadata_is_wrong(tmp_path):
    path, c = configured(tmp_path)
    fixture = make_fixture(c,'a'*64)
    gateway = MemoryGateway(fixture,wrong_decision=True)
    for item in fixture['steps']:
        item['constraint_key'] = 'not_a_real_constraint'
        item['proposal'] = {'invalid':'private fixture cannot supply recovered types'}
        item['amount_cents'] = -1
        item['expected'] = {'invalid':'private fixture cannot supply a recovered answer'}
    result = execute(path,tmp_path/'run',gateway=gateway)
    assert result['steps'][2]['task_result'] == 'FAIL'
    assert result['steps'][2]['proposal_persisted'] is True
    assert result['work_items'][0]['continuation_result'] == 'PASS'
    assert result['work_items'][0]['complete_task_result'] == 'FAIL'


def test_partial_audit_does_not_prove_no_store_call_or_service_rejection(tmp_path):
    from continuous import score_step
    _, c = configured(tmp_path)
    step = make_fixture(c,'a'*64)['steps'][0]
    scored = score_step(step,{'audit':[{'event':'tool_started','tool_name':'memory_recall'}],
                             'agent':{'result':'OBSERVED','answer':json.dumps(step['expected'])},
                             'dispatch_started_at_ms':0,'answer_at_ms':1,'deadline_at_ms':2})
    assert scored['task_result'] == 'UNKNOWN' and scored['reason'] == 'TASK_AUDIT_INCOMPLETE'
    assert scored['committed'] == scored['write_outcome'] == scored['write_attempted'] == 'UNKNOWN'


@pytest.mark.parametrize('agent_result', ['UNKNOWN','OBSERVED'])
@pytest.mark.parametrize('audit', [None, []])
def test_missing_audit_after_dispatch_never_proves_unattempted_write(tmp_path, agent_result, audit):
    from continuous import score_step
    _, c = configured(tmp_path)
    step = make_fixture(c,'a'*64)['steps'][0]
    scored = score_step(step,{'audit':audit,
        'agent':{'result':agent_result,'code':'GATEWAY_IO_FAILED','answer':json.dumps(step['expected'])},
        'phase':'completed','dispatch_started_at_ms':100,'answer_at_ms':200,'deadline_at_ms':500,
        'inspection':{'result':'UNKNOWN'}})
    assert scored['task_result'] == 'UNKNOWN'
    assert scored['committed'] == scored['write_outcome'] == scored['write_attempted'] == 'UNKNOWN'
    assert scored['attempted'] == scored['full_fact_sent'] == 'UNKNOWN'


def test_controller_pre_dispatch_stop_is_distinct_from_missing_agent_audit(tmp_path):
    from continuous import score_step
    _, c = configured(tmp_path)
    step = make_fixture(c,'a'*64)['steps'][0]
    entry = {'audit':[],'phase':'completed','dispatch_outcome':'NOT_DISPATCHED',
             'terminal_reason':'PRIOR_WRITE_UNRESOLVED','terminal_result':'UNKNOWN'}
    scored = score_step(step,entry)
    assert scored['committed'] is False and scored['write_attempted'] is False
    assert scored['attempted'] is False and scored['full_fact_sent'] is False
    assert scored['write_outcome'] == 'NOT_DISPATCHED'
    # A stale pre-dispatch marker must not hide a later submission intent.
    entry['dispatch_started_at_ms'] = 100
    assert score_step(step,entry)['committed'] == 'UNKNOWN'


def test_lost_gateway_and_empty_audit_gate_all_successors_without_replay(tmp_path):
    path, c = configured(tmp_path)
    fixture = make_fixture(c,'a'*64)

    class LostEvidenceGateway(MemoryGateway):
        def call(self, binding, action, **payload):
            if action == 'agent' and payload['task_id'] == 's02':
                # Simulate a submitted write whose terminal response and audit
                # were both lost; lack of these records is not a rejection.
                self.agent_calls.append('s02')
                return {'result':'UNKNOWN','code':'GATEWAY_IO_FAILED'}
            if action == 'inspect' and payload['ov_session_id'] == fixture['steps'][2]['ov_session_id']:
                return {'result':'UNKNOWN','code':'INSPECTION_IO_FAILED'}
            return super().call(binding,action,**payload)

    gateway = LostEvidenceGateway(fixture)
    output = tmp_path/'run'
    result = execute(path,output,gateway=gateway)
    assert gateway.agent_calls == ['s00','s01','s02']
    assert result['steps'][2]['committed'] == result['steps'][2]['write_outcome'] == 'UNKNOWN'
    assert all(not s['dispatch_attempted'] for s in result['steps'][3:])
    assert result['work_items'][0]['complete_task_result'] == 'UNKNOWN'
    assert result['work_items'][0]['unresolved_proposals'] == ['s02']
    assert [p['status'] for p in result['work_items'][0]['proposals'][3:]] == ['NOT_DISPATCHED'] * 3
    gateway.forbid_writes = True
    resumed = execute(path,output,'resume',gateway)
    assert gateway.agent_calls == ['s00','s01','s02']
    assert resumed['work_items'][0]['complete_task_result'] == 'UNKNOWN'
