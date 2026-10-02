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
from lifecycle_evidence import rotation, resumed_launch, quote_delta, helper_events, reestablished

ROOT = Path(__file__).resolve().parents[2]
NODE_SCRIPT = ROOT / 'core/spire/workload/scripts/node_attestation_observe.py'
CASES = ('node-enrollment', 'agent-renewal', 'workload-rotation', 'resume-launch',
         'workload-resubscribe', 'replacement-launch')
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
    if config.get('provider_socket'):
        commands.append(('provider', [sys.executable, str(Path(__file__).with_name('lifecycle_evidence.py')),
                                      'quote-snapshot', '--provider-socket', config['provider_socket'], '--run-id', config['run_id']]))
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
              'workload_quote_source': 'provider_generation_counters' if config.get('provider_socket') else 'UNAVAILABLE'}
    if config.get('provider_agent_id'):
        result['provider_agent_id'] = config['provider_agent_id']
    atomic(directory/'observation.json', result)
    if config.get('workload_config'):
        result['helper_journal_anchor'] = capture_helper_journal(config, directory, 'anchor')
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
    if config.get('workload_config'):
        result['helper_journal'] = capture_helper_journal(config, directory, 'window')
    result['completed_at_ms'] = now_ms()
    result['complete'] = True
    atomic(directory/'observation.json', result)
    return result


def capture_helper_journal(config, directory, phase):
    output = directory / ('helper-journal-' + phase + '.json')
    argv = [sys.executable, str(Path(__file__).with_name('lifecycle_evidence.py')), 'journal-' + phase,
            '--config', config['workload_config'], '--output', str(output)]
    if phase == 'window':
        argv += ['--anchor', str(directory / 'helper-journal-anchor.json')]
    result = {'result': 'UNKNOWN', 'source': output.name}
    try:
        completed = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45, check=False)
        if completed.returncode == 0 and output.is_file():
            read(output)
            result.update(result='OBSERVED', sha256=sha(output))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        result['error_class'] = type(error).__name__
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


def imported_quote_pair(before_file, after_file, observation, node, workload, clock_uncertainty_ms):
    """Associate host-local Provider snapshots with the other host's trial.

    The wider counter window must cover both trial snapshots, allowing for the
    measured inter-host uncertainty. Counts cover that window, not one request.
    """
    result = {'result': 'UNKNOWN', 'node': None, 'workload': None, 'source': 'imported_provider_snapshots'}
    try:
        require(before_file and after_file, 'both external Provider snapshots are required')
        before, after = read(before_file), read(after_file)
        require(all(s.get('run_id') == observation['run_id'] for s in (before, after)), 'Provider run ID differs')
        expected = observation.get('provider_agent_id')
        if node:
            require(node[0].get('agent_id') == node[1].get('agent_id'), 'Node Agent changed across snapshots')
            if expected: require(expected == node[0].get('agent_id'), 'configured Provider Agent differs from Node trial')
            expected = node[0].get('agent_id')
        if workload:
            agents = [s.get('target', {}).get('agent_id') for s in workload]
            if all(agents):
                require(agents[0] == agents[1] and (not expected or expected == agents[0]), 'workload Provider Agent differs')
                expected = agents[0]
        require(expected and all(s.get('agent_id') == expected for s in (before, after)), 'Provider Agent association missing or different')
        left = before.get('observation_completed_at_ms')
        right = after.get('observation_started_at_ms')
        require(type(left) is int and type(right) is int and
                left <= observation['started_at_ms'] - clock_uncertainty_ms and
                right >= observation['completed_at_ms'] + clock_uncertainty_ms,
                'Provider window does not cover the trial and measured clock uncertainty')
        result.update(quote_delta(before, after), source='imported_provider_snapshots',
                      before_sha256=sha(before_file), after_sha256=sha(after_file), run_id=observation['run_id'],
                      counter_window_started_at_ms=left, counter_window_ended_at_ms=right,
                      clock_uncertainty_ms=clock_uncertainty_ms)
    except (OSError, ValueError, KeyError, TypeError) as error:
        result.update(reason=str(error) if isinstance(error, ValueError) else type(error).__name__)
    return result


