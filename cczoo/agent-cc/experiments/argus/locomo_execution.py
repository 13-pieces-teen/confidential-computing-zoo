"""Small QA scheduler and application measurements for the existing LoCoMo runner.

The scheduler changes when original questions run, never their text or answers.
History import and production admission remain in their existing components.
"""
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import math
import hashlib
import os
from pathlib import Path
import subprocess
import threading
import time

from common import atomic, digest, require


def stamp():
    return time.time_ns() // 1_000_000


def iso(value=None):
    return datetime.fromtimestamp((stamp() if value is None else value) / 1000, timezone.utc).isoformat()


def validate_execution(config):
    require(type(config.get('concurrent_clients', False)) is bool, 'concurrent_clients must be boolean')
    condition = config.get('condition', 'no_fault')
    require(condition in ('fault', 'no_fault'), 'condition must be fault or no_fault')
    schedule = config.get('schedule')
    if schedule is not None:
        require(isinstance(schedule, dict) and set(schedule) == {'release_interval_s', 'deadline_s'},
                'schedule needs release_interval_s and deadline_s')
        require(all(type(schedule[k]) in (int, float) and math.isfinite(schedule[k])
                    and 0 < schedule[k] <= 86400 for k in schedule), 'positive finite schedule required')
    controls = config.get('controls', {})
    require(isinstance(controls, dict), 'controls must be an object')
    if controls:
        require(schedule is not None and set(controls) == {'fault', 'recovery'},
                'controls require a schedule and both fault and recovery')
        for control in controls.values():
            require(isinstance(control, dict) and set(control) <= {'at_s', 'argv', 'timeout_s'}
                    and {'at_s', 'argv'} <= set(control), 'invalid control fields')
            require(type(control['at_s']) in (int, float) and math.isfinite(control['at_s'])
                    and 0 <= control['at_s'] <= 86400, 'invalid control time')
            require(type(control.get('timeout_s', 60)) in (int, float)
                    and 0 < control.get('timeout_s', 60) <= 3600, 'invalid control timeout')
            argv = control['argv']
            require(isinstance(argv, list) and all(isinstance(s, str) and s and '\0' not in s for s in argv),
                    'control argv must be an array of arguments')
            require(bool(argv) == (condition == 'fault'), 'fault needs commands; no_fault must use empty argv')
        require(controls['fault']['at_s'] < controls['recovery']['at_s'], 'recovery must follow fault')
    require(condition != 'fault' or controls, 'fault condition requires controls')


def execution_protocol(config):
    return {'concurrent_clients': config.get('concurrent_clients', False),
            'schedule': config.get('schedule'), 'condition': config.get('condition', 'no_fault'),
            'qa_timeout_seconds': config.get('qa_timeout_seconds', 180),
            'control_times': {name: {'at_s': item['at_s'], 'timeout_s': item.get('timeout_s', 60)}
                              for name, item in config.get('controls', {}).items()}}


def outcome(observation):
    if observation.get('result') == 'OBSERVED':
        return 'completed'
    value = observation.get('outcome')
    if value in ('rejected', 'failed', 'timeout', 'unknown'):
        return value
    code = observation.get('code', '')
    if code in ('QA_TIMEOUT', 'GATEWAY_TIMEOUT', 'ETIMEDOUT', 'ABORT_ERR', 'UND_ERR_HEADERS_TIMEOUT', 'UND_ERR_BODY_TIMEOUT'):
        return 'timeout'
    if observation.get('http_status') in (401, 403):
        return 'rejected'
    return 'unknown'


