#!/usr/bin/env python3
"""Run LoCoMo-derived imports and fresh-session QA through admitted Gateways.

This is an application workload, not a security oracle or the official LoCoMo
protocol. History writes use the pinned plugin's native SPIFFE transport; QA
uses the real OpenClaw ContextEngine. Reference answers stay in the grader.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import string
import subprocess
import time
from urllib.parse import quote
import uuid

from common import atomic, digest, lock, read, require, resolve, sha

ROOT = Path(__file__).resolve().parent


def now():
    return datetime.now(timezone.utc).isoformat()


def secure_atomic(path, value):
    atomic(path, value)
    if os.name == 'posix':
        os.chmod(path, 0o600)


def configuration(path):
    path = Path(path).resolve()
    value = read(path)
    require(value.get('schema') == 'argus.locomo-run.v1', 'invalid LoCoMo configuration schema')
    require(set(value) <= {'schema', 'fixture', 'bindings', 'poll_attempts', 'poll_seconds', 'qa_timeout_seconds'}, 'unknown LoCoMo configuration field')
    fixture = resolve(path.parent, value['fixture'])
    data = read(fixture)
    require(data.get('schema') == 'argus.locomo-derived.v1' and data.get('tasks'), 'invalid derived fixture')
    require(len({t['task_id'] for t in data['tasks']}) == len(data['tasks']), 'duplicate task ID')
    samples = {str(t['source_sample']) for t in data['tasks']}
    bindings, scopes, containers = {}, set(), set()
    for item in value['bindings']:
        require(set(item) == {'source_sample', 'container', 'docker_user', 'config_path', 'agent_id', 'account_id', 'user_id', 'client_spiffe_id', 'server_spiffe_id'}, 'invalid binding fields')
        sample = str(item['source_sample'])
        require(sample not in bindings, 'duplicate sample binding')
        require(item['user_id'].lower() not in ('root', 'admin'), 'ordinary business user required')
        scope = (item['account_id'], item['user_id'])
        require(scope not in scopes and item['container'] not in containers, 'each conversation needs its own user and dedicated Gateway within a batch')
        require(all(isinstance(v, str) and v and '\x00' not in v for k, v in item.items() if k != 'source_sample'), 'invalid binding value')
        bindings[sample] = item
        scopes.add(scope); containers.add(item['container'])
    require(set(bindings) == samples, 'bindings must exactly cover selected conversations')
    require(isinstance(value.get('poll_attempts', 120), int) and 1 <= value.get('poll_attempts', 120) <= 3600, 'invalid poll attempts')
    require(0 <= value.get('poll_seconds', 2) <= 60, 'invalid poll interval')
    require(1 <= value.get('qa_timeout_seconds', 180) <= 3600, 'invalid QA timeout')
    # Reference text is not part of the operational binding. Only hashes and
    # public task identifiers are retained in run/state records.
    return value, data, bindings, digest({'config': value, 'fixture_sha256': sha(fixture)})


class Gateway:
    def __init__(self):
        self.script = (ROOT / 'locomo_gateway.mjs').read_text(encoding='utf-8')

    def call(self, binding, action, **payload):
        spec = {**{k: binding[k] for k in ('agent_id', 'account_id', 'user_id', 'client_spiffe_id', 'server_spiffe_id')},
                'session_key': 'argus-locomo-import', 'action': action, **payload}
        command = ['docker', 'exec', '-i', '-u', binding['docker_user'], '-e',
                   'OPENCLAW_CONFIG_PATH=' + binding['config_path'], binding['container'],
                   'node', '--input-type=module', '-e', self.script]
        try:
            result = subprocess.run(command, input=json.dumps(spec), text=True, encoding='utf-8', capture_output=True,
                                    timeout=payload.get('timeout_seconds', 180) + 90)
            value = json.loads(result.stdout.splitlines()[-1])
            require(isinstance(value, dict), 'invalid Gateway observation')
            return value
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            # Do not copy process stderr: upstream errors may contain inputs or keys.
            return {'result': 'UNKNOWN', 'code': 'GATEWAY_PROCESS_UNAVAILABLE'}

    def audit(self, binding, since, session_key):
        try:
            output = subprocess.run(['docker', 'logs', '--since', since, binding['container']],
                                    capture_output=True, text=True, encoding='utf-8', timeout=30)
            records = []
            for line in (output.stdout + '\n' + output.stderr).splitlines():
                start = line.find('{')
                if start < 0:
                    continue
                try:
                    entry = json.loads(line[start:])
                except ValueError:
                    continue
                if isinstance(entry, dict) and str(entry.get('component', '')).startswith('argus-openclaw'):
                    records.append(entry)
            return injection_evidence(records, session_key, binding)
        except (OSError, subprocess.SubprocessError):
            return {'result': 'UNKNOWN', 'code': 'RECALL_AUDIT_UNAVAILABLE'}


def injection_evidence(records, session_key, binding):
    spans = [r for r in records if r.get('component') == 'argus-openclaw-recall'
             and r.get('session_key') == session_key and r.get('event') in ('completed', 'failed')]
    if not spans:
        return {'result': 'UNKNOWN', 'code': 'RECALL_AUDIT_MISSING'}
    span = spans[-1]
    requests = set(span.get('request_ids', []))
    receipts = [r for r in records if r.get('request_id') in requests and r.get('http_status', 0) in range(200, 300)]
    associated = [r for r in receipts if r.get('client_spiffe_id') == binding['client_spiffe_id']
                  and r.get('server_spiffe_id') == binding['server_spiffe_id']]
    # These are content-block hashes, not proof of semantic support for an answer.
    observed = {k: span.get(k) for k in ('context_span_id', 'source_observed', 'memory_count',
                                        'recall_block_sha256', 'recall_block_chars', 'recall_block_in_input', 'recall_block_in_output', 'search')}
    if span.get('event') == 'failed':
        code = 'ASSEMBLY_FAILED'
    elif not associated:
        code = 'RECALL_IDENTITY_UNOBSERVED'
    elif span.get('recall_block_chars', 0) == 0:
        code = 'RECALL_EMPTY'
    elif span.get('recall_block_in_output') is not True:
        code = 'INJECTION_UNOBSERVED'
    else:
        code = 'INJECTION_OBSERVED'
    return {'result': 'OBSERVED' if code == 'INJECTION_OBSERVED' else 'UNKNOWN', 'code': code,
            'request_ids': sorted(requests), 'associated_requests': len(associated), **observed}


class Pending(Exception):
    pass


def counts(value):
    if not isinstance(value, dict) or any(not isinstance(n, int) or isinstance(n, bool) or n < 0 for n in value.values()):
        return None
    return {'total': sum(value.values()), 'by_category': value}


def execute(config_path, output, resume=False, gateway=None, sleep=time.sleep):
    config, fixture, bindings, fingerprint = configuration(config_path)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    gateway = gateway or Gateway()
    with lock(output):
        path = output / 'state.json'
        if path.exists():
            require(resume, 'existing run requires resume')
            state = read(path)
            require(state['configuration_sha256'] == fingerprint, 'configuration or fixture changed')
            for field, variable in (('run_id', 'ARGUS_RUN_ID'), ('operation_id', 'ARGUS_OPERATION_ID')):
                require(not os.environ.get(variable) or state.get(field) == os.environ[variable], 'runner association changed')
        else:
            require(not resume, 'no run to resume')
            state = {'schema': 'argus.locomo-state.v1', 'run_id': os.environ.get('ARGUS_RUN_ID') or uuid.uuid4().hex,
                     'operation_id': os.environ.get('ARGUS_OPERATION_ID'),
                     'configuration_sha256': fingerprint, 'source_sha256': fixture['source_sha256'],
                     'started_at': now(), 'operations': {}, 'conversations': {}, 'questions': {}, 'preflight': {}}

        def save():
            state['updated_at'] = now()
            secure_atomic(path, state)

        def api(binding, method, route, body=None):
            observation = gateway.call(binding, 'api', method=method, route=route, **({'body': body} if body is not None else {}))
            if method == 'GET' and observation.get('result') != 'OBSERVED':
                observation = gateway.call(binding, 'api', method=method, route=route)
            if observation.get('result') != 'OBSERVED':
                raise Pending(observation.get('code', 'API_RESULT_UNKNOWN'))
            return observation

        def mutate(operation, binding, route, body):
            saved = state['operations'].get(operation)
            if saved:
                if saved['status'] == 'completed':
                    return saved['observation']['value']
                raise Pending('SUBMISSION_OUTCOME_UNKNOWN')
            state['operations'][operation] = {'status': 'submission_unknown', 'submitted_at': now(),
                                               'request_sha256': digest({'route': route, 'body': body})}
            save()
            observation = api(binding, 'POST', route, body)
            state['operations'][operation].update(status='completed', observation=observation, completed_at=now())
            save()
            return observation['value']

        save()
        # A changed deployment/protocol cannot silently resume the same experiment.
        for sample, binding in bindings.items():
            observed = gateway.call(binding, 'preflight')
            require(observed.get('result') == 'OBSERVED' and observed.get('read_only_qa') is True,
                    'LoCoMo preflight failed: ' + observed.get('code', 'PROTOCOL_UNVERIFIED'))
            old = state['preflight'].get(sample)
            require(old is None or (old.get('config_sha256') == observed.get('config_sha256')
                    and old.get('scope') == observed.get('scope')), 'Gateway configuration or scope changed')
            state['preflight'][sample] = observed
        save()
        tasks_by_sample = defaultdict(list)
        for task in fixture['tasks']:
            tasks_by_sample[str(task['source_sample'])].append(task)
        for sample, tasks in tasks_by_sample.items():
            binding = bindings[sample]
            sessions = tasks[0]['sessions']
            require(all(digest(t['sessions']) == digest(sessions) for t in tasks), 'inconsistent conversation sessions')
            conversation = state['conversations'].setdefault(sample, {'status': 'NOT_RUN', 'sessions': {}})
            try:
                for session in sessions:
                    key = session['source_session']
                    entry = conversation['sessions'].setdefault(key, {})
                    sid = entry.setdefault('session_id', 'argus-locomo-' + digest([state['run_id'], sample, key])[:32])
                    route = '/api/v1/sessions/' + quote(sid, safe='')
                    prefix = sample + '/' + key
                    if entry.get('status') == 'completed':
                        continue
                    mutate(prefix + '/create', binding, '/api/v1/sessions', {'session_id': sid})
                    for index, message in enumerate(session['messages']):
                        # Keep source timestamps and speakers in the derived
                        # transcript without treating speakers as security users.
                        text = '[%s] %s (%s): %s' % (session.get('date_time') or 'time unknown', message['speaker'],
                                                    message.get('dia_id', index), message['text'])
                        mutate(prefix + '/message/' + str(index), binding, route + '/messages',
                               {'role': 'user', 'parts': [{'type': 'text', 'text': text}], 'role_id': message['speaker']})
                    commit = mutate(prefix + '/commit', binding, route + '/commit', {})
                    entry['commit'] = commit
                    entry['task_id'] = commit.get('task_id')
                    save()
                    final = commit
                    if entry['task_id']:
                        final = None
                        for attempt in range(config.get('poll_attempts', 120)):
                            task = api(binding, 'GET', '/api/v1/tasks/' + quote(entry['task_id'], safe=''))['value']
                            entry['task_status'] = task.get('status'); save()
                            if task.get('status') == 'failed':
                                entry['status'] = 'failed'; raise Pending('EXTRACTION_FAILED')
                            if task.get('status') == 'completed':
                                final = {'status': 'completed', **(task.get('result') or {})}; break
                            if task.get('status') not in ('pending', 'running', 'queued', 'processing', 'accepted'):
                                raise Pending('TASK_STATUS_UNKNOWN')
                            if attempt + 1 < config.get('poll_attempts', 120):
                                sleep(config.get('poll_seconds', 2))
                        if final is None:
                            raise Pending('EXTRACTION_TIMEOUT_KNOWN_TASK')
                    if final.get('status') != 'completed':
                        raise Pending('EXTRACTION_NOT_COMPLETED')
                    entry['extraction'] = counts(final.get('memories_extracted'))
                    detail = api(binding, 'GET', route)['value']
                    context = api(binding, 'GET', route + '/context')['value']
                    entry['archive'] = detail.get('commit_count', 0) > 0 and bool(str(context.get('latest_archive_overview') or '').strip())
                    if not entry['archive']:
                        raise Pending('ARCHIVE_UNOBSERVED')
                    # Natural sessions can legitimately add zero new memories.
                    # Preserve 0 and unknown separately; QA determines task utility.
                    entry.update(status='completed', completed_at=now()); save()
                conversation.update(status='completed', completed_at=now())
            except Pending as error:
                conversation.update(status='UNKNOWN', reason=str(error)); save()
                continue
            save()
            for task in tasks:
                task_id = task['task_id']
                previous = state['questions'].get(task_id)
                if previous:
                    # Neither an unknown model request nor a completed answer is
                    # replayed; a new experimental replicate needs a new run/user.
                    continue
                qsession = 'argus-locomo-qa-' + digest([state['run_id'], task_id])[:32]
                date = sessions[-1].get('date_time') or 'unspecified'
                question = ('Answer the question using your long-term memory. Give only the concise answer; '
                            'if unsupported, reply UNKNOWN.\nLast source session date: ' + str(date) + '\nQuestion: ' + task['question'])
                state['questions'][task_id] = {'status': 'submission_unknown', 'session_key': qsession,
                    'source_sample': sample, 'category': task['category'], 'submitted_at': now(),
                    'question_sha256': hashlib.sha256(question.encode()).hexdigest()}
                save()
                observation = gateway.call(binding, 'qa', question=question, session_key=qsession,
                                           timeout_seconds=config.get('qa_timeout_seconds', 180))
                item = state['questions'][task_id]
                if observation.get('result') == 'OBSERVED':
                    # The answer artifact is private local evaluation data. It
                    # contains no reference answer and is never imported to memory.
                    prediction = 'predictions/' + digest(task_id)[:24] + '.json'
                    secure_atomic(output / prediction, {'task_id': task_id, 'answer': observation['answer']})
                    item.update(status='completed', prediction=prediction, answer_sha256=hashlib.sha256(observation['answer'].encode()).hexdigest(),
                                run_id=observation.get('run_id'), duration_ms=observation.get('duration_ms'), usage=observation.get('usage'),
                                model=observation.get('model'), provider=observation.get('provider'))
                else:
                    item.update(status='UNKNOWN', reason=observation.get('code', 'QA_RESULT_UNKNOWN'))
                item['injection'] = gateway.audit(binding, item['submitted_at'], qsession)
                item['completed_at'] = now(); save()
        result = analyze(fixture, state, output)
        secure_atomic(output / 'result.json', result)
        return result


def normalize(text):
    text = str(text).lower().translate(str.maketrans('', '', string.punctuation))
    return ' '.join(re.sub(r'\b(a|an|the)\b', ' ', text).split())


def token_f1(prediction, answer):
    prediction, answer = normalize(prediction).split(), normalize(answer).split()
    if not prediction or not answer:
        return float(prediction == answer)
    overlap = sum((Counter(prediction) & Counter(answer)).values())
    return 2 * overlap / (len(prediction) + len(answer))


def score(prediction, task):
    if task['category'] == 5:
        return {'abstention': float(normalize(prediction) in ('unknown', 'not enough information', 'cannot determine'))}
    answer = task['reference_answer']
    if task['category'] == 1:
        # Report the declared derived multi-answer rule, not an official score.
        references = answer if isinstance(answer, list) else str(answer).split(',')
        pieces = str(prediction).split(',')
        f1 = sum(max(token_f1(piece, reference) for piece in pieces) for reference in references) / len(references)
        exact_references = [', '.join(str(reference) for reference in references)]
    else:
        references = answer if isinstance(answer, list) else [answer]
        f1 = max(token_f1(prediction, reference) for reference in references)
        exact_references = references
    return {'token_f1': f1, 'exact_match': float(normalize(prediction) in [normalize(a) for a in exact_references])}


def analyze(fixture, state, directory):
    directory = Path(directory)
    rows, by_category, by_conversation = [], defaultdict(list), defaultdict(list)
    for task in fixture['tasks']:
        item = state['questions'].get(task['task_id'], {})
        row = {'task_id': task['task_id'], 'source_sample': str(task['source_sample']), 'category': task['category'],
               'status': item.get('status', 'NOT_RUN'), 'injection': item.get('injection', {}).get('code', 'NOT_RUN')}
        if item.get('status') == 'completed':
            row.update(score(read(directory / item['prediction'])['answer'], task))
            row['duration_ms'] = item.get('duration_ms')
        rows.append(row); by_category[str(task['category'])].append(row); by_conversation[str(task['source_sample'])].append(row)

    def summary(values):
        out = {'tasks': len(values), 'status_counts': dict(Counter(v['status'] for v in values))}
        for metric in ('token_f1', 'exact_match', 'abstention'):
            relevant = [v for v in values if (v['category'] == 5) == (metric == 'abstention')]
            measured = [v[metric] for v in relevant if metric in v]
            out[metric] = {'answered_mean': sum(measured) / len(measured) if measured else None,
                           'measured': len(measured), 'eligible': len(relevant),
                           'all_tasks_zero_for_uncompleted': sum(measured) / len(relevant) if relevant else None}
        return out
    conversations = {k: summary(v) for k, v in by_conversation.items()}
    means = [v['token_f1']['all_tasks_zero_for_uncompleted'] for v in conversations.values()
             if v['token_f1']['eligible']]
    rng = random.Random(0)
    replicates = sorted(sum(rng.choice(means) for _ in means) / len(means) for _ in range(2000)) if len(means) >= 2 else []
    complete = all(v['status'] == 'completed' for v in rows)
    return {'schema': 'argus.locomo-result.v1', 'result': 'COMPLETE' if complete else 'INCOMPLETE',
            'run_id': state['run_id'], 'operation_id': state.get('operation_id'),
            'source_sha256': fixture['source_sha256'], 'selection': fixture.get('selection'),
            'protocol': 'LoCoMo-derived; private per-conversation users; read-only QA; no official-score claim',
            'scoring': 'normalized token F1; category 1 comma/list best-piece mean; category 5 exact declared abstention',
            'overall': summary(rows), 'by_category': {k: summary(v) for k, v in by_category.items()},
            'by_conversation': conversations, 'conversation_macro_f1': sum(means) / len(means) if means else None,
            'conversation_bootstrap_95': [replicates[49], replicates[1949]] if replicates else None,
            'cluster_count': len(means), 'cluster_caution': 'few conversation clusters; QA items are not independent replicates',
            'questions': rows, 'delivery_compliance': 'NOT_ASSESSED_BY_QA',
            'real_tdx_acceptance': 'NOT_ASSESSED_BY_QA'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('run', 'resume', 'analyze', 'preflight'))
    parser.add_argument('--config', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.command == 'analyze':
        _, fixture, _, fingerprint = configuration(args.config)
        state = read(Path(args.output) / 'state.json')
        require(state['configuration_sha256'] == fingerprint, 'configuration or fixture changed')
        result = analyze(fixture, state, args.output)
        secure_atomic(Path(args.output) / 'result.json', result)
    elif args.command == 'preflight':
        _, _, bindings, _ = configuration(args.config)
        adapter = Gateway()
        result = {'bindings': {sample: adapter.call(binding, 'preflight') for sample, binding in bindings.items()}}
        result['result'] = 'PASS' if all(v.get('result') == 'OBSERVED' and v.get('read_only_qa') is True for v in result['bindings'].values()) else 'FAIL'
        secure_atomic(Path(args.output) / 'preflight.json', result)
    else:
        result = execute(args.config, args.output, resume=args.command == 'resume')
    print(json.dumps({'result': result['result'], 'output': str(Path(args.output).resolve())}))
    return 0 if result['result'] in ('COMPLETE', 'PASS') else 1


if __name__ == '__main__':
    raise SystemExit(main())