def recovery_access(readiness_file, load_result, observation, workload, uncertainty):
    """Join existing readiness timing to actual memory access on that SVID."""
    result = {'result': 'UNKNOWN', 'scope': 'observed readiness to first sampled nonempty memory response on the new SVID; not Agent task correctness'}
    try:
        require(workload and load_result, 'workload snapshots and memory request trace required')
        ready, receipt = read(readiness_file), read(load_result)
        after = workload[1]
        require(ready.get('schema') == 'argus.command-readiness.v1' and ready.get('result') == 'OBSERVED'
                and ready.get('run_id') == observation['run_id'] == receipt.get('run_id')
                and ready.get('target_id') == observation['target_id']
                and ready.get('config_sha256') == after['config_sha256']
                and ready.get('helper_invocation_id') == after['status'].get('helper_invocation_id')
                and ready.get('target_serial') == after['status'].get('target_serial'), 'readiness receipt belongs to another target/generation')
        stamp = ready.get('ready_observed_at_ms')
        require(type(stamp) is int and workload[0]['completed_at_ms'] <= stamp <= after['started_at_ms'], 'readiness is outside the lifecycle window')
        trace = Path(load_result).parent / 'requests.jsonl'
        require(receipt.get('measurement_complete') is True and receipt.get('workload_kind') == 'memory_query'
                and receipt.get('requests_sha256') == sha(trace), 'memory trace incomplete or changed')
        rows = [json.loads(line) for line in trace.read_text().splitlines() if line.strip()]
        require(rows and all(r.get('run_id') == observation['run_id'] for r in rows), 'memory run association differs')
        selected = [r for r in rows if r.get('phase') == 'measurement' and r.get('outcome') == 'success'
                    and r.get('memory_result') == 'nonempty' and r.get('peer_identity_verified') is True
                    and r.get('peer_spiffe_id') == observation['target_id'] and r.get('peer_svid_serial') == ready['target_serial']
                    and type(r.get('started_at_ms')) is int and type(r.get('completed_at_ms')) is int
                    and r['started_at_ms'] - uncertainty >= stamp and r['completed_at_ms'] >= r['started_at_ms']]
        require(selected, 'no successful post-readiness memory response bound to the new SVID')
        first = min(selected, key=lambda r: r['completed_at_ms'])
        delta = first['completed_at_ms'] - stamp
        result.update(result='OBSERVED', request_id=first['request_id'], ready_observed_at_ms=stamp,
                      first_response_completed_at_ms=first['completed_at_ms'], target_serial=ready['target_serial'],
                      readiness_to_response_ms={'lower_ms': max(0, delta-uncertainty), 'upper_ms': delta+uncertainty},
                      source_sha256={'readiness': sha(readiness_file), 'load': sha(load_result), 'requests': sha(trace)})
    except (OSError, ValueError, KeyError, TypeError) as error:
        result['reason'] = str(error) if isinstance(error, ValueError) else type(error).__name__
    return result