def execute_questions(config, fixture, bindings, state, output, gateway, save, *, resume=False):
    """Run QA after all imports. Once a window starts, resume only reads audit."""
    output = Path(output)
    mutex = threading.RLock()
    by_sample = defaultdict(list)
    for task in fixture['tasks']:
        by_sample[str(task['source_sample'])].append(task)

    def audit(binding, entry):
        try:
            return gateway.audit(binding, entry['submitted_at'], entry['session_key'])
        except (OSError, ValueError, subprocess.SubprocessError):
            return {'result': 'UNKNOWN', 'code': 'RECALL_AUDIT_UNAVAILABLE'}

    if resume and state.get('qa_started_at_ms') is not None:
        # No new QA, writes, controls or replacement time window on reconciliation.
        for task in fixture['tasks']:
            entry = state['questions'].get(task['task_id'])
            if not entry or not entry.get('submitted_at'):
                continue
            fresh = audit(bindings[str(task['source_sample'])], entry)
            if fresh.get('result') == 'OBSERVED' or not entry.get('injection'):
                entry['injection'] = fresh
            if entry.get('status') == 'submission_unknown':
                entry.update(status='UNKNOWN', outcome='unknown', reason='INTERRUPTED_QA_OUTCOME_UNKNOWN')
        state['reconciled_at_ms'] = stamp()
        if state.get('qa_phase') != 'complete':
            state['qa_phase'] = 'interrupted'
        save()
        return

    if not all(state['conversations'].get(sample, {}).get('status') == 'completed' for sample in bindings):
        return
    protocol = execution_protocol(config)
    state['execution_protocol'] = protocol
    state['controls'] = {}
    state['qa_phase'] = 'running'
    start = stamp()
    origin = time.monotonic()
    state['qa_started_at_ms'] = start
    state['initialization_completed_at_ms'] = start
    save()
    schedule = config.get('schedule')
    planned = []
    for sample, tasks in by_sample.items():
        for index, task in enumerate(tasks):
            offset = index * schedule['release_interval_s'] if schedule else 0
            planned.append((offset, sample, task))
            if task['task_id'] not in state['questions']:
                state['questions'][task['task_id']] = {
                    'status': 'NOT_RUN', 'outcome': 'not_run', 'source_sample': sample, 'category': task['category'],
                    'planned_at_ms': start + round(offset * 1000),
                    'deadline_at_ms': start + round((offset + schedule['deadline_s']) * 1000) if schedule else None}
    save()
    planned.sort(key=lambda item: (item[0], item[1]))

    def worker(sample, task):
        task_id = task['task_id']
        binding = bindings[sample]
        with mutex:
            entry = state['questions'][task_id]
            begun = stamp()
            remaining = ((entry['deadline_at_ms'] - begun) / 1000 if entry['deadline_at_ms'] is not None
                         else config.get('qa_timeout_seconds', 180))
            if remaining <= 0:
                entry.update(status='NOT_RUN', outcome='deadline_missed', reason='DEADLINE_BEFORE_START',
                             deadline_missed=True, finished_at_ms=begun)
                save()
                return
            session = 'argus-locomo-qa-' + digest([state['run_id'], task_id])[:32]
            question = ('Answer the question using your long-term memory. Give only the concise answer; '
                        'if unsupported, reply UNKNOWN.\nLast source session date: '
                        + str(task['sessions'][-1].get('date_time') or 'unspecified') + '\nQuestion: ' + task['question'])
            entry.update(status='submission_unknown', outcome='unknown', session_key=session, submitted_at=iso(begun),
                         started_at_ms=begun, question_sha256=hashlib.sha256(question.encode()).hexdigest())
            save()
        before = time.monotonic()
        try:
            observed = gateway.call(binding, 'qa', question=question, session_key=session,
                                    timeout_seconds=max(1, math.ceil(min(config.get('qa_timeout_seconds', 180), remaining))))
        except (OSError, ValueError, subprocess.SubprocessError):
            observed = {'result': 'UNKNOWN', 'code': 'GATEWAY_PROCESS_UNAVAILABLE', 'outcome': 'unknown'}
        finished = stamp()
        duration = (time.monotonic() - before) * 1000
        with mutex:
            entry.update(outcome=outcome(observed), finished_at_ms=finished, invocation_duration_ms=duration,
                         deadline_missed=entry['deadline_at_ms'] is not None and finished > entry['deadline_at_ms'])
            if observed.get('result') == 'OBSERVED':
                prediction = 'predictions/' + digest(task_id)[:24] + '.json'
                atomic(output / prediction, {'task_id': task_id, 'answer': observed['answer']})
                if os.name == 'posix':
                    os.chmod(output / prediction, 0o600)
                entry.update(status='completed', prediction=prediction,
                             answer_sha256=hashlib.sha256(observed['answer'].encode()).hexdigest(), run_id=observed.get('run_id'),
                             duration_ms=observed.get('duration_ms', duration), usage=observed.get('usage'),
                             model=observed.get('model'), provider=observed.get('provider'))
            else:
                entry.update(status='UNKNOWN', reason=observed.get('code', 'QA_RESULT_UNKNOWN'))
            save()
        return sample, task_id

    def collect_audit(sample, task_id):
        entry = state['questions'][task_id]
        evidence = audit(bindings[sample], entry)
        with mutex:
            entry['injection'] = evidence
            entry['completed_at'] = iso()
            save()

    def control(name, item):
        with mutex:
            state['controls'][name] = {'planned_at_ms': start + round(item['at_s'] * 1000),
                'started_at_ms': stamp(), 'status': 'submission_unknown', 'argv_sha256': digest(item['argv'])}
            save()
        result = {'status': 'no_fault', 'returncode': 0}
        if item['argv']:
            try:
                operation = state.get('operation_id') or ''
                env = {**os.environ, 'ARGUS_RUN_ID': state['run_id'], 'ARGUS_OPERATION_ID': operation}
                argv = [s.replace('{run_id}', state['run_id']).replace('{operation_id}', operation)
                        .replace('{output}', str(output.resolve())) for s in item['argv']]
                command = subprocess.run(argv, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         timeout=item.get('timeout_s', 60))
                result = {'status': 'completed' if command.returncode == 0 else 'failed', 'returncode': command.returncode}
            except (OSError, subprocess.SubprocessError):
                result = {'status': 'submission_unknown', 'error': 'CONTROL_OUTCOME_UNKNOWN'}
        with mutex:
            state['controls'][name].update(result, completed_at_ms=stamp())
            save()

    # A single outstanding worker per Gateway. The controller never waits on a
    # model before firing the independent fault or recovery command.
    concurrency = len(bindings) if config.get('concurrent_clients', False) else 1
    waiting = {sample: [] for sample in bindings}
    active, controls, audits = {}, {}, []
    cursor = 0
    try:
        with ThreadPoolExecutor(max_workers=concurrency) as pool, ThreadPoolExecutor(max_workers=2) as control_pool, \
                ThreadPoolExecutor(max_workers=len(bindings)) as audit_pool:
            while True:
                elapsed = time.monotonic() - origin
                for sample, future in list(active.items()):
                    if future.done():
                        del active[sample]
                        completed = future.result()
                        if completed:
                            audits.append(audit_pool.submit(collect_audit, *completed))
                for name, item in config.get('controls', {}).items():
                    if name not in controls and elapsed >= item['at_s']:
                        controls[name] = control_pool.submit(control, name, item)
                while cursor < len(planned) and planned[cursor][0] <= elapsed:
                    _, sample, task = planned[cursor]
                    cursor += 1
                    entry = state['questions'][task['task_id']]
                    if entry.get('status') == 'NOT_RUN' and entry.get('outcome') == 'not_run':
                        with mutex:
                            entry['released_at_ms'] = stamp()
                        waiting[sample].append(task)
                for sample in bindings:
                    if sample not in active and waiting[sample] and len(active) < concurrency:
                        task = waiting[sample].pop(0)
                        active[sample] = pool.submit(worker, sample, task)
                if (cursor == len(planned) and not active and not any(waiting.values())
                        and len(controls) == len(config.get('controls', {})) and all(f.done() for f in controls.values())):
                    for future in controls.values():
                        future.result()
                    with mutex:
                        state['qa_completed_at_ms'] = stamp()
                    break
                time.sleep(0.01)
            for future in audits:
                future.result()
        state.update(qa_phase='complete')
        save()
    except BaseException:
        with mutex:
            state.update(qa_phase='interrupted', interrupted_at_ms=stamp())
            save()
        raise


