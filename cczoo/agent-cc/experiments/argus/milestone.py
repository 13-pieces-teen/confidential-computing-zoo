#!/usr/bin/env python3
"""Derive fault gates from persisted real business observations, never a manual flag."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

from common import atomic, digest, read, require

CLIENT = Path(__file__).resolve().parents[2] / 'adapters/OpenClaw/spiffe_client'
sys.path.insert(0, str(CLIENT))
from verify_audit import find_recall, find_write


STAGES = ('archive', 'nonempty-extraction', 'injection', 'answer')


def _answer(value):
    require(value.get('status') == 'ok' and value.get('runId') and not value.get('error'), 'Gateway answer incomplete')
    payloads = value.get('result', {}).get('payloads')
    require(isinstance(payloads, list), 'Gateway answer payload missing')
    return '\n'.join(v['text'] for v in payloads if isinstance(v.get('text'), str)).strip()


def _evaluate(stage, contents, metadata):
    child = json.loads(contents['run.json'])
    identity = json.loads(contents['identity.json'])
    require(child['run_id'] == metadata['run_id'] + '-' + metadata['instance_id'], 'business belongs to another run/instance')
    states = []
    lines = contents['processing-events.jsonl'].splitlines()
    for index, line in enumerate(lines):
        try: value = json.loads(line)
        except ValueError:
            require(index == len(lines)-1, 'corrupt processing evidence'); continue
        if value.get('schema_version') == 1 and 'scope' in value: states.append(value)
    require(states, 'no processing state')
    state = states[-1]; scope = state['scope']
    require(scope['session_key'] == child['session_key'] and scope['agent_id'] == child['agent_id'], 'business session binding mismatch')
    require(all(scope[key] == identity[key] and scope[key] for key in ('client_spiffe_id', 'server_spiffe_id')), 'identity binding mismatch')
    require(state.get('plugin_readback') is True and state.get('session_id'), 'Gateway write not read back')
    archive = state.get('archive') is True and state.get('commit_count', 0) > 0
    extraction = state.get('extraction') or {}
    counts = extraction.get('by_category') or {}
    nonempty = (bool(counts) and all(type(v) is int and v >= 0 for v in counts.values())
                and type(extraction.get('total')) is int and sum(counts.values()) == extraction['total'] > 0)
    reached = archive if stage == 'archive' else nonempty
    # Write receipt is derived from actual Gateway logs, not just HTTP commit state.
    find_write(contents['gateway-write.log'].splitlines(), state['session_id'], identity['client_spiffe_id'], identity['server_spiffe_id'])
    if stage in ('injection', 'answer'):
        require(archive and nonempty, 'memory processing not complete')
        fact = contents['fact.txt'].strip()
        session_key = contents['recall-session-key.txt'].strip()
        require(session_key != child['session_key'] and session_key.startswith(child['session_key'] + '-recall-'), 'recall is not a new session')
        require(fact and fact not in contents['recall-prompt.txt'], 'expected answer leaked into question')
        find_recall(contents['gateway.log'].splitlines(), session_key, fact, identity['client_spiffe_id'], identity['server_spiffe_id'])
        reached = True
        if stage == 'answer': reached = _answer(json.loads(contents['recall-response.json'])) == fact
    return {'reached':bool(reached), 'session_id':state['session_id'], 'task_id':state.get('task_id'),
            'client_spiffe_id':identity['client_spiffe_id'], 'server_spiffe_id':identity['server_spiffe_id']}


def _projection(fleet, instance):
    require(instance in fleet.get('instances', {}), 'instance has not started')
    return {key:fleet.get(key) for key in ('run_id', 'config_sha256', 'scope')}


def derive(directory, instance, stage, run_id):
    require(stage in STAGES, 'unknown business milestone')
    require(__import__('re').fullmatch(r'[a-z][a-z0-9-]{0,22}', instance), 'invalid instance')
    directory = Path(directory).resolve()
    parent_file = directory / 'result.json'; fleet = read(parent_file)
    projection = _projection(fleet, instance)
    require(projection['run_id'] == run_id and projection['config_sha256'], 'fleet/run binding mismatch')
    metadata = {'schema':'argus.business-milestone.v1', 'run_id':run_id, 'instance_id':instance,
                'milestone':stage, 'fleet_file':str(parent_file), 'fleet_projection_sha256':digest(projection)}
    files = ['run.json', 'identity.json', 'processing-events.jsonl', 'gateway-write.log']
    if stage in ('injection', 'answer'):
        files += ['fact.txt', 'recall-session-key.txt', 'recall-prompt.txt', 'gateway.log']
    if stage == 'answer': files += ['recall-response.json']
    contents, sources = {}, []
    for name in files:
        path = directory / instance / name
        data = path.read_bytes()
        contents[name] = data.decode('utf-8')
        sources.append({'name':name, 'path':str(path), 'bytes':len(data), 'sha256':hashlib.sha256(data).hexdigest(),
                        'append_only':name.endswith(('.jsonl', '.log'))})
    value = _evaluate(stage, contents, metadata)
    return dict(metadata, **value, result='PASS' if value['reached'] else 'NOT_RUN', sources=sources,
                observed_at_ms=time.time_ns()//1000000,
                boundary='persisted Gateway/processing evidence; not a model-provider receipt')


def verify(record, run_id):
    require(record.get('schema') == 'argus.business-milestone.v1' and record.get('run_id') == run_id,
            'use a derived business milestone for this run')
    fleet_path = Path(record['fleet_file']).resolve()
    require(record.get('milestone') in STAGES and __import__('re').fullmatch(r'[a-z][a-z0-9-]{0,22}', record.get('instance_id', '')),
            'invalid milestone instance/stage')
    require(digest(_projection(read(fleet_path), record['instance_id'])) == record['fleet_projection_sha256'], 'fleet binding changed')
    contents = {}
    expected = {'run.json', 'identity.json', 'processing-events.jsonl', 'gateway-write.log'}
    if record['milestone'] in ('injection', 'answer'):
        expected |= {'fact.txt', 'recall-session-key.txt', 'recall-prompt.txt', 'gateway.log'}
    if record['milestone'] == 'answer': expected.add('recall-response.json')
    require({v.get('name') for v in record['sources']} == expected, 'milestone source set differs')
    for source in record['sources']:
        path = Path(source['path']).resolve()
        require(path == fleet_path.parent / record['instance_id'] / source['name'], 'milestone source escapes its instance')
        data = path.read_bytes()
        require(source['append_only'] is source['name'].endswith(('.jsonl', '.log')), 'invalid evidence snapshot mode')
        if source['append_only']:
            require(len(data) >= source['bytes'], 'business evidence truncated')
            data = data[:source['bytes']]
        require(len(data) == source['bytes'] and hashlib.sha256(data).hexdigest() == source['sha256'], 'business evidence changed')
        require(source['name'] not in contents, 'duplicate milestone source')
        contents[source['name']] = data.decode('utf-8')
    current = _evaluate(record['milestone'], contents, record)
    require(record.get('reached') is True and current['reached'] is True, 'business milestone not reached')
    for key in ('session_id', 'task_id', 'client_spiffe_id', 'server_spiffe_id'):
        require(record.get(key) == current[key], 'milestone association changed')
    return True


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--business-dir', required=True); p.add_argument('--instance', required=True)
    p.add_argument('--milestone', choices=STAGES, required=True); p.add_argument('--run-id', required=True)
    p.add_argument('--output', required=True); p.add_argument('--wait-seconds', type=float, default=0)
    a = p.parse_args(); require(0 <= a.wait_seconds <= 3600, 'invalid milestone observation budget')
    end = time.monotonic() + a.wait_seconds
    while True:
        try: result = derive(a.business_dir, a.instance, a.milestone, a.run_id)
        except (OSError, ValueError, KeyError):
            result = {'schema':'argus.business-milestone.v1', 'run_id':a.run_id, 'instance_id':a.instance,
                      'milestone':a.milestone, 'reached':False, 'result':'UNKNOWN', 'reason':'BUSINESS_EVIDENCE_NOT_READY'}
        atomic(a.output, result)
        if result['reached'] or time.monotonic() >= end: break
        time.sleep(.2)
    print(json.dumps(result, indent=2))
    return 0 if result['reached'] else 1


if __name__ == '__main__': raise SystemExit(main())