def collect(directory, load_result=None, resume_result=None, creates=None, clock_uncertainty_ms=0, max_probe_gap_ms=2000,
            provider_before=None, provider_after=None, admission_observation=None, readiness_result=None):
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
              'workload_quote_samples':{'result':'UNKNOWN','count':None,'reason':'received Workload Quote samples are not captured here; real generation counts are reported separately in quote_generated'},
              'quote_generated':{'result':'NOT_RUN','node':None,'workload':None},
              'helper_events':{'result':'UNKNOWN', 'subscription_starts':None, 'svid_publications':None},
              'recovery_access':{'result':'NOT_RUN'},
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
        provider = snapshot_pair(directory, observation, 'provider')
        if provider_before or provider_after:
            if provider:
                result['quote_generated'] = {'result':'UNKNOWN', 'node':None, 'workload':None,
                    'reason':'both local and imported Provider snapshots supplied; select one source'}
            else:
                result['quote_generated'] = imported_quote_pair(provider_before, provider_after, observation, node, workload, clock_uncertainty_ms)
        elif provider:
            result['quote_generated'] = quote_delta(*provider)
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
            journal = observation.get('helper_journal', {})
            if journal.get('result') == 'OBSERVED':
                require(journal.get('source') == 'helper-journal-window.json', 'journal source differs')
                path = directory / journal['source']
                require(sha(path) == journal.get('sha256'), 'journal checksum differs')
                result['helper_events'] = helper_events(read(path), *workload)
                result['helper_events']['source_sha256'] = sha(path)
            if observation['case'] == 'resume-launch':
                if resume_result:
                    logs = [json.loads(line) for line in Path(creates).read_text().splitlines() if line.strip()] if creates else None
                    result['workload'] = resumed_launch(*workload, read(resume_result), logs)
                    result['resume_result_sha256'] = sha(resume_result)
                    if creates: result['creates_sha256'] = sha(creates)
                else:
                    result['workload'] = {'result':'NOT_RUN','reason':'operator resume-launch result was not supplied; no mutation executed'}
            elif observation['case'] in ('workload-resubscribe', 'replacement-launch'):
                admission = read(admission_observation) if admission_observation else None
                if admission:
                    require(admission.get('run_id') == observation['run_id'], 'admission observation run differs')
                    result['admission_observation_sha256'] = sha(admission_observation)
                result['workload'] = reestablished(*workload, observation['case'], result['helper_events'], admission)
            else:
                result['workload'] = rotation(*workload)
            old, new = workload[0].get('credential', {}), workload[1].get('credential', {})
            result['workload']['observed_serial_transitions_min'] = int(old['serial'] != new['serial']) if old.get('serial') and new.get('serial') else None
        primary = result['node'] if observation['case'] in ('node-enrollment','agent-renewal') else result['workload']
        result['result'] = primary['result']
        result['business_continuity'] = business_continuity(load_result, observation, clock_uncertainty_ms, max_probe_gap_ms)
        if observation['case'] in ('workload-resubscribe', 'replacement-launch') and result['result'] == 'PASS':
            counts = result['quote_generated']
            native = workload[1].get('runtime_variant') == 'native_spire_guarded'
            generated = counts.get('workload', {}).get('generated') if counts.get('workload') else None
            result['new_workload_evidence'] = ('NOT_APPLICABLE_NATIVE' if native else 'OBSERVED') if counts.get('result') == 'OBSERVED' and (
                generated == 0 if native else type(generated) is int and generated > 0) else 'UNKNOWN'
            if result['new_workload_evidence'] == 'UNKNOWN':
                result.update(result='UNKNOWN', reason='new subscription is observed, but a new generated Workload Quote is not established')
        if readiness_result:
            result['recovery_access'] = recovery_access(readiness_result, load_result, observation, workload, clock_uncertainty_ms)
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
    p.add_argument('--provider-before'); p.add_argument('--provider-after')
    p.add_argument('--admission-observation', help='same-run E1 production observation of the newly admitted target')
    p.add_argument('--readiness-result', help='existing time-ready-command receipt; joined to the first subsequent legal memory response')
    args = parser.parse_args()
    if args.action == 'observe':
        value = observe(args.config, args.output)
    else:
        value = collect(args.observation, args.load_result, args.resume_result, args.creates, args.clock_uncertainty_ms, args.max_probe_gap_ms,
                        args.provider_before, args.provider_after, args.admission_observation, args.readiness_result)
        atomic(args.output, value)
    print(json.dumps(value, indent=2))
    return 0 if args.action == 'observe' or value['result'] == 'PASS' else 1 if value['result'] == 'FAIL' else 2


if __name__ == '__main__': sys.exit(main())