def latency(values):
    values = sorted(v for v in values if type(v) in (int, float) and math.isfinite(v) and v >= 0)
    def percentile(q):
        if not values:
            return None
        index = (len(values) - 1) * q
        lower = math.floor(index)
        upper = math.ceil(index)
        return values[lower] + (values[upper] - values[lower]) * (index - lower)
    return {'n': len(values), 'mean_ms': sum(values) / len(values) if values else None,
            'p50_ms': percentile(.5), 'p95_ms': percentile(.95)}


def application_summary(fixture, state):
    """Application completion and receipt observations are separate from QA F1."""
    def summarize(tasks):
        entries = [state['questions'].get(task['task_id'], {}) for task in tasks]
        outcomes = Counter(e.get('outcome', 'completed' if e.get('status') == 'completed' else 'not_run'
                                 if not e or e.get('status') == 'NOT_RUN' else 'unknown') for e in entries)
        complete = [e for e in entries if e.get('status') == 'completed']
        valid = [e for e in complete if e.get('injection', {}).get('code') == 'INJECTION_OBSERVED'
                 and not e.get('deadline_missed')]
        requests = {}
        for entry in entries:
            for request in entry.get('injection', {}).get('requests', []):
                requests[request['request_id']] = request
        request_rows = list(requests.values())
        return {'planned': len(tasks), 'attempted': sum(bool(e.get('started_at_ms')) for e in entries),
                'outcome_counts': {name: outcomes[name] for name in
                    ('completed', 'rejected', 'failed', 'timeout', 'unknown', 'deadline_missed', 'not_run')},
                'completed': len(complete), 'completion_rate': len(complete) / len(tasks) if tasks else None,
                'valid_completed': len(valid), 'valid_completion_rate': len(valid) / len(tasks) if tasks else None,
                'valid_completion_definition': 'observed answer and memory injection within deadline; not answer correctness',
                'deadline_misses': sum(bool(e.get('deadline_missed')) for e in entries),
                'latency': latency(e.get('duration_ms') for e in valid),
                'invocation_latency': latency(e.get('invocation_duration_ms') for e in valid),
                'completed_latency': latency(e.get('duration_ms') for e in complete),
                'injection_counts': dict(Counter(e.get('injection', {}).get('code', 'NOT_RUN') for e in entries)),
                'requests': {'count': len(request_rows), 'outcome_counts': dict(Counter(r.get('outcome', 'unknown') for r in request_rows)),
                    'latency': latency(r.get('duration_ms') for r in request_rows if r.get('outcome') == 'completed'),
                    'coverage': 'OBSERVED' if entries and all(e.get('injection', {}).get('request_audit_complete') is True
                                                           for e in entries if e.get('started_at_ms')) and request_rows else 'UNKNOWN'},
                'invocations': [{key: e.get(key) for key in ('session_key', 'started_at_ms', 'finished_at_ms', 'outcome')}
                                for e in entries if e.get('started_at_ms') is not None]}
    by_client = {sample: summarize([t for t in fixture['tasks'] if str(t['source_sample']) == sample])
                 for sample in sorted({str(t['source_sample']) for t in fixture['tasks']})}
    summary = summarize(fixture['tasks'])
    intervals = [(row['started_at_ms'], row['finished_at_ms'], sample)
                 for sample, client in by_client.items() for row in client['invocations']
                 if row['finished_at_ms'] is not None]
    points = sorted([(start, 1) for start, end, _ in intervals if end > start]
                    + [(end, -1) for start, end, _ in intervals if end > start])
    running, maximum = 0, 0
    for _, change in points:
        running += change
        maximum = max(maximum, running)
    summary.update(by_client=by_client, concurrency={'max_in_flight': maximum,
        'scope': 'observed Gateway invocation intervals; incomplete invocations do not establish their remote completion',
        'intervals': [{'source_sample': sample, 'started_at_ms': start, 'finished_at_ms': end} for start, end, sample in intervals]})
    recovery = state.get('controls', {}).get('recovery', {})
    anchor = recovery.get('started_at_ms') if recovery.get('status') == 'completed' else None
    completions = [e['finished_at_ms'] for e in state['questions'].values() if anchor is not None
                   and e.get('status') == 'completed' and e.get('finished_at_ms', 0) >= anchor
                   and e.get('started_at_ms', 0) >= anchor
                   and e.get('injection', {}).get('code') == 'INJECTION_OBSERVED' and not e.get('deadline_missed')]
    accesses = [r['at_ms'] for e in state['questions'].values() for r in e.get('injection', {}).get('requests', [])
                if anchor is not None and r.get('outcome') == 'completed' and type(r.get('at_ms')) is int and r['at_ms'] >= anchor
                and type(r.get('started_at_ms')) is int and r['started_at_ms'] >= anchor]
    summary['recovery'] = {'command_started_at_ms': anchor,
        'first_successful_access_at_ms': min(accesses) if accesses else None,
        'first_completed_task_at_ms': min(completions) if completions else None,
        'first_access_ms': min(accesses) - anchor if accesses else None,
        'first_task_ms': min(completions) - anchor if completions else None,
        'scope': 'new calls begun after explicit recovery command; instance admission remains separate evidence'}
    return summary
