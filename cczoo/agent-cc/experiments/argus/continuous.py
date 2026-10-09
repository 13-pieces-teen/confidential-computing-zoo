#!/usr/bin/env python3
"""Fixed E4 synthetic tasks through real OpenClaw tools; no write replay.

The private fixture and raw Agent replies remain in mode-0600 state files. Public
events contain only identifiers, hashes and outcomes. Resume reconciles an old
window; it never shifts releases or starts replacement Agent tasks.
"""
import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import random
import re
import secrets
import subprocess
import threading
import time
import uuid

from common import append, atomic, digest, lock, read, require, resolve, sha
from fact_protocol import encode_fact, parse_fact
from locomo_run import Gateway as BaseGateway
from continuous_work_item import SCENARIOS, make_fixture as work_item_fixture, resolve_step as resolve_work_item, prompt_for as work_item_prompt
from continuous_proposal import SCHEMA as PROPOSAL_SCHEMA, proposal_hash, record_updates

ROOT = Path(__file__).resolve().parent
BINDING_FIELDS = {'client_id', 'container', 'docker_user', 'config_path', 'agent_id',
                  'account_id', 'user_id', 'client_spiffe_id', 'server_spiffe_id'}
PHASES = ('normal', 'pause', 'recovery')
# A committed seed's Phase-2 extraction can outlive the plugin's client-side wait
# poll (the poll bails with status 'timeout' on one transient read failure), so a
# reconcile may observe the archive before it is servable. Re-query the same seed
# session (never add or commit messages) until the task is terminal, within budget.
INIT_RECONCILE_BUDGET_S = 600
INIT_RECONCILE_INTERVAL_S = 15
# The plugin's commit wait-poll runs up to 300 s; give the seed's docker exec
# subprocess room for it plus overhead (the Gateway adds 90 s to this value).
SEED_COMMIT_TIMEOUT_S = 330


def now_ms():
    return time.time_ns() // 1000000


def secure_write(path, value):
    atomic(path, value)
    if os.name == 'posix':
        os.chmod(path, 0o600)


