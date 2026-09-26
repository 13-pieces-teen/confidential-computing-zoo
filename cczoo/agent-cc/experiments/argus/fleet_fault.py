#!/usr/bin/env python3
"""Fixed E4 local/shared fault trial using real Gateway memory reads.

Business setup is performed once by fleet_business. This tool never recreates a
container or replays an unknown fault/Agent question. Recovery is an explicit
operator deployment action; observe-recovery only verifies its result.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from common import atomic, digest, lock, measurement_log, read, require, resolve, sha, write_measurement

CLIENT = Path(__file__).resolve().parents[2] / 'adapters/OpenClaw/spiffe_client'
sys.path.insert(0, str(CLIENT))
import fleet_business as business
from verify_audit import find_recall


def now():
    return time.time_ns() // 1000000


def settings(path):
    source = Path(path).resolve()
    c = read(source)
    require(c.get('schema') == 'argus.fleet-fault.v1', 'invalid fleet fault schema')
    require(c.get('event') in ('client-stop', 'shared-service-fault'), 'select local or shared fault')
    c['business_config'] = str(resolve(source.parent, c['business_config']))
    c['business_evidence'] = str(resolve(source.parent, c['business_evidence']))
    _, items, config_digest = business.config(Path(c['business_config']))
    previous = read(Path(c['business_evidence']) / 'result.json')
    require(previous.get('completed') is True and previous.get('result') == 'PASS'
            and previous.get('config_sha256') == config_digest, 'matching completed private-memory acceptance required')
    require(c.get('run_id') and all(v.get('result') == 'PASS' for v in previous['instances'].values()), 'business milestone missing')
    require(len(items) >= 2 and set(previous['instances']) == {i['name'] for i in items}, 'business instance set differs')
    if c['event'] == 'client-stop':
        require(c.get('instance') in previous['instances'], 'select an exact client instance')
    else:
        c['server_fault_config'] = str(resolve(source.parent, c['server_fault_config']))
        server = read(c['server_fault_config'])
        require(server['run_id'] == c['run_id'], 'shared fault run ID differs')
        require(server['event'] in ('helper-freeze', 'helper-crash', 'target-exit', 'config-change', 'same-container-restart'), 'unsupported shared fault')
        c['_server'] = server
    for key, default in (('baseline_rounds', 3), ('observation_rounds', 10), ('recovery_rounds', 3)):
        c[key] = c.get(key, default)
        require(type(c[key]) is int and c[key] >= 3, 'at least three rounds required')
    c['interval_seconds'] = c.get('interval_seconds', 1)
    require(type(c['interval_seconds']) in (int, float) and 0 <= c['interval_seconds'] <= 60, 'invalid interval')
    c['settle_seconds'] = c.get('settle_seconds', 10)
    require(type(c['settle_seconds']) in (int, float) and 0 <= c['settle_seconds'] <= 300, 'invalid settle period')
    keys = {i['name']: business.secret(i) for i in items}
    facts = {}
    for item in items:
        directory = Path(c['business_evidence']) / item['name']
        marker, fact = (directory / 'marker.txt').read_text().strip(), (directory / 'fact.txt').read_text().strip()
        require(marker and fact and fact not in marker, 'invalid private-memory fixture')
        facts[item['name']] = {'marker': marker, 'fact': fact, 'fact_sha256': hashlib.sha256(fact.encode()).hexdigest()}
    fingerprint = digest({'config': c, 'business': config_digest, 'milestone': sha(Path(c['business_evidence']) / 'result.json'),
                          'fixtures': {n: digest(v) for n, v in facts.items()}})
    return c, items, keys, facts, fingerprint


def observe(item, key, fact, run_id, phase, number):
    started = now()
    observed = business.probe(item, key, query=fact['marker'], fact_sha256=fact['fact_sha256'])
    available = business.authenticated(observed) and observed.get('contains_fact') is True
    unavailable = (observed.get('result') == 'UNAVAILABLE' and observed.get('code') == 'CONNECTION_UNAVAILABLE'
                   and isinstance(observed.get('request_id'), str) and bool(observed['request_id'])
                   and ((observed.get('phase') == 'https_request' and observed.get('network_error') in ('ECONNREFUSED', 'ECONNRESET'))
                        or (observed.get('phase') == 'response_body' and observed.get('network_error') == 'ECONNRESET')))
    outcome = ('available' if available else 'empty' if business.authenticated(observed)
               else 'rejected' if observed.get('http_status') in (401, 403)
               else 'unavailable' if unavailable else 'unknown')
    return {'schema': 'argus.fleet-observation.v1', 'run_id': run_id, 'instance_id': item['name'],
            'phase': phase, 'sequence': number, 'started_at_ms': started, 'completed_at_ms': now(),
            'latency_ms': now() - started, 'outcome': outcome, 'http_status': observed.get('http_status'),
            'request_id': observed.get('request_id'), 'probe_phase': observed.get('phase'),
            'network_error': observed.get('network_error') if unavailable else None,
            'fact_sha256': fact['fact_sha256'], 'response_sha256': observed.get('body_sha256')}


def sample(c, items, keys, facts, phase, output, count, started_event=None, fault_state=None):
    rows = []
    with measurement_log(output) as stream, ThreadPoolExecutor(max_workers=len(items)) as pool:
        number, post_rounds = 0, 0
        while number < count or (fault_state is not None and post_rounds < count):
            if fault_state is not None and fault_state.get('aborted'): break
            pending = [pool.submit(observe, i, keys[i['name']], facts[i['name']], c['run_id'], phase, number) for i in items]
            if started_event is not None: started_event.set()
            batch = []
            for future in pending:
                row = future.result(); rows.append(row); batch.append(row); write_measurement(stream, row)
            if fault_state is not None and fault_state.get('completed_at_ms') is not None:
                cutoff = fault_state['completed_at_ms'] + c['settle_seconds'] * 1000
                if all(r['started_at_ms'] >= cutoff for r in batch): post_rounds += 1
            number += 1
            if number < count or (fault_state is not None and post_rounds < count):
                time.sleep(c['interval_seconds'])
    atomic(str(output) + '.complete.json', {'run_id': c['run_id'], 'phase': phase, 'complete': True,
                                           'sha256': sha(output), 'rows': len(rows)})
    return rows


def stop_client(item):
    c = item['deployment']
    inspection = json.loads(business.deploy.run(['docker', 'inspect', c['container_name']]))
    require(len(inspection) == 1, 'ambiguous fault container')
    info = inspection[0]
    business.fleet.check_mounts(info, c)
    require(info['Config'].get('Labels', {}).get(business.deploy.INSTANCE_LABEL) == item['name']
            and info['Image'] == c['image_config_digest'] and info['State']['Running'], 'fault target binding differs')
    cid = info['Id']
    started = now()
    business.deploy.run(['docker', 'stop', '--time', '2', cid])
    current = json.loads(business.deploy.run(['docker', 'inspect', cid]))[0]
    require(not current['State']['Running'], 'selected Gateway did not stop')
    return {'event': 'client-stop', 'instance_id': item['name'], 'container_id': cid,
            'started_at_ms': started, 'completed_at_ms': now(), 'verified': True,
            'scope': 'only selected Gateway; shared SPIRE Agent unchanged'}


def inject(c, items, output):
    if c['event'] == 'client-stop':
        return stop_client(next(i for i in items if i['name'] == c['instance']))
    from fault_trial import remote, fault_command
    server = dict(c['_server'], diagnostic_dir=str(output))
    started = now()
    remote(server, fault_command(server), diagnostic=output / 'fault-diagnostic.json')
    remote(server, ['cat', '--', server['fault_file']], output=output / 'server-fault.jsonl')
    receipt = shared_receipt(c, output / 'server-fault.jsonl')
    return {'event': 'shared-service-fault', 'started_at_ms': started, 'completed_at_ms': now(),
            'verified': True, 'server_evidence_sha256': sha(output / 'server-fault.jsonl'),
            'container_id': receipt['target']['container_id'],
            'scope': 'shared service; protected runtime fault receipt'}


def shared_receipt(c, path):
    from timeline import acceptance
    # Unlike diagnostic checkpoint recovery, injection confirmation requires the
    # last complete final receipt. A truncated tail is an unknown operation.
    raw = path.read_bytes()
    require(raw.endswith(b'\n'), 'server fault receipt has an incomplete tail')
    receipt = acceptance.fault_checkpoint(path)
    event = c['_server']['event']
    require(receipt.get('run_id') == c['run_id'] and receipt.get('event') == event, 'server fault receipt association differs')
    require(receipt.get('executed') is True and receipt.get('target', {}).get('container_id')
            and type(receipt.get('started_at_ms')) is int and type(receipt.get('completed_at_ms')) is int
            and receipt['completed_at_ms'] >= receipt['started_at_ms']
            and receipt.get('recovery_hold', {}).get('verified') is True, 'latest server fault receipt is incomplete')
    if event in ('config-change', 'same-container-restart'):
        require(receipt.get('phase') == 'mutation_observed', 'fixture mutation was not observed')
    else:
        require(receipt.get('exit_code') == 0, 'fault process did not succeed')
    return receipt


def question(item, key, fact, run_id, directory):
    directory.mkdir(parents=True, exist_ok=True)
    state = directory / 'question.json'
    if state.exists():
        return read(state)  # Unknown submissions are never replayed.
    session = 'argus-recovery-' + uuid.uuid4().hex
    prompt = f"项目 {fact['marker']} 的校验码是什么？只回答校验码；没有记忆则回答 UNKNOWN。"
    require(fact['fact'] not in prompt, 'answer leaked into query')
    value = {'result': 'UNKNOWN', 'run_id': run_id, 'instance_id': item['name'], 'session_key': session,
             'phase': 'submission_unknown', 'query_sha256': hashlib.sha256(prompt.encode()).hexdigest()}
    atomic(state, value)
    c = item['deployment']; started = time.time()
    command = ['docker', 'exec', '-u', f"{c['gateway_uid']}:{c['gateway_gid']}", '-e',
               'OPENCLAW_CONFIG_PATH=/home/node/.openclaw/openclaw.json', c['container_name'], 'openclaw', 'agent',
               '--agent', item['agent_id'], '--session-key', session, '--message', prompt, '--timeout', '180', '--json']
    try:
        response = subprocess.run(command, env=business.environment(item, key), capture_output=True, text=True, timeout=210)
        data = json.loads(response.stdout)
        require(response.returncode == 0 and data.get('status') == 'ok' and data.get('runId') and not data.get('error'), 'Agent answer incomplete')
        actual = '\n'.join(v['text'] for v in data['result']['payloads'] if isinstance(v.get('text'), str)).strip()
        value.update(phase='answer_observed', answer_sha256=hashlib.sha256(actual.encode()).hexdigest(),
                     result='FAIL' if actual != fact['fact'] else 'UNKNOWN')
        logs = subprocess.run(['docker', 'logs', '--since', str(started), c['container_name']], capture_output=True, text=True, timeout=20)
        require(logs.returncode == 0, 'Gateway audit unavailable')
        receipt = find_recall((logs.stdout + '\n' + logs.stderr).splitlines(), session, fact['fact'],
                              business.deploy.client_id(c), c.get('server_spiffe_id', business.deploy.SERVER))
        value.update(phase='completed', result='PASS' if actual == fact['fact'] else 'FAIL',
                     answer_sha256=hashlib.sha256(actual.encode()).hexdigest(), recall=receipt,
                     latency_ms=int((time.time() - started) * 1000))
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        value['reason'] = 'ANSWER_OR_INJECTION_EVIDENCE_INCOMPLETE'
    atomic(state, value)
    return value


def summarize(c, directory, state):
    counts, coverage = {}, {}
    expected = set(state.get('instances', []))
    for phase in ('baseline', 'fault', 'recovery'):
        path = directory / (phase + '.jsonl')
        if not path.exists(): continue
        try:
            seal = read(str(path) + '.complete.json')
            require(seal['complete'] is True and seal['sha256'] == sha(path) and seal['run_id'] == c['run_id']
                    and seal.get('phase') == phase, 'incomplete sample')
            rows = [json.loads(line) for line in path.read_text().splitlines() if line]
            require(rows and all(r['run_id'] == c['run_id'] and r['phase'] == phase for r in rows), 'mixed sample')
            require(expected and {r['instance_id'] for r in rows} == expected
                    and len({(r['instance_id'], r['sequence']) for r in rows}) == len(rows)
                    and len(rows) == seal['rows'], 'missing instance, duplicate or incomplete sample')
            require(all(r.get('outcome') in ('available','empty','rejected','unavailable','unknown')
                        and type(r.get('sequence')) is int and r['sequence'] >= 0
                        and type(r.get('started_at_ms')) is int and type(r.get('completed_at_ms')) is int
                        and r['completed_at_ms'] >= r['started_at_ms'] for r in rows), 'invalid probe result')
            sequences = {name: sorted(r['sequence'] for r in rows if r['instance_id'] == name) for name in expected}
            require(all(sequence == list(range(len(sequence))) for sequence in sequences.values())
                    and len({tuple(sequence) for sequence in sequences.values()}) == 1, 'missing per-instance probe round')
            coverage[phase] = 'COMPLETE'
            if phase == 'fault' and state.get('fault'):
                cutoff = state['fault']['completed_at_ms'] + c['settle_seconds'] * 1000
                rows = [r for r in rows if r['started_at_ms'] >= cutoff]
                if {r['instance_id'] for r in rows} != expected: coverage[phase] = 'UNKNOWN'
            counts[phase] = {name: {v: sum(r['instance_id'] == name and r['outcome'] == v for r in rows)
                                    for v in ('available', 'empty', 'rejected', 'unavailable', 'unknown')}
                             for name in sorted({r['instance_id'] for r in rows})}
            if any(sum(v.values()) < 3 for v in counts[phase].values()): coverage[phase] = 'UNKNOWN'
        except (ValueError, OSError, KeyError, TypeError): coverage[phase] = 'UNKNOWN'
    fault_verified = state.get('fault', {}).get('verified') is True
    if c['event'] == 'shared-service-fault' and fault_verified:
        try:
            receipt = directory / 'server-fault.jsonl'
            require(sha(receipt) == state['fault'].get('server_evidence_sha256'), 'saved fault receipt checksum differs')
            shared_receipt(c, receipt)
        except (ValueError, OSError, KeyError, TypeError):
            fault_verified = False
            coverage['fault'] = 'UNKNOWN'
    verdict = 'UNKNOWN'
    fault_impact = 'UNKNOWN'
    if coverage.get('baseline') == coverage.get('fault') == 'COMPLETE' and fault_verified:
        if any(v['available'] < 3 or sum(v[x] for x in ('empty', 'rejected', 'unavailable', 'unknown')) for v in counts['baseline'].values()):
            coverage['baseline'] = 'UNKNOWN'
    if coverage.get('baseline') == coverage.get('fault') == 'COMPLETE' and fault_verified:
        unaffected = [n for n in counts['fault'] if c['event'] == 'client-stop' and n != c['instance']]
        verdict = 'PASS' if unaffected and all(counts['fault'][n]['available'] >= 3 and
                    sum(counts['fault'][n][v] for v in ('empty', 'rejected', 'unavailable', 'unknown')) == 0 for n in unaffected) else 'UNKNOWN'
        if any(counts['fault'][n]['empty'] or counts['fault'][n]['rejected'] or counts['fault'][n]['unavailable'] for n in unaffected): verdict = 'FAIL'
        if c['event'] == 'client-stop' and counts['fault'].get(c['instance'], {}).get('available', 0): verdict = 'FAIL'
        if c['event'] == 'shared-service-fault':
            fault_impact = ('AVAILABILITY_PERSISTS' if any(v['available'] for v in counts['fault'].values()) else
                            'UNKNOWN' if any(v['unknown'] for v in counts['fault'].values()) else 'OBSERVED_UNAVAILABLE')
            # HTTP/transport observations describe availability only. They never
            # establish a delivery/security verdict for the shared service.
    recovery = 'NOT_RUN'
    if state.get('questions'):
        values = [v.get('result') for v in state['questions'].values()]
        recovery = 'PASS' if set(state['questions']) == expected and all(v == 'PASS' for v in values) else 'FAIL' if 'FAIL' in values else 'UNKNOWN'
        if coverage.get('recovery') != 'COMPLETE' or any(v['available'] < 3 or sum(v[x] for x in ('empty','rejected','unavailable','unknown')) for v in counts.get('recovery', {}).values()):
            recovery = 'UNKNOWN' if recovery != 'FAIL' else recovery
        if 'FAIL' in values: verdict = 'FAIL'
        elif recovery == 'UNKNOWN' and verdict != 'FAIL': verdict = 'UNKNOWN'
        elif c['event'] == 'shared-service-fault' and fault_impact == 'OBSERVED_UNAVAILABLE' and recovery == 'PASS': verdict = 'PASS'
    result = {'schema': 'argus.fleet-fault-result.v1', 'run_id': c['run_id'], 'result': verdict,
              'phase': state['phase'], 'counts': counts, 'coverage': coverage,
              'task_recovery': state.get('questions', {}), 'recovery_result': recovery, 'fault_availability': fault_impact,
              'delivery_compliance': 'NOT_RUN', 'independent_readmission': 'NOT_RUN',
              'fault_verified': fault_verified,
              'boundary': 'Gateway private-memory availability and task recovery; observed connection failure and transport unknown do not establish zero delivery'}
    atomic(directory / 'result.json', result)
    return result


def run(config_file, output, action='run', execute_fault=False):
    c, items, keys, facts, fingerprint = settings(config_file)
    directory = Path(output).resolve()
    with lock(directory):
        state_file = directory / 'state.json'
        if action == 'run':
            require(execute_fault, 'run requires --execute-fault for this configured experiment')
            require(not state_file.exists(), 'existing trial: collect or observe-recovery, never repeat fault')
            state = {'run_id': c['run_id'], 'config_digest': fingerprint, 'phase': 'baseline',
                     'instances': [i['name'] for i in items]}
            atomic(state_file, state)
            rows = sample(c, items, keys, facts, 'baseline', directory / 'baseline.jsonl', c['baseline_rounds'])
            if not all(r['outcome'] == 'available' for r in rows):
                state['phase'] = 'baseline_failed'; atomic(state_file, state)
                return summarize(c, directory, state)
            # Reads continue across the injection, including unaffected clients.
            ready = threading.Event()
            fault_state = {}
            with ThreadPoolExecutor(max_workers=1) as observer:
                future = observer.submit(sample, c, items, keys, facts, 'fault', directory / 'fault.jsonl', c['observation_rounds'], ready, fault_state)
                try:
                    require(ready.wait(timeout=10), 'continuous observer did not start')
                    state['phase'] = 'fault_submission_unknown'; atomic(state_file, state)
                    state['fault'] = inject(c, items, directory)
                    fault_state['completed_at_ms'] = state['fault']['completed_at_ms']
                except BaseException:
                    fault_state['aborted'] = True
                    raise
                state['phase'] = 'observing'; atomic(state_file, state)
                future.result()
            state['phase'] = 'awaiting_explicit_recovery'; atomic(state_file, state)
        else:
            state = read(state_file)
            require(state['config_digest'] == fingerprint, 'trial configuration or business fixtures changed')
            if action == 'observe-recovery':
                require(state['phase'] in ('awaiting_explicit_recovery', 'recovery_observing', 'recovery_questions', 'completed'), 'fault is unknown or unfinished; collect only')
                if state['phase'] == 'awaiting_explicit_recovery':
                    state['phase'] = 'recovery_observing'; atomic(state_file, state)
                    sample(c, items, keys, facts, 'recovery', directory / 'recovery.jsonl', c['recovery_rounds'])
                if state['phase'] == 'recovery_observing':
                    seal = read(str(directory / 'recovery.jsonl') + '.complete.json')
                    require(seal['sha256'] == sha(directory / 'recovery.jsonl') and seal['complete'], 'interrupted recovery reads; collect evidence without replay')
                    state['phase'] = 'recovery_questions'; atomic(state_file, state)
                if state['phase'] == 'recovery_questions':
                    state.setdefault('questions', {})
                    for item in items:
                        name = item['name']
                        state['questions'][name] = question(item, keys[name], facts[name], c['run_id'], directory / 'questions' / name)
                        atomic(state_file, state)
                    state['phase'] = 'completed'; atomic(state_file, state)
        return summarize(c, directory, state)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('run', 'collect', 'observe-recovery'))
    p.add_argument('--config', required=True); p.add_argument('--output', required=True)
    p.add_argument('--execute-fault', action='store_true')
    args = p.parse_args()
    result = run(args.config, args.output, args.action, args.execute_fault)
    print(json.dumps(result, indent=2))
    return 0 if result['result'] == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
