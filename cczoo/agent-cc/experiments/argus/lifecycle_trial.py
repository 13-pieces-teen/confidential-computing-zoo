#!/usr/bin/env python3
"""Observe E3 lifecycle changes and collect continuous memory-API evidence.

Only invokes the existing read-only snapshot tools. Renewal, enrollment and
resume-launch remain explicit operator actions; no identity or launch replay.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit

from common import atomic, digest, read, require, sha
from lifecycle_evidence import rotation, resumed_launch

ROOT = Path(__file__).resolve().parents[2]
NODE_SCRIPT = ROOT / 'core/spire/workload/scripts/node_attestation_observe.py'
CASES = ('node-enrollment', 'agent-renewal', 'workload-rotation', 'resume-launch')
NODE_FIELDS = ('spire_server', 'server_socket', 'agent_id', 'metrics_url', 'attempt_metric',
               'quote_count_metric', 'process_start_metric')


def node_observer():
    # Ordinary sibling import of the repository's existing read-only tool.
    scripts = str(NODE_SCRIPT.parent)
    if scripts not in sys.path: sys.path.insert(0, scripts)
    import node_attestation_observe
    return node_attestation_observe


def now_ms():
    return time.time_ns() // 1_000_000


def capture_phase(config, directory, phase):
    captured = {}
    commands = []
    if config.get('node'):
        node = config['node']
        require(set(node) == set(NODE_FIELDS) and all(isinstance(node[k], str) and node[k] for k in NODE_FIELDS),
                'node settings must identify the existing public-record and dedicated metric sources')
        parsed = urlsplit(node['metrics_url'])
        require(parsed.scheme in ('http','https') and parsed.hostname and not parsed.username and not parsed.password
                and not parsed.query and not parsed.fragment, 'metrics URL must not contain embedded credentials or query secrets')
        commands.append(('node', [sys.executable, str(NODE_SCRIPT), 'snapshot'] +
                         [part for name in NODE_FIELDS for part in ('--'+name.replace('_', '-'), node[name])]))
    if config.get('workload_config'):
        commands.append(('workload', [sys.executable, str(Path(__file__).with_name('lifecycle_evidence.py')),
                                     'snapshot', '--config', config['workload_config']]))
    for kind, argv in commands:
        output = directory / (kind+'-'+phase+'.json')
        item = {'result': 'UNKNOWN', 'started_at_ms': now_ms(), 'source': output.name}
        try:
            completed = subprocess.run(argv + ['--output', str(output)], stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, timeout=45, check=False)
            item['exit_code'] = completed.returncode
            if completed.returncode == 0 and output.is_file():
                read(output)
                item.update(result='OBSERVED', sha256=sha(output))
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            item['error_class'] = type(error).__name__
        item['completed_at_ms'] = now_ms()
        captured[kind] = item
    return captured


def observe(config_file, output):
    config, directory = read(config_file), Path(output)
    require(config.get('case') in CASES, 'select an E3 lifecycle case')
    require(isinstance(config.get('run_id'), str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,159}', config['run_id']), 'invalid run ID')
    require(isinstance(config.get('target_id'), str) and config['target_id'].startswith('spiffe://'), 'target SPIFFE identity required')
    duration = config.get('duration_seconds', 120)
    require(type(duration) in (int, float) and 1 <= duration <= 86400, 'observation duration must be in [1,86400] seconds')
    require(config.get('node') if config['case'] in ('node-enrollment', 'agent-renewal') else config.get('workload_config'),
            'selected case needs its actual Node or workload snapshot source')
    require(not directory.exists(), 'fresh lifecycle observation directory required')
    directory.mkdir(parents=True, mode=0o700)
    result = {'schema': 'argus.lifecycle-trial.v1', 'run_id': config['run_id'], 'case': config['case'],
              'target_id': config['target_id'], 'config_sha256': digest(config), 'complete': False,
              'started_at_ms': now_ms(), 'duration_seconds': duration, 'automatic_mutations': False,
              'workload_quote_source': 'UNAVAILABLE'}
    atomic(directory/'observation.json', result)
    result['before'] = capture_phase(config, directory, 'before')
    result['observation_started_at_ms'] = now_ms()
    atomic(directory/'observation.json', result)
    print(json.dumps({'status':'E3_OBSERVER_READY', 'run_id':config['run_id'], 'case':config['case'],
                      'automatic_mutations':False}), flush=True)
    until = time.monotonic() + duration
    while time.monotonic() < until:
        time.sleep(min(.5, max(0, until-time.monotonic())))
    result['observation_ended_at_ms'] = now_ms()
    result['after'] = capture_phase(config, directory, 'after')
    result['completed_at_ms'] = now_ms()
    result['complete'] = True
    atomic(directory/'observation.json', result)
    return result


def snapshot_pair(directory, observation, kind):
    values = []
    for phase in ('before', 'after'):
        item = observation.get(phase, {}).get(kind, {})
        if item.get('result') != 'OBSERVED': return None
        require(item.get('source') == kind+'-'+phase+'.json', 'snapshot source differs from observer output')
        path = directory / item['source']
        require(sha(path) == item.get('sha256'), 'snapshot checksum differs')
        values.append(read(path))
    return values


def business_continuity(load_result, observation, clock_uncertainty_ms, max_probe_gap_ms):
    """Summarize finite-cadence API observations, not unobserved uptime or Agent answers."""
    missing = {'result':'NOT_RUN', 'coverage':'NOT_RUN', 'scope':'sampled nonempty private-memory API responses; not complete Agent task correctness'}
    if load_result is None: return missing
    result = missing | {'result':'UNKNOWN', 'coverage':'UNKNOWN'}
    try:
        path = Path(load_result)
        receipt = read(path)
        trace = path.parent/'requests.jsonl'
        require(receipt.get('run_id') == observation['run_id'] and receipt.get('measurement_complete') is True,
                'continuous probe did not complete for this run')
        require(receipt.get('requests_sha256') == sha(trace), 'continuous probe checksum differs')
        require(receipt.get('workload_kind') == 'memory_query', 'status API does not establish memory business continuity')
        targets = receipt.get('server_ids', [receipt.get('server_id')])
        require(targets == [observation['target_id']], 'probe was configured for another service')
        rows = [json.loads(line) for line in trace.read_text(encoding='utf-8').splitlines() if line.strip()]
        require(rows and all(r.get('run_id') == observation['run_id'] for r in rows), 'probe rows belong to another run')
        require(len({r.get('request_id') for r in rows}) == len(rows), 'duplicate probe request IDs')
        require(all(r.get('outcome') in ('success', 'rejected', 'timeout', 'unknown', 'overload') for r in rows), 'unrecognized probe outcome')
        require(all(r.get('peer_identity_verified') is True and r.get('peer_spiffe_id') == observation['target_id']
                    for r in rows if r.get('http_status') is not None), 'probe target differs from the observed service')
        start, end = observation['started_at_ms'], observation['completed_at_ms']
        left, right = start-clock_uncertainty_ms, end+clock_uncertainty_ms
        time_coverage = receipt.get('measurement_started_at_ms', float('inf')) <= left and receipt.get('measurement_ended_at_ms', 0) >= right
        groups = {}
        for row in rows:
            if row.get('phase') != 'measurement': continue
            require(type(row.get('started_at_ms')) is int, 'probe timestamp unavailable')
            groups.setdefault(row.get('instance_id', 'client'), []).append(row)
        require(groups, 'no measured continuous probes')
        if receipt.get('schema') == 'argus.load-fleet.v1':
            require(len(groups) == receipt.get('clients'), 'missing client probe stream')
        clients, total_failed = [], 0
        for instance, samples in sorted(groups.items()):
            samples.sort(key=lambda r:r['started_at_ms'])
            stamps = [r['started_at_ms'] for r in samples]
            inside = [r for r in samples if left <= r['started_at_ms'] <= right]
            before = [stamp for stamp in stamps if stamp <= left]
            after = [stamp for stamp in stamps if stamp >= right]
            points = ([max(before)] if before else [left]) + [r['started_at_ms'] for r in inside] + ([min(after)] if after else [right])
            maximum_gap = max((b-a for a,b in zip(points, points[1:])), default=right-left)
            coverage = time_coverage and bool(inside) and bool(before) and bool(after) and maximum_gap <= max_probe_gap_ms
            def succeeded(row): return row.get('outcome') == 'success' and row.get('memory_result') == 'nonempty'
            failures = [r for r in inside if not succeeded(r)]
            episodes, episode, previous_success = [], None, None
            for row in samples:
                if row['started_at_ms'] < left:
                    if succeeded(row): previous_success = row
                    continue
                if row['started_at_ms'] > right and episode is None: break
                if succeeded(row):
                    if episode:
                        episode['first_success_after_ms'] = row['started_at_ms']
                        episode['sampled_recovery_ms'] = row['started_at_ms'] - episode['first_failed_request_ms']
                        episodes.append(episode); episode = None
                    previous_success = row
                elif row['started_at_ms'] <= right:
                    if episode is None:
                        episode = {'first_failed_request_ms':row['started_at_ms'], 'last_success_before_ms':previous_success['started_at_ms'] if previous_success else None,
                                   'first_success_after_ms':None, 'sampled_recovery_ms':None, 'failed_queries':0}
                    episode['failed_queries'] += 1
            if episode: episodes.append(episode)
            total_failed += len(failures)
            counts = {name:sum(r.get('outcome') == name for r in inside) for name in ('success','rejected','timeout','unknown','overload')}
            clients.append({'instance_id':instance, 'coverage':'COMPLETE' if coverage else 'UNKNOWN', 'max_observed_probe_gap_ms':maximum_gap,
                            'queries':len(inside), 'outcomes':counts, 'nonempty_successes':sum(succeeded(r) for r in inside),
                            'failed_queries':len(failures), 'interruption_episodes':episodes})
        complete = all(c['coverage'] == 'COMPLETE' for c in clients)
        result.update(coverage='COMPLETE' if complete else 'UNKNOWN', result='FAIL' if total_failed else 'PASS' if complete else 'UNKNOWN',
                      clients=clients, failed_queries=total_failed, clock_uncertainty_ms=clock_uncertainty_ms,
                      max_probe_gap_ms=max_probe_gap_ms, window_started_at_ms=start, window_ended_at_ms=end,
                      source_sha256=sha(path), requests_sha256=sha(trace),
                      interpretation='PASS means no failed scheduled memory queries in covered samples, not zero interruption between probes')
    except (OSError, ValueError, KeyError, TypeError) as error:
        result['error_class'] = type(error).__name__
    return result


def collect(directory, load_result=None, resume_result=None, creates=None, clock_uncertainty_ms=0, max_probe_gap_ms=2000):
    require(type(clock_uncertainty_ms) is int and clock_uncertainty_ms >= 0, 'nonnegative clock uncertainty required')
    require(type(max_probe_gap_ms) is int and max_probe_gap_ms > 0, 'positive probe gap required')
    directory = Path(directory)
    observation = read(directory/'observation.json')
    require(observation.get('schema') == 'argus.lifecycle-trial.v1', 'invalid lifecycle observation')
    require(observation.get('case') in CASES, 'invalid observed lifecycle case')
    result = {'schema':'argus.lifecycle-trial-result.v1', 'run_id':observation['run_id'], 'case':observation['case'],
              'result':'UNKNOWN', 'scope':'primary lifecycle case; Quote and business evidence are reported separately',
              'observation_sha256':sha(directory/'observation.json'), 'automatic_mutations':False,
              'node':{'result':'NOT_RUN'}, 'workload':{'result':'NOT_RUN'},
              'node_quote_samples':{'result':'NOT_RUN','count':None},
              'workload_quote_samples':{'result':'UNKNOWN','count':None,'reason':'no dedicated Workload Quote counter captured; SVID changes do not establish Quote counts'},
              'business_continuity':{'result':'NOT_RUN','coverage':'NOT_RUN'}}
    if observation.get('complete') is not True:
        return result | {'reason':'observer was interrupted; existing snapshots are preserved and no action is replayed'}
    for kind in ('node', 'workload'):
        if any(kind in observation.get(phase, {}) for phase in ('before','after')):
            result[kind]['result'] = 'UNKNOWN'
            result[kind+'_quote_samples']['result'] = 'UNKNOWN'
    try:
        node = snapshot_pair(directory, observation, 'node')
        workload = snapshot_pair(directory, observation, 'workload')
        if node:
            mode = 'enrollment' if observation['case'] == 'node-enrollment' else 'renewal'
            result['node'] = node_observer().assess(*node, mode, clock_uncertainty_ms)
            old, new = node[0].get('agent', {}), node[1].get('agent', {})
            result['node']['observed_serial_transitions_min'] = int(old['serial'] != new['serial']) if old.get('serial') and new.get('serial') else None
            result['node']['can_reattest_observed'] = new.get('can_reattest')
            count = result['node'].get('node_quote_samples', result['node'].get('node_quote_samples_in_new_process'))
            result['node_quote_samples'] = {'result':'OBSERVED' if count is not None and count >= 0 else 'UNKNOWN',
                                            'count':count if count is not None and count >= 0 else None}
        if workload:
            require(all(s.get('target_id') == observation['target_id'] for s in workload), 'workload target differs from trial')
            if observation['case'] == 'resume-launch':
                if resume_result:
                    logs = [json.loads(line) for line in Path(creates).read_text().splitlines() if line.strip()] if creates else None
                    result['workload'] = resumed_launch(*workload, read(resume_result), logs)
                    result['resume_result_sha256'] = sha(resume_result)
                    if creates: result['creates_sha256'] = sha(creates)
                else:
                    result['workload'] = {'result':'NOT_RUN','reason':'operator resume-launch result was not supplied; no mutation executed'}
            else:
                result['workload'] = rotation(*workload)
            old, new = workload[0].get('credential', {}), workload[1].get('credential', {})
            result['workload']['observed_serial_transitions_min'] = int(old['serial'] != new['serial']) if old.get('serial') and new.get('serial') else None
        primary = result['node'] if observation['case'] in ('node-enrollment','agent-renewal') else result['workload']
        result['result'] = primary['result']
        result['business_continuity'] = business_continuity(load_result, observation, clock_uncertainty_ms, max_probe_gap_ms)
    except (OSError, ValueError, KeyError, TypeError) as error:
        result.update(result='UNKNOWN', error_class=type(error).__name__)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('observe'); p.add_argument('--config', required=True); p.add_argument('--output', required=True)
    p = sub.add_parser('collect'); p.add_argument('--observation', required=True); p.add_argument('--load-result')
    p.add_argument('--resume-result'); p.add_argument('--creates'); p.add_argument('--output', required=True)
    p.add_argument('--clock-uncertainty-ms', type=int, required=True); p.add_argument('--max-probe-gap-ms', type=int, default=2000)
    args = parser.parse_args()
    if args.action == 'observe':
        value = observe(args.config, args.output)
    else:
        value = collect(args.observation, args.load_result, args.resume_result, args.creates, args.clock_uncertainty_ms, args.max_probe_gap_ms)
        atomic(args.output, value)
    print(json.dumps(value, indent=2))
    return 0 if args.action == 'observe' or value['result'] == 'PASS' else 1 if value['result'] == 'FAIL' else 2


if __name__ == '__main__': sys.exit(main())