def configuration(path):
    path = Path(path).resolve()
    c = read(path)
    require(c.get('schema') == 'argus.continuous.v1', 'invalid continuous schema')
    c.setdefault('scenario', 'ledger-v1')
    require(c['scenario'] in SCENARIOS, 'invalid continuous scenario')
    phase_steps = SCENARIOS[c['scenario']]
    require(c.get('condition', 'no_fault') in ('fault', 'no_fault'), 'invalid condition')
    c.setdefault('condition', 'no_fault')
    bindings = c.get('bindings', [])
    require(bindings and all(set(b) == BINDING_FIELDS for b in bindings), 'invalid Gateway bindings')
    for field in ('client_id', 'container', 'client_spiffe_id'):
        values = [b[field] for b in bindings]
        require(len(values) == len(set(values)), 'duplicate ' + field)
    require(len({(b['account_id'], b['user_id']) for b in bindings}) == len(bindings), 'each client needs a private business user')
    for b in bindings:
        require(all(isinstance(v, str) and v and '\x00' not in v for v in b.values()), 'invalid binding value')
        require(b['user_id'].lower() not in ('root', 'admin'), 'ordinary user required')
        require(re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', b['client_id']), 'client ID must fit tool audit session scope')
    schedule = c.setdefault('schedule', {})
    for k, v in {'release_interval_s': 60, 'deadline_s': 120, 'stop_budget_s': 10, 'frozen': False}.items():
        schedule.setdefault(k, v)
    require(all(type(schedule[k]) in (int, float) and schedule[k] > 0 for k in
                ('release_interval_s', 'deadline_s', 'stop_budget_s')), 'positive timing required')
    require(phase_steps * schedule['release_interval_s'] >= 3 * schedule['stop_budget_s'], 'pause must cover at least three stop budgets')
    require(c.get('max_concurrency', 1) == 1 and c.get('queue_limit', 1) == 1 and c.get('task_retries', 0) == 0,
            'v1 uses one active task and one queued task per client, without task retries')
    require(c.get('mode', 'pilot') in ('pilot', 'formal'), 'invalid mode')
    require(c.get('mode', 'pilot') != 'formal' or schedule['frozen'] is True, 'formal schedule must be frozen after pilots')
    require(c.get('fault_kind') is None or isinstance(c['fault_kind'],str) and bool(c['fault_kind'].strip()), 'fault_kind must be a nonempty string')
    require(c.get('mode','pilot') != 'formal' or bool(c.get('fault_kind')), 'formal runs require fault_kind, including the matched no-fault condition')
    service_fault = c.get('fault_kind') in ('helper-freeze', 'helper-crash', 'target-exit')
    c.setdefault('fault_scope', 'shared_service' if service_fault else 'unspecified')
    require(c['fault_scope'] == ('shared_service' if service_fault else 'unspecified'),
            'continuous v1 supports shared service faults; local Gateway continuous trials remain NOT_RUN')
    require(not c.get('injected_client_id') and not c.get('uninjected_client_ids'),
            'shared service faults do not have uninjected clients')
    controls = c.setdefault('controls', {})
    for name, multiplier in (('fault', phase_steps), ('recovery', 2 * phase_steps)):
        item = controls.setdefault(name, {})
        item.setdefault('at_s', multiplier * schedule['release_interval_s'])
        item.setdefault('argv', [])
        item.setdefault('timeout_s', 120)
        require(item['at_s'] == multiplier * schedule['release_interval_s'], 'controls must match the fixed three-stage schedule')
        require(isinstance(item['argv'], list) and all(isinstance(v, str) for v in item['argv']), 'control argv must be an array')
        require(type(item['timeout_s']) in (int, float) and item['timeout_s'] > 0, 'invalid control timeout')
        if c['condition'] == 'no_fault':
            require(not item['argv'], 'no-fault controls must not execute commands')
        elif c.get('mode', 'pilot') == 'formal':
            require(item['argv'], 'formal fault runs need explicit fault and recovery commands')
    c.setdefault('structure_seed', 0)
    c.setdefault('initialization_generation', 0)
    require(type(c['initialization_generation']) is int and c['initialization_generation'] >= 0,
            'initialization_generation must be a nonnegative integer')
    c.setdefault('model_settings', {})
    require(isinstance(c['model_settings'],dict) and set(c['model_settings']) <= {'model'},
            'model_settings supports the configured model only; sampling parameters are not verified')
    require(not c['model_settings'] or isinstance(c['model_settings'].get('model'),str) and bool(c['model_settings']['model']), 'model must be a nonempty name')
    require(c.get('mode','pilot') != 'formal' or bool(c['model_settings'].get('model')), 'formal runs require a configured model name')
    c.setdefault('qa_timeout_seconds', schedule['deadline_s'])
    return c


def make_fixture(c, secret_seed):
    if c.get('scenario') == 'work-item-v1':
        return work_item_fixture(c, secret_seed)
    key = str(secret_seed).encode()
    token = lambda name, length: hmac.new(key, name.encode(), hashlib.sha256).hexdigest()[:length]
    rules, steps = [], []
    rng = random.Random(c['structure_seed'])
    for b in sorted(c['bindings'],key=lambda item:item['client_id']):
        client = b['client_id']
        client_rules = []
        for project in range(3):
            rule = {'client_id': client, 'project_id': token(f'{client}/p{project}', 12),
                    'threshold_cents': rng.randint(12000, 20000),
                    'normal_code': token(f'{client}/p{project}/normal', 12),
                    'review_code': token(f'{client}/p{project}/review', 12)}
            rule['text'] = ('Private project {project_id}: add the current amount to the previous confirmed total '
                            '(zero for a new chain). If total_cents <= {threshold_cents}, route_code={normal_code}; '
                            'otherwise route_code={review_code}. Retain exact transaction references and totals.').format(**rule)
            rule['ov_session_id'] = str(uuid.UUID(token(f'{client}/p{project}/seed', 32)))
            rules.append(rule); client_rules.append(rule)
        previous = {}
        for index in range(18):
            phase, local = divmod(index, 6)
            project = local % 3
            rule = client_rules[project]
            step_id = f's{index:02d}'
            fields = {'fact_id': token(f'{client}/{step_id}/id', 32), 'project_id': rule['project_id'],
                      'chain_id': token(f'{client}/{phase}/{project}/chain', 12),
                      'event_ref': token(f'{client}/{step_id}/ref', 32), 'amount_cents': rng.randint(4000, 12000)}
            predecessor = previous.get((phase, project)) if local >= 3 else None
            total = fields['amount_cents'] + (predecessor['expected']['total_cents'] if predecessor else 0)
            expected = {'fact_id': fields['fact_id'], 'event_ref': fields['event_ref'],
                        'previous_event_ref': predecessor['expected']['event_ref'] if predecessor else None,
                        'total_cents': total, 'route_code': rule['normal_code'] if total <= rule['threshold_cents'] else rule['review_code']}
            step = {'client_id': client, 'step_id': step_id, 'phase': PHASES[phase], 'index': index,
                    'release_offset_s': index * c['schedule']['release_interval_s'],
                    'predecessor': predecessor['step_id'] if predecessor else None,
                    'predecessor_fact_id': predecessor['fact_id'] if predecessor else None,
                    'ov_session_id': str(uuid.UUID(token(f'{client}/{step_id}/store', 32))),
                    **fields, 'frame': encode_fact(fields), 'expected': expected}
            previous[(phase, project)] = step
            steps.append(step)
    return {'rules': rules, 'steps': steps}


def prepare(config_path, output):
    c = configuration(config_path); output = Path(output)
    require(not (output / 'state.json').exists(), 'already prepared')
    run_id = os.environ.get('ARGUS_RUN_ID') or c.get('run_id') or ('e4-' + uuid.uuid4().hex)
    require(re.fullmatch(r'[a-z][a-z0-9-]{0,63}', run_id), 'run ID must match the receiver deployment run name')
    operation_id = os.environ.get('ARGUS_OPERATION_ID') or uuid.uuid4().hex
    fixture = make_fixture(c, c.get('secret_seed') or secrets.token_hex(32))
    secure_write(output / 'private-fixture.json', fixture)
    structure = {'scenario': c['scenario'], 'seed': c['structure_seed'], 'clients': len(c['bindings']),
                 'dag': [{'index': s['index'], 'phase': s['phase'], 'predecessor': s['predecessor'],
                          'amount_cents': s['amount_cents']} for s in fixture['steps']],
                 'thresholds': [r['threshold_cents'] for r in fixture['rules']]}
    protocol = {'scenario': c['scenario'], 'schedule': c['schedule'], 'max_concurrency': 1, 'queue_limit': 1, 'task_retries': 0,
                'tool_mode': 'explicit_memory_recall_store', 'model_settings': c['model_settings'],
                'qa_timeout_seconds': c['qa_timeout_seconds'], 'clients': len(c['bindings']),
                'dag': [{'index': s['index'], 'phase': s['phase'], 'predecessor': s['predecessor']} for s in fixture['steps']],
                'amount_range_cents': [4000,12000], 'threshold_range_cents': [12000,20000]}
    if c['scenario'] == 'work-item-v1':
        protocol.update(steps_per_client=6, dependency_policy='all_confirmed_prior_proposals; all_successors_block_unresolved_writes',
                        proposal_schema=PROPOSAL_SCHEMA,
                        continuity_scope='persistent_memory_across_fresh_agent_sessions',
                        constraint_keys=['budget_cents','max_walk_minutes','max_travel_minutes','min_indoor_stops'])
        protocol.pop('amount_range_cents'); protocol.pop('threshold_range_cents')
    facts = [{**{k: s[k] for k in ('client_id','step_id','phase','fact_id','release_offset_s','predecessor')},
              **{k: s[k] for k in ('work_item_id', 'constraint_key', 'proposal_sha256') if k in s},
              'task_id':s['step_id'], 'fact_index':s['index'],
              'full_fact_sha256': hashlib.sha256(s['frame'].encode()).hexdigest(),
              'fact_sha256': hashlib.sha256(s['frame'].encode()).hexdigest(), 'fact_bytes': len(s['frame'])} for s in fixture['steps']]
    manifest = {'schema': 'argus.continuous-manifest.v1', 'run_id': run_id, 'operation_id': operation_id,
                'block_id': os.environ.get('ARGUS_BLOCK_ID') or c.get('block_id'),
                'group': os.environ.get('ARGUS_GROUP') or c.get('group'), 'condition': c['condition'], 'scenario':c['scenario'],
                'fault_kind':c.get('fault_kind'), 'fault_scope':c['fault_scope'],
                'configuration_sha256': digest(c), 'fixture_sha256': sha(output / 'private-fixture.json'),
                'structure_hash': digest(structure), 'protocol_hash': digest(protocol), 'protocol': protocol, 'facts': facts}
    atomic(output / 'manifest.json', manifest)
    atomic(output / 'operation-id.json', {'run_id': run_id, 'operation_id': operation_id})
    secure_write(output / 'state.json', {'schema': 'argus.continuous-state.v1', 'run_id': run_id,
        'operation_id': operation_id, 'configuration_sha256': digest(c), 'phase': 'prepared',
        'initialization': {}, 'preflight': {}, 'controls': {}, 'steps': {}})
    return manifest


class Gateway(BaseGateway):
    def __init__(self):
        self.script = (ROOT / 'continuous_gateway.mjs').read_text(encoding='utf-8')

    def records(self, binding, since, session_key):
        # Docker log retrieval supplies positive observations only. Even a
        # successful read has no end-of-scope/coverage proof, so an absent
        # tool event cannot prove that the dispatched Agent did not call it.
        try:
            p = subprocess.run(['docker','logs','--since',since,binding['container']], capture_output=True,
                               text=True, encoding='utf-8', timeout=30)
            if p.returncode != 0:
                return None
            records = []
            for line in (p.stdout + '\n' + p.stderr).splitlines():
                try:
                    value = json.loads(line[line.index('{'):])
                except (ValueError, TypeError):
                    continue
                if value.get('session_key') == session_key and value.get('component') in ('argus-openclaw-task','argus-openclaw-spiffe'):
                    records.append(value)
            return records
        except (OSError, subprocess.SubprocessError):
            return None


class Observations:
    """An I/O observation failure affects one task, never the release schedule."""
    def __init__(self, gateway):
        self.gateway = gateway

    def call(self, binding, action, **payload):
        try:
            value = self.gateway.call(binding,action,**payload)
            return value if isinstance(value,dict) else {'result':'UNKNOWN','code':'INVALID_GATEWAY_OBSERVATION'}
        except Exception:
            return {'result':'UNKNOWN','code':'GATEWAY_ADAPTER_EXCEPTION'}

    def records(self, *args):
        try:
            value = self.gateway.records(*args)
            return value if isinstance(value,list) and all(isinstance(r,dict) for r in value) else None
        except Exception:
            return None


def prompt_for(step):
    if step.get('work_item_id'):
        return work_item_prompt(step)
    prior = ('This starts a new chain; previous_event_ref=null and previous total=0.' if not step['predecessor'] else
             'First retrieve the previously committed decision for fact_id=' + step['predecessor_fact_id'] +
             '. You must recover its event_ref and total_cents from external memory; do not invent missing values.')
    return ('Process this private transaction using the project rule in OpenViking memory. Use memory_recall, with the '
            'ENTIRE transaction frame below unchanged in the query, to select the project rule. ' + prior + '\n' + step['frame'] +
            '\nCalculate total_cents and route_code according to the stored rule. Use memory_store exactly once with '
            'sessionId="' + step['ov_session_id'] + '" and text consisting of the unchanged transaction frame, then a newline, '
            'then Decision: followed by your JSON decision. The decision fields are fact_id, event_ref, previous_event_ref, '
            'total_cents (integer), route_code. Return only that same JSON after the write. If required memory or the write '
            'is unavailable, report UNKNOWN; do not retry memory_store or replace a failed write. Do not use local files or prior sessions.')


def parse_answer(answer):
    text = answer.strip()
    if text.startswith('```') and text.endswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except (ValueError, TypeError):
        return None


def archive_contains(inspection, frame, expected):
    for archive in inspection.get('archives', []):
        for message in archive.get('messages', []):
            for part in message.get('parts', []):
                text = part.get('text', '') if isinstance(part, dict) else ''
                if frame in text and 'Decision:' in text:
                    if parse_answer(text.split('Decision:', 1)[1]) == expected:
                        return True
    return False


def archive_has_fact(inspection, frame):
    return any(frame in part.get('text', '') for archive in inspection.get('archives', [])
               for message in archive.get('messages', []) for part in message.get('parts', []) if isinstance(part, dict))


def archive_has_proposal(inspection, step):
    """A frame alone does not prove the typed business input was persisted."""
    expected = step.get('proposal_sha256')
    return bool(expected) and any(proposal_hash(update['proposal']) == expected
        for archive in inspection.get('archives', []) for message in archive.get('messages', [])
        for part in message.get('parts', []) if isinstance(part, dict)
        for update in record_updates(part.get('text', '')))


def _score_step(step, entry):
    records = entry.get('audit')
    tools = [r for r in (records or []) if r.get('event') == 'tool_started']
    completed = [r for r in (records or []) if r.get('event') == 'tool_completed']
    recalls = [r for r in completed if r.get('tool_name') == 'memory_recall']
    stores = [r for r in completed if r.get('tool_name') == 'memory_store']
    inspection = entry.get('inspection', {})
    stored = any(r.get('details', {}).get('status') == 'completed' and r.get('details', {}).get('memoriesCount', 0) > 0 for r in stores)
    task = inspection.get('task') or {}
    counts = task.get('result', {}).get('memories_extracted')
    extracted = isinstance(counts, dict) and counts and all(type(v) is int and v >= 0 for v in counts.values()) and sum(counts.values()) > 0
    submission_confirmed = bool((stored or (task.get('status') == 'completed' and extracted))
                     and inspection.get('result') == 'OBSERVED' and inspection.get('session', {}).get('commit_count', 0) > 0
                     and archive_has_fact(inspection, step['frame'])
                     and (not step.get('work_item_id') or archive_has_proposal(inspection, step)))
    decision_committed = submission_confirmed and archive_contains(inspection, step['frame'], step['expected'])
    # A wrong Decision cannot erase a real persisted user constraint. Legacy
    # ledger scoring keeps its former decision-commit semantics.
    committed = submission_confirmed if step.get('work_item_id') else decision_committed
    recalled = any(r.get('details', {}).get('count', 0) > 0 for r in recalls)
    output_hashes = {f.get('full_fact_sha256') for r in recalls for f in r.get('output_facts', [])}
    required_hashes = set(step.get('required_recall_fact_hashes', []))
    predecessors_recalled = required_hashes <= output_hashes
    proposal_outputs = {p.get('proposal_sha256') for r in recalls for p in r.get('output_proposals', [])}
    proposals_recalled = set(step.get('required_recall_proposal_hashes', [])) <= proposal_outputs
    fact_hash = hashlib.sha256(step['frame'].encode()).hexdigest()
    carries = lambda name: any(r.get('tool_name') == name and any(f.get('full_fact_sha256') == fact_hash for f in r.get('input_facts', [])) for r in tools)
    store_started = any(r.get('tool_name') == 'memory_store' for r in tools)
    typed_store = (not step.get('work_item_id') or any(r.get('tool_name') == 'memory_store'
        and any(p.get('proposal_sha256') == step.get('proposal_sha256') for p in r.get('input_proposals', [])) for r in tools))
    store_failed = any(r.get('event') == 'tool_failed' and r.get('tool_name') == 'memory_store' for r in (records or []))
    empty_store = any(r.get('details', {}).get('status') == 'completed' and r.get('details', {}).get('memoriesCount') == 0 for r in stores)
    # Only the controller's pre-dispatch terminal record proves non-submission.
    # Neither an empty/partial docker log nor an observed Agent answer proves
    # complete tool coverage. Lost writes must keep every successor gated.
    not_dispatched = (entry.get('dispatch_outcome') == 'NOT_DISPATCHED' and bool(entry.get('terminal_reason'))
                      and entry.get('dispatch_started_at_ms') is None and not entry.get('agent')
                      and not entry.get('release_receipt') and not tools and not completed)
    rejected = (inspection.get('result') == 'OBSERVED' and task.get('status') == 'failed') or empty_store
    committed_state = (True if committed else False if not_dispatched or rejected else
                       False if inspection.get('result') == 'OBSERVED' and inspection.get('archives') and stored else 'UNKNOWN')
    outcome, reason = 'UNKNOWN', 'TASK_EVIDENCE_INCOMPLETE'
    if entry.get('terminal_reason'):
        outcome, reason = entry.get('terminal_result', 'FAIL'), entry['terminal_reason']
    elif entry.get('agent', {}).get('result') == 'OBSERVED':
        answer = parse_answer(entry['agent'].get('answer', ''))
        if entry.get('answer_at_ms', 0) > entry['deadline_at_ms']:
            outcome, reason = 'FAIL', 'DEADLINE_MISSED'
        elif answer != step['expected']:
            outcome, reason = 'FAIL', 'ANSWER_INCORRECT'
        elif records is not None and (not any(r.get('tool_name') == 'memory_recall' for r in tools) or not store_started):
            outcome, reason = 'UNKNOWN', 'TASK_AUDIT_INCOMPLETE'
        elif sum(r.get('tool_name') == 'memory_store' for r in tools) > 1:
            outcome, reason = 'FAIL', 'AGENT_REPEATED_WRITE'
        elif recalls and not recalled:
            outcome, reason = 'FAIL', 'RECALL_EMPTY'
        elif records is not None and not predecessors_recalled:
            outcome, reason = 'FAIL', 'CONFIRMED_PREDECESSOR_NOT_RECALLED'
        elif records is not None and not proposals_recalled:
            outcome, reason = 'FAIL', 'TYPED_PROPOSAL_NOT_RECALLED'
        elif records is not None and not typed_store:
            outcome, reason = 'FAIL', 'TYPED_PROPOSAL_MISSING_FROM_STORE'
        elif records is not None and (not carries('memory_recall') or not carries('memory_store')):
            outcome, reason = 'FAIL', 'FULL_FACT_MISSING_FROM_TOOL_INPUT'
        elif empty_store:
            outcome, reason = 'FAIL', 'EXTRACTION_EMPTY'
        elif any(r.get('details', {}).get('status') == 'failed' for r in stores) or task.get('status') == 'failed':
            outcome, reason = 'FAIL', 'STORE_FAILED'
        elif decision_committed and recalled and (stored or (entry.get('inspection_observed_at_ms') is not None
                                                    and entry['inspection_observed_at_ms'] <= entry['deadline_at_ms'])):
            outcome, reason = 'PASS', 'GOAL_COMPLETED'
    elif entry.get('phase') == 'completed' and entry.get('answer_at_ms', 0) >= entry.get('deadline_at_ms', 0):
        outcome, reason = 'FAIL', 'DEADLINE_MISSED'
    requests = sorted({r['request_id'] for r in (records or []) if r.get('request_id')})
    full_sent = any(any(f.get('full_fact_sha256') == hashlib.sha256(step['frame'].encode()).hexdigest() for f in r.get('facts', []))
                    for r in (records or []) if r.get('event') == 'request_body')
    transport_evidence = [{k:r[k] for k in ('event','request_id','path','method','http_status','checked_at','reason',
                          'error_code','body_bytes','body_scan_complete','body_sha256','facts') if k in r}
                          for r in (records or []) if r.get('request_id')]
    return {'task_result': outcome, 'reason': reason, 'request_ids': requests,
            'attempted':True if any(r.get('event') == 'request_attempted' for r in (records or [])) else False if not_dispatched else 'UNKNOWN',
            'committed': committed_state,
            'proposal_persisted': committed_state if step.get('work_item_id') else None,
            'write_attempted':True if store_started or stores or store_failed or rejected or committed else False if not_dispatched else 'UNKNOWN',
            'write_outcome':('CONFIRMED' if committed_state is True else 'NOT_DISPATCHED' if not_dispatched
                             else 'REJECTED' if rejected
                             else 'INVALID_PERSISTED_INPUT' if committed_state is False else 'UNKNOWN'),
            'decision_committed': bool(decision_committed),
            'recalled': recalled if records is not None else 'UNKNOWN', 'tool_call_count': len(tools) if records is not None else None,
            'confirmed_predecessors_recalled': predecessors_recalled if records is not None else 'UNKNOWN',
            'confirmed_proposals_recalled': proposals_recalled if records is not None else 'UNKNOWN',
            'full_fact_sent':True if full_sent else False if not_dispatched else 'UNKNOWN',
            'gateway_outcome':entry.get('agent', {}).get('result'), 'gateway_error':entry.get('agent', {}).get('code'),
            'store_tool_failed':store_failed,
            'local_blocked_requests':sum(r.get('event') == 'request_blocked_local' for r in records) if records is not None else None,
            'transport_evidence':transport_evidence if records is not None else None,
            'tool_events':[{k:r[k] for k in ('event','tool_name','tool_call_id','checked_at') if k in r}
                           for r in (records or []) if r.get('event') in ('tool_started','tool_completed','tool_failed')],
            'deadline_missed': reason == 'DEADLINE_MISSED' or entry.get('answer_at_ms', 0) > entry.get('deadline_at_ms', float('inf'))}


def score_step(step, entry):
    try:
        return _score_step(step,entry)
    except (TypeError,ValueError,AttributeError,KeyError):
        return {'task_result':'UNKNOWN','reason':'MALFORMED_TASK_EVIDENCE','request_ids':[],
                'attempted':'UNKNOWN','committed':'UNKNOWN','recalled':'UNKNOWN','tool_call_count':None,
                'full_fact_sent':'UNKNOWN','deadline_missed':False}


def inspect_step(gateway, binding, step, entry):
    commits = [r for r in (entry.get('audit') or []) if r.get('event') == 'commit_receipt'
               and r.get('ov_session_id') == step['ov_session_id']]
    commit = commits[-1] if commits else {}
    return gateway.call(binding, 'inspect', ov_session_id=step['ov_session_id'],
                        extraction_task_id=commit.get('extraction_task_id'), archive_id=commit.get('archive_id'))


def reconcile_initialization(gateway, binding, rule, previous):
    """Only query the original seed session/task; never add or commit messages."""
    commit = previous.get('observation', {}).get('commit') or {}
    archive = (commit.get('archive_uri') or '').rstrip('/').split('/')[-1] or None
    inspection = gateway.call(binding,'inspect',ov_session_id=rule['ov_session_id'],
                              extraction_task_id=commit.get('task_id'),archive_id=archive)
    found = gateway.call(binding,'find',query=rule.get('initialization_query') or
                         'Private project '+rule['project_id'])
    task = inspection.get('task') or {}
    counts = (task.get('result') or {}).get('memories_extracted') or commit.get('memories_extracted') or {}
    extracted = isinstance(counts,dict) and bool(counts) and all(type(v) is int and v>=0 for v in counts.values()) and sum(counts.values())>0
    archived = any(rule['text'] in part.get('text','') for entry in inspection.get('archives',[])
                   for message in entry.get('messages',[]) for part in message.get('parts',[]) if isinstance(part,dict))
    text = json.dumps(found.get('memories',[]),ensure_ascii=False)
    confirmed = (inspection.get('result') == 'OBSERVED' and inspection.get('session',{}).get('commit_count',0)>0
                 and (task.get('status') or commit.get('status')) == 'completed' and extracted and archived
                 and found.get('result') == 'OBSERVED'
                 and all(str(rule[k]) in text for k in ('project_id','normal_code','review_code','threshold_cents')))
    previous.update(inspection=inspection,confirmed=bool(confirmed),status='confirmed' if confirmed else 'submission_unknown')
    return confirmed


def result_for(c, manifest, fixture, state, output):
    steps = []
    by_id = {(s['client_id'],s['step_id']): s for s in fixture['steps']}
    start = state.get('started_at_ms')
    for fact in manifest['facts']:
        key = fact['client_id'] + '/' + fact['step_id']; entry = state['steps'].get(key, {})
        planned = start + round(fact['release_offset_s'] * 1000) if start is not None else None
        deadline = planned + round(c['schedule']['deadline_s'] * 1000) if planned is not None else None
        effective = entry.get('effective_step') or by_id[(fact['client_id'],fact['step_id'])]
        scored = score_step(effective, entry) if entry.get('started_at_ms') is not None else {
            'task_result':'FAIL' if entry.get('terminal_reason') else 'NOT_RUN', 'reason':entry.get('terminal_reason','NOT_RELEASED'),
            'request_ids':[], 'attempted':False, 'committed':False, 'recalled':False, 'tool_call_count':0,
            'deadline_missed':entry.get('terminal_reason') == 'DEADLINE_MISSED', 'full_fact_sent':False}
        reconciliation = entry.get('reconciliation', {}).get('committed')
        if scored['committed'] is not True and (reconciliation is True or reconciliation is False):
            scored['committed'] = reconciliation
            if effective.get('work_item_id'): scored['proposal_persisted'] = reconciliation
        goal_confirmed = (max(entry['answer_at_ms'],entry['inspection_observed_at_ms'])
                          if scored['task_result'] == 'PASS' and type(entry.get('answer_at_ms')) is int
                          and type(entry.get('inspection_observed_at_ms')) is int else None)
        steps.append({**fact, 'predecessor':effective.get('predecessor'),
                      'confirmed_predecessors':effective.get('confirmed_predecessors'),
                      'planned_at_ms':planned, 'offered_at_ms':entry.get('offered_at_ms'), 'offered':bool(entry.get('offered_at_ms')),
                      'released_at_ms':entry.get('release_receipt', {}).get('at_ms'),
                      'release_source':entry.get('release_receipt', {}).get('source'),
                      'release_receipt':entry.get('release_receipt'),
                      'dispatch_started_at_ms':entry.get('dispatch_started_at_ms'),
                      'dispatch_attempted':entry.get('dispatch_started_at_ms') is not None,
                      'started_at_ms':entry.get('started_at_ms'), 'deadline_at_ms':deadline,
                      'completed_at_ms':entry.get('answer_at_ms') or entry.get('completed_at_ms'),
                      'answer_at_ms':entry.get('answer_at_ms'),'goal_confirmed_at_ms':goal_confirmed,
                      'goal_confirmation_scope':'conservative answer and independent archive confirmation observation',
                      'gateway_run_id':entry.get('agent', {}).get('gateway_run_id'),
                      'model':entry.get('agent', {}).get('model'), 'provider':entry.get('agent', {}).get('provider'),
                      'usage':entry.get('agent', {}).get('usage'),
                      'prerequisite':entry.get('prerequisite'),
                      'unresolved_proposals':entry.get('unresolved_proposals', []), **scored})
    outcomes = {(s['client_id'],s['step_id']):s for s in steps}
    for row in steps:
        predecessor = row.get('predecessor')
        if not predecessor:
            continue
        prior = outcomes[(row['client_id'],predecessor)]
        row['prerequisite'] = ('confirmed' if prior['committed'] is True else
                               'prerequisite_unknown' if prior['committed'] == 'UNKNOWN' else 'prerequisite_uncommitted')
        if row['task_result'] == 'PASS' and prior['committed'] is not True:
            row.update(task_result='UNKNOWN' if prior['committed'] == 'UNKNOWN' else 'FAIL', reason=row['prerequisite'].upper())
    complete = state.get('phase') == 'complete'
    expected_control = 'completed' if c['condition'] == 'fault' else 'no_fault'
    controls_ok = all(v.get('status') == expected_control and v.get('returncode',0) == 0 for v in state.get('controls', {}).values())
    expected_model = c['model_settings'].get('model')
    model_mismatches = [s['step_id'] for s in steps if s.get('model') and expected_model and expected_model not in
                        (s['model'], str(s.get('provider'))+'/'+s['model'])]
    verdict = 'PASS' if complete and controls_ok and len(state['controls']) == 2 and not model_mismatches else 'UNKNOWN' if start is not None else 'NOT_RUN'
    result = {'schema':'argus.continuous-result.v1', 'run_id':state['run_id'], 'operation_id':state['operation_id'],
              'block_id':manifest.get('block_id'), 'group':manifest.get('group'), 'condition':c['condition'],
              'scenario':c['scenario'],
              'fault_kind':manifest.get('fault_kind'), 'fault_scope':manifest.get('fault_scope', 'unspecified'),
              'result':verdict, 'evidence_scope':'execution_window_not_task_or_receipt_success',
              'measurement_complete':complete, 'started_at_ms':start, 'completed_at_ms':state.get('completed_at_ms'),
              'protocol_digest':manifest['protocol_hash'], 'structure_hash':manifest['structure_hash'],
              'model_mismatches':model_mismatches,
              'model_observations':{client:{k:v.get(k) for k in ('configured_model','model_config_sha256','sampling_observation')}
                                    for client,v in state.get('preflight',{}).items()},
              'manifest_sha256':sha(Path(output)/'manifest.json'), 'controls':state['controls'], 'steps':steps,
              'counts':{v:sum(s['task_result']==v for s in steps) for v in ('PASS','FAIL','UNKNOWN','NOT_RUN')},
              'planned_tasks':len(steps), 'offered_tasks':sum(s['offered'] for s in steps),
              'attempted_tasks':sum(s['attempted'] is True for s in steps), 'receipt_result':'UNKNOWN'}
    if c['scenario'] == 'work-item-v1':
        work_items = []
        for binding in c['bindings']:
            rows = [s for s in steps if s['client_id'] == binding['client_id']]
            proposals = [{'step_id':s['step_id'], 'fact_id':s['fact_id'],
                          'constraint_key':s['constraint_key'],
                          'status':'CONFIRMED' if s['committed'] is True else 'NOT_DISPATCHED' if not s['dispatch_attempted'] else
                                   s.get('write_outcome','UNKNOWN'),
                          'task_result':s['task_result']} for s in rows]
            unknown = [p['step_id'] for p in proposals if p['status'] == 'UNKNOWN']
            final = rows[-1]
            continuation = ('NOT_RUN' if start is None else 'UNKNOWN' if unknown else
                    'PASS' if final['task_result'] == 'PASS' else final['task_result'])
            complete_task = ('NOT_RUN' if start is None else 'UNKNOWN' if unknown or not complete or
                             any(s['task_result'] == 'UNKNOWN' for s in rows) else
                             'PASS' if all(p['status'] == 'CONFIRMED' for p in proposals) and
                             all(s['task_result'] == 'PASS' for s in rows) else 'FAIL')
            work_items.append({'work_item_id':rows[0]['work_item_id'], 'client_id':binding['client_id'],
                               'result':complete_task, 'complete_task_result':complete_task,
                               'continuation_result':continuation, 'proposals':proposals, 'unresolved_proposals':unknown,
                               'unapplied_required_proposals':[p['step_id'] for p in proposals if p['status'] != 'CONFIRMED'],
                               'final_step_id':final['step_id'],
                               'applied_confirmed_proposals':(final.get('confirmed_predecessors') or []) +
                                   ([final['step_id']] if final['committed'] is True else []),
                               'continuity_scope':'persistent_memory_across_fresh_agent_sessions',
                               'receipt_result':'UNKNOWN', 'legal_recovery_result':'UNKNOWN'})
        result['work_items'] = work_items
    atomic(Path(output)/'result.json', result)
    return result


def execute(config_path, output, action='run', gateway=None):
    c = configuration(config_path); output = Path(output); gateway = Observations(gateway or Gateway())
    with lock(output):
        if not (output/'state.json').exists():
            require(action != 'resume', 'no run to reconcile')
            prepare(config_path, output)
        state, manifest, fixture = read(output/'state.json'), read(output/'manifest.json'), read(output/'private-fixture.json')
        require(state['configuration_sha256'] == digest(c) and manifest['fixture_sha256'] == sha(output/'private-fixture.json'), 'configuration or private fixture changed')
        require(not os.environ.get('ARGUS_RUN_ID') or state['run_id'] == os.environ['ARGUS_RUN_ID'], 'run association changed')
        require(not os.environ.get('ARGUS_OPERATION_ID') or state['operation_id'] == os.environ['ARGUS_OPERATION_ID'], 'operation association changed')
        bindings = {b['client_id']:b for b in c['bindings']}
        by_key = {s['client_id']+'/'+s['step_id']:s for s in fixture['steps']}
        mutex = threading.RLock()

        def save():
            with mutex: secure_write(output/'state.json', state)

        def event(name, **values):
            with mutex: append(output/'events.jsonl', {'run_id':state['run_id'],'event':name,'at_ms':now_ms(),**values})

        if action == 'analyze':
            return result_for(c,manifest,fixture,state,output)
        if action == 'resume':
            # No Agent invocation, message/commit write, control, or shifted timeline.
            for rule in fixture['rules']:
                previous = state['initialization'].get(rule['client_id']+'/'+rule['project_id'])
                if previous and not previous.get('confirmed'):
                    reconcile_initialization(gateway,bindings[rule['client_id']],rule,previous)
                    save()
            for key, entry in state['steps'].items():
                if entry.get('started_at_ms'):
                    step = entry.get('effective_step') or by_key[key]; binding = bindings[step['client_id']]
                    observed_records = gateway.records(binding,entry['submitted_at'],entry['session_key'])
                    if observed_records is not None:
                        entry['audit'] = list({digest(record):record for record in (entry.get('audit') or [])+observed_records}.values())
                    observed = inspect_step(gateway,binding,step,entry)
                    previous = entry.get('inspection',{})
                    # A failed later query cannot erase an earlier confirmed archive.
                    previous_confirmed = score_step(step,entry)['committed'] is True
                    if observed.get('result') == 'OBSERVED' and not previous_confirmed:
                        entry['inspection'] = observed; entry['inspection_observed_at_ms'] = now_ms()
                    elif not previous:
                        entry['inspection'] = observed
                    save()
            if state.get('started_at_ms') is not None and state['phase'] != 'complete':
                state.update(phase='interrupted',completed_at_ms=now_ms()); save()
            event('reconciled_without_replay')
            return result_for(c,manifest,fixture,state,output)
        require(state['phase'] in ('prepared','initializing'), 'existing time window requires resume, not replay')
        for client, binding in bindings.items():
            observed = gateway.call(binding,'preflight',expected_model=c['model_settings'].get('model'),
                                    proposal_schema=PROPOSAL_SCHEMA if c['scenario'] == 'work-item-v1' else None)
            state['preflight'][client] = observed; save()
            if observed.get('result') != 'OBSERVED':
                event('preflight_failed',client_id=client)
                return result_for(c,manifest,fixture,state,output)
        if action == 'preflight':
            return {'result':'PASS','run_id':state['run_id'],'operation_id':state['operation_id']}
        state['phase'] = 'initializing'; save()
        for rule in fixture['rules']:
            key = rule['client_id']+'/'+rule['project_id']; binding = bindings[rule['client_id']]
            previous = state['initialization'].get(key)
            if previous and previous.get('confirmed'):
                continue
            if not previous:
                previous = {'status':'submission_unknown','ov_session_id':rule['ov_session_id']}
                state['initialization'][key] = previous; save()
                observed = gateway.call(binding,'seed',ov_session_id=rule['ov_session_id'],text=rule['text'],
                                        memory_policy=rule.get('memory_policy'),
                                        timeout_seconds=SEED_COMMIT_TIMEOUT_S)
                previous['observation'] = observed; save()
            confirmed = reconcile_initialization(gateway,binding,rule,previous); save()
            terminal = lambda prev: (prev.get('inspection',{}).get('task') or {}).get('status') in ('completed','failed')
            deadline = time.monotonic() + INIT_RECONCILE_BUDGET_S
            while not confirmed and not terminal(previous) and time.monotonic() < deadline:
                time.sleep(INIT_RECONCILE_INTERVAL_S)
                confirmed = reconcile_initialization(gateway,binding,rule,previous); save()
            if not confirmed:
                event('initialization_unconfirmed',client_id=rule['client_id'],project_id=rule['project_id'])
                return result_for(c,manifest,fixture,state,output)

        start = now_ms(); origin = time.monotonic()
        state.update(phase='running',started_at_ms=start); save(); event('window_started')
        pending = {client:[] for client in bindings}; active = {}; control_futures = {}
        timeline = sorted(fixture['steps'],key=lambda s:(s['release_offset_s'],s['client_id']))
        next_release = 0

        def finish_without_dispatch(entry, reason, result='UNKNOWN'):
            with mutex:
                entry.update(terminal_reason=reason,terminal_result=result,audit=[],dispatch_outcome='NOT_DISPATCHED',
                             phase='completed',completed_at_ms=now_ms())
                save()

        def confirm_proposal(step):
            """Query only the original write, including after service recovery."""
            key = step['client_id']+'/'+step['step_id']
            with mutex:
                original = copy.deepcopy(state['steps'].get(key, {}))
            if not original.get('started_at_ms'):
                return False if original.get('terminal_reason') else 'UNKNOWN'
            effective = original.get('effective_step') or step
            records = gateway.records(bindings[step['client_id']],original['submitted_at'],original['session_key'])
            if records is not None:
                original['audit'] = list({digest(r):r for r in (original.get('audit') or [])+records}.values())
            inspection = inspect_step(gateway,bindings[step['client_id']],effective,original)
            proof = {**original, 'inspection':inspection, 'inspection_observed_at_ms':now_ms()}
            query_confirmed = score_step(effective,proof)['committed']
            previously_confirmed = (score_step(effective,original)['committed'] is True or
                                    original.get('reconciliation', {}).get('committed') is True)
            confirmed = True if previously_confirmed else query_confirmed
            with mutex:
                current = state['steps'][key]
                current['reconciliation'] = {'checked_at_ms':now_ms(), 'committed':confirmed,
                                              'latest_query_committed':query_confirmed,
                                              'inspection':inspection}
                if query_confirmed is True:
                    current.update(audit=original.get('audit'),inspection=inspection)
                    if not previously_confirmed:
                        current['inspection_observed_at_ms'] = proof['inspection_observed_at_ms']
                save()
            return confirmed

        def worker(step):
            key = step['client_id']+'/'+step['step_id']; binding = bindings[step['client_id']]
            with mutex:
                entry = state['steps'][key]
                entry.update(phase='submission_unknown',started_at_ms=now_ms(),
                             submitted_at=datetime.now(timezone.utc).isoformat(),
                             session_key=f"argus-e4:{state['run_id']}:{step['client_id']}:{step['step_id']}:1")
                if step['predecessor']:
                    prior = state['steps'].get(step['client_id']+'/'+step['predecessor'],{})
                    prior_score = score_step(by_key[step['client_id']+'/'+step['predecessor']],prior) if prior.get('started_at_ms') else {}
                    entry['prerequisite'] = ('confirmed' if prior_score.get('committed') is True else
                                             'prerequisite_unknown' if prior_score.get('committed') == 'UNKNOWN' else 'prerequisite_uncommitted')
                save()
            if c['scenario'] == 'work-item-v1':
                if step['phase'] == 'recovery':
                    expected = 'completed' if c['condition'] == 'fault' else 'no_fault'
                    while now_ms() < entry['deadline_at_ms']:
                        with mutex:
                            recovered = state['controls'].get('recovery', {})
                            done = recovered.get('status') == expected and recovered.get('returncode', 0) == 0
                        if done:
                            break
                        if recovered.get('completed_at_ms') is not None:
                            finish_without_dispatch(entry,'RECOVERY_CONTROL_UNCONFIRMED')
                            return
                        time.sleep(0.02)
                    if not done:
                        finish_without_dispatch(entry,'RECOVERY_CONTROL_UNCONFIRMED')
                        return
                    ready = gateway.call(binding,'preflight',expected_model=c['model_settings'].get('model'),
                                         proposal_schema=PROPOSAL_SCHEMA)
                    with mutex:
                        entry['recovery_preflight'] = ready; save()
                    if ready.get('result') != 'OBSERVED':
                        finish_without_dispatch(entry,'RECOVERY_ENTRY_UNCONFIRMED')
                        return
                previous = [s for s in fixture['steps'] if s['client_id'] == step['client_id'] and s['index'] < step['index']]
                statuses = {s['step_id']:confirm_proposal(s) for s in previous}
                unresolved = [sid for sid, value in statuses.items() if value == 'UNKNOWN']
                checkpoint = step.get('required_checkpoint')
                with mutex:
                    entry.update(proposal_statuses=statuses,unresolved_proposals=unresolved)
                    save()
                if checkpoint and statuses.get(checkpoint) is not True:
                    finish_without_dispatch(entry,'CHECKPOINT_UNCONFIRMED',
                                            'UNKNOWN' if statuses.get(checkpoint) == 'UNKNOWN' else 'FAIL')
                    return
                if unresolved:
                    finish_without_dispatch(entry,'PRIOR_WRITE_UNRESOLVED')
                    return
                step = resolve_work_item(step,[s for s in previous if statuses[s['step_id']] is True])
                with mutex:
                    entry.update(effective_step=step,prerequisite='confirmed' if step['predecessor'] else None)
                    save()
            if now_ms() >= entry['deadline_at_ms']:
                finish_without_dispatch(entry,'DEADLINE_MISSED','FAIL')
                return
            timeout = max(1,min(c['qa_timeout_seconds'],(entry['deadline_at_ms']-now_ms())/1000))
            with mutex:
                entry['dispatch_started_at_ms'] = now_ms(); save()
                event('input_dispatch_intent',client_id=step['client_id'],task_id=step['step_id'],fact_id=step['fact_id'])
            observation = gateway.call(binding,'agent',session_key=entry['session_key'],prompt=prompt_for(step),
                                       task_id=step['step_id'],fact_id=step['fact_id'],work_item_id=step.get('work_item_id'),
                                       timeout_seconds=int(timeout))
            with mutex:
                receipt = observation.get('input_release')
                if (isinstance(receipt,dict) and receipt.get('source') == 'gateway_agent_dispatch'
                        and receipt.get('boundary') == 'openclaw_cli_input'
                        and receipt.get('task_id') == step['step_id'] and receipt.get('fact_id') == step['fact_id']
                        and receipt.get('work_item_id') == step.get('work_item_id')
                        and receipt.get('session_key') == entry['session_key']
                        and receipt.get('prompt_sha256') == hashlib.sha256(prompt_for(step).encode()).hexdigest()
                        and type(receipt.get('at_ms')) is int):
                    entry['release_receipt'] = receipt
                    event('input_released',client_id=step['client_id'],**receipt)
                entry.update(agent=observation,answer_at_ms=now_ms()); save()
            records = gateway.records(binding,entry['submitted_at'],entry['session_key'])
            with mutex: entry['audit'] = records; save()
            inspection = inspect_step(gateway,binding,step,entry)
            with mutex:
                entry.update(inspection=inspection,inspection_observed_at_ms=now_ms(),phase='completed',completed_at_ms=now_ms()); save()
                event('task_observed',client_id=step['client_id'],step_id=step['step_id'],**score_step(step,entry))

        def control(name):
            item = c['controls'][name]
            with mutex:
                state['controls'][name] = {'planned_at_ms':start+round(item['at_s']*1000),'started_at_ms':now_ms(),
                                           'status':'submission_unknown','argv_sha256':digest(item['argv'])}; save()
                event(name+'_started')
            result = {'status':'no_fault','returncode':0}
            if item['argv']:
                try:
                    env = {**os.environ,'ARGUS_RUN_ID':state['run_id'],'ARGUS_OPERATION_ID':state['operation_id']}
                    argv = [value.replace('{run_id}',state['run_id']).replace('{operation_id}',state['operation_id'])
                            .replace('{output}',str(output.resolve())) for value in item['argv']]
                    p = subprocess.run(argv,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=item['timeout_s'])
                    result = {'status':'completed','returncode':p.returncode}
                except (OSError,subprocess.SubprocessError):
                    result = {'status':'submission_unknown','error':'CONTROL_OUTCOME_UNKNOWN'}
            with mutex:
                state['controls'][name].update(result,completed_at_ms=now_ms()); save(); event(name+'_observed',**result)

        def dispatch_ready(workers):
            for client in bindings:
                if client in active and active[client].done():
                    active.pop(client).result()
                while pending[client] and state['steps'][client+'/'+pending[client][0]['step_id']]['deadline_at_ms']<=now_ms():
                    expired=pending[client].pop(0)
                    with mutex:
                        state['steps'][client+'/'+expired['step_id']].update(terminal_reason='DEADLINE_MISSED',phase='completed',completed_at_ms=now_ms()); save()
                if client not in active and pending[client]:
                    active[client]=workers.submit(worker,pending[client].pop(0))

        end_offset = max(s['release_offset_s'] for s in fixture['steps'])+c['schedule']['deadline_s']
        try:
            with ThreadPoolExecutor(max_workers=len(bindings)) as workers, ThreadPoolExecutor(max_workers=2) as controls:
                while True:
                    elapsed = time.monotonic()-origin
                    # Retire completed work before deciding whether a new release
                    # encounters a full queue; a finished future is not active.
                    dispatch_ready(workers)
                    for name in ('fault','recovery'):
                        if name not in control_futures and elapsed >= c['controls'][name]['at_s']:
                            control_futures[name] = controls.submit(control,name)
                    while next_release<len(timeline) and timeline[next_release]['release_offset_s']<=elapsed:
                        step=timeline[next_release]; next_release+=1; client=step['client_id']; key=client+'/'+step['step_id']
                        with mutex:
                            state['steps'][key] = {'offered_at_ms':now_ms(),'deadline_at_ms':start+round((step['release_offset_s']+c['schedule']['deadline_s'])*1000),'phase':'queued'}
                            if client in active and len(pending[client])>=1:
                                state['steps'][key].update(terminal_reason='QUEUE_FULL',phase='completed',completed_at_ms=now_ms())
                            else: pending[client].append(step)
                            save(); event('offered',client_id=client,step_id=step['step_id'],fact_id=step['fact_id'])
                        dispatch_ready(workers)
                    dispatch_ready(workers)
                    if elapsed>=end_offset and not active and not any(pending.values()) and all(f.done() for f in control_futures.values()):
                        for f in control_futures.values(): f.result()
                        break
                    time.sleep(min(0.05,max(0.005,end_offset-elapsed)))
            state.update(phase='complete',completed_at_ms=now_ms()); save(); event('window_completed')
        except BaseException:
            state.update(phase='interrupted',completed_at_ms=now_ms()); save(); event('window_interrupted')
            result_for(c,manifest,fixture,state,output)
            raise
        return result_for(c,manifest,fixture,state,output)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','preflight','run','resume','analyze'))
    parser.add_argument('--config',required=True); parser.add_argument('--output',required=True)
    args=parser.parse_args()
    result=prepare(args.config,args.output) if args.action=='prepare' else execute(args.config,args.output,args.action)
    print(json.dumps({k:result[k] for k in ('schema','run_id','result','measurement_complete','counts') if k in result}))
    return 0 if result.get('result','PASS')=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
