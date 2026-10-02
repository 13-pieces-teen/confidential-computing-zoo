#!/usr/bin/env python3
"""Small E5 recipes over existing admission, control and memory-load tools.

Commands are explicitly frozen argv files. Every mutation is recorded before
submission and is never retried. Collection is read-only and keeps failed and
unexecuted points. Neither a measurement receipt nor HTTP success proves TDX.
"""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from common import atomic, digest, read, require, run_logged, sha

ROOT = Path(__file__).resolve().parent
GROUPS = ('full_argus', 'native_spire_guarded')


def now_ms():
    return time.time_ns() // 1_000_000


class UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__('localhost', timeout=5)
        self.path = str(path)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def chain_snapshot(socket_path):
    """Same protected read endpoint as Provider; this is NOT a Quote."""
    started = time.monotonic_ns()
    result = {'schema': 'argus.cost-chain.v1', 'started_at_ms': now_ms(), 'result': 'UNKNOWN'}
    connection = None
    try:
        path = Path(socket_path)
        require(path.is_absolute() and path.resolve() == path, 'absolute socket without symlink traversal required')
        info = path.lstat()
        require(stat.S_ISSOCK(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o077,
                'root-owned mode 0600 TruCon socket required')
        connection = UnixHTTP(path)
        connection.request('GET', '/chain-state?include_history=true',
                           headers={'X-TruCon-Caller-Service': 'argus_provider'})
        response = connection.getresponse()
        body = response.read(524289)
        require(len(body) <= 524288, 'chain response exceeds limit')
        result.update(http_status=response.status, response_bytes=len(body), reason_code=response.getheader('X-Argus-Reason'))
        payload = json.loads(body)
        if response.status == 200:
            refs = payload.get('log_ids')
            require(payload.get('chain_id') == 'default' and isinstance(refs, list)
                    and all(isinstance(x, str) for x in refs) and len(refs) == len(set(refs))
                    and payload.get('sequence_num') == len(refs) and refs
                    and payload.get('head_log_id') == refs[-1], 'invalid chain snapshot')
            result.update(result='OBSERVED', snapshot=payload, record_count=len(refs),
                          reference_bytes=len(json.dumps(refs, separators=(',', ':')).encode()))
        else:
            # 409 is not necessarily pending: it can be capacity or bad history.
            result.update(detail=payload.get('detail'), result='UNAVAILABLE')
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException) as error:
        result['error_class'] = type(error).__name__
    finally:
        if connection:
            connection.close()
        result.update(completed_at_ms=now_ms(), elapsed_ms=(time.monotonic_ns()-started)/1e6)
    return result


def configuration(path):
    value = read(path)
    require(value.get('schema') == 'argus.cost-trial.v1' and value.get('group') in GROUPS, 'invalid cost configuration')
    require(value.get('run_id') and value.get('deployment'), 'run and protected deployment required')
    require(value.get('mode', 'pilot') in ('pilot', 'formal'), 'invalid capture mode')
    require(1 <= value.get('timeout_seconds', 180) <= 600, 'bounded admission command timeout required')
    points = value.get('history_points', [])
    if points:
        require(len(points) == 3 and len({p['id'] for p in points}) == 3, 'exactly three distinct history points required')
        for p in points:
            require(p['id'] in ('small', 'medium', 'large'), 'use small/medium/large history points')
            require(p.get('expected_records') is None or type(p['expected_records']) is int and p['expected_records'] >= 2,
                    'invalid expected record count')
        require([p['id'] for p in points] == ['small', 'medium', 'large'], 'history points must be ordered')
        if value.get('mode') == 'formal':
            require(value.get('frozen') is True and all(type(p.get('expected_records')) is int for p in points),
                    'freeze all three pilot record counts before formal capture')
            counts = [p['expected_records'] for p in points]
            require(counts == sorted(set(counts)), 'formal record counts must strictly increase')
    return value


def load_runtime(value):
    import admission_trial
    settings = {'schema': 'argus.e1-trial.v1', 'case': 'legal', 'group': value['group'],
                'run_id': value['run_id'], 'deployment': value['deployment']}
    loaded = admission_trial.runtime(settings)
    admission_trial.preflight(settings, loaded)
    return loaded


def frozen_commands(path):
    commands = read(path)
    require(isinstance(commands, list) and commands and all(isinstance(a, list) and a
            and all(isinstance(s, str) and s and '\x00' not in s for s in a) for a in commands),
            'growth file must contain explicit nonempty argv arrays')
    require(not any(x in ('--token', '--password', '--api-key', '--client-secret') for a in commands for x in a),
            'credentials must use protected environment or file references')
    return commands


def command_once(argv, directory, timeout, invoke=run_logged):
    directory = Path(directory)
    require(not directory.exists(), 'command already has an intent; reconcile it without replay')
    directory.mkdir(parents=True, mode=0o700)
    receipt = {'schema': 'argus.cost-command.v1', 'argv_sha256': digest(argv), 'state': 'SUBMISSION_UNKNOWN',
               'started_at_ms': now_ms(), 'automatic_retries': 0}
    atomic(directory/'command.json', receipt)
    started = time.monotonic_ns()
    try:
        process = invoke(argv, timeout=timeout, diagnostic=directory/'diagnostic.json', stage='cost-command')
        receipt.update(state='COMPLETED', exit_code=process.returncode)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        receipt['error_class'] = type(error).__name__
    receipt.update(completed_at_ms=now_ms(), elapsed_ms=(time.monotonic_ns()-started)/1e6)
    atomic(directory/'command.json', receipt)
    return receipt


def captured_timing(root, nonce):
    """Only use hash-bound contemporaneous export, never offline replay timing."""
    if not root or not nonce:
        return {'result': 'UNKNOWN', 'reason': 'no correlated Trustee capture'}
    try:
        require(isinstance(nonce, str) and Path(nonce).name == nonce and nonce not in ('.', '..'), 'invalid capture nonce path')
        directory = Path(root)/nonce
        capture = read(directory/'capture.json')
        require(capture.get('nonce') == nonce, 'capture nonce differs')
        name = 'history-timing.json'
        require(sha(directory/name) == capture['artifacts'][name], 'timing capture hash differs')
        timing = read(directory/name)
        require(timing.get('schema') == 'argus.history-timing.v1', 'unsupported timing')
        return {'result': 'OBSERVED', 'sha256': sha(directory/name), 'capture_sha256': sha(directory/'capture.json'), **timing}
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {'result': 'UNKNOWN', 'error_class': type(error).__name__}


def await_history(socket_path, directory, seconds, snapshot):
    require(type(seconds) is int and 0 <= seconds <= 600, 'history wait budget must be 0..600 seconds')
    deadline = time.monotonic()+seconds
    observations = []
    while True:
        result = snapshot(socket_path)
        observations.append(result)
        atomic(directory/'history-wait.json', {'samples':observations, 'wait_budget_seconds':seconds})
        if (result.get('reason_code') not in ('MUTATION_PENDING', 'HISTORY_NOT_CONFIRMED')
                or time.monotonic() >= deadline):
            return result
        time.sleep(min(.5, max(0, deadline-time.monotonic())))


def deployment_binding(value, workload):
    """Re-read deployment and approved policy bytes; never hash cached objects."""
    result = {'result':'UNKNOWN','observed_at_ms':now_ms()}
    try:
        source = workload.protected_file(value['deployment'])
        raw = source.read_bytes()
        deployed = json.loads(raw)
        expected_policy = workload.policy_bytes(deployed)
        require(isinstance(expected_policy,bytes) and expected_policy,'approved policy bytes unavailable')
        files = {'deployment':{'path':str(source),'sha256':hashlib.sha256(raw).hexdigest()}}
        artifact = deployed.get('approved_policy_artifact')
        if artifact:
            path = workload.protected_file(artifact['path'])
            files['approved_policy_artifact'] = {'path':str(path),'sha256':sha(path)}
            require(files['approved_policy_artifact']['sha256'] == artifact['sha256'], 'approved policy artifact changed')
        installed = workload.Deployment(deployed).etc / (deployed['approved']['policy_id'] + '_cpu.rego')
        files['installed_policy'] = {'path':str(installed), 'sha256':sha(workload.protected_file(installed)) if installed.exists() else None}
        expected_hash = hashlib.sha256(expected_policy).hexdigest()
        require(files['installed_policy']['sha256'] in (None,expected_hash),'installed and approved policy bytes differ')
        result.update(result='OBSERVED',binding={'deployment_sha256':digest(deployed),
            'approved_sha256':digest(deployed['approved']),'expected_policy_sha256':expected_hash,'files':files})
    except (OSError,ValueError,KeyError,TypeError,AttributeError) as error:
        result['error_class'] = type(error).__name__
    return result


def accepted_provider_timing(attempt):
    nonce = attempt.get('accepted_nonce')
    rows = [r for r in attempt.get('provider_timings', []) if nonce and r.get('nonce') == nonce
            and r.get('attempt_id') == nonce and r.get('component') == 'provider'
            and r.get('stage') == 'provider' and r.get('status') == 'ALLOW']
    if len(rows) != 1 or not isinstance(rows[0].get('timings'),dict):
        return {'result':'UNKNOWN','accepted_nonce':nonce,'matching_receipts':len(rows),
                'reason':'no unique Provider timing for the accepted nonce'}
    return {'result':'OBSERVED','accepted_nonce':nonce,'timings':rows[0]['timings']}


def history_series(config_path, output, *, runtime_loader=load_runtime, snapshot=chain_snapshot,
                   invoke=run_logged):
    value, output = configuration(config_path), Path(output).resolve()
    require(value.get('history_points'), 'history points required')
    require(not output.exists(), 'series exists; collect it, never automatically replay growth/subscriptions')
    workload, deployed = runtime_loader(value)
    target_path = workload.Deployment(deployed).target
    target, policy = read(workload.protected_file(target_path)), digest(deployed['approved'])
    frozen = deployment_binding(value,workload)
    require(frozen['result'] == 'OBSERVED' and frozen['binding']['deployment_sha256'] == digest(deployed),
            'deployment or approved policy could not be frozen from original files')
    output.mkdir(parents=True, mode=0o700)
    config_hash = sha(config_path)
    plan = {'schema': 'argus.history-cost.v1', 'run_id': value['run_id'], 'group': value['group'],
            'configuration_sha256': config_hash, 'target': target, 'policy_sha256': policy,
            'frozen_deployment':frozen,
            'started_at_ms': now_ms(), 'points': [], 'remote_acceptance': 'NOT_ESTABLISHED_BY_THIS_TOOL'}
    # Pin every command input before the first operation, including later points.
    commands = {}
    for point in value['history_points']:
        path = point.get('growth_argv_file')
        commands[point['id']] = frozen_commands(path) if path else []
    plan['growth_commands_sha256'] = digest(commands)
    plan['points'] = [dict(p, result='NOT_RUN') for p in value['history_points']]
    atomic(output/'series.json', plan)
    previous_refs = None
    for index, point in enumerate(value['history_points']):
        row = plan['points'][index]
        directory = output/point['id']; directory.mkdir(mode=0o700)
        row.update(result='UNKNOWN', started_at_ms=now_ms(), growth=[])
        atomic(output/'series.json', plan)
        row['deployment_before_growth'] = deployment_binding(value,workload)
        if row['deployment_before_growth'].get('binding') != frozen['binding']:
            row.update(reason='deployment/policy changed before growth; no command submitted')
            atomic(output/'series.json',plan)
            break
        stop = False
        for n, argv in enumerate(commands[point['id']]):
            receipt = command_once(argv, directory/f'growth-{n}', value.get('timeout_seconds', 180), invoke)
            row['growth'].append(receipt)
            atomic(output/'series.json', plan)
            if receipt.get('exit_code') != 0:
                row['reason'] = 'growth outcome unknown or failed; no later operation replayed'
                stop = True
                break
        if stop:
            break
        before = await_history(deployed['trucon_socket_path'], directory, value.get('history_wait_seconds', 60), snapshot)
        atomic(directory/'chain-before.json', before)
        row.update(chain_before=before, actual_record_count=before.get('record_count'),
                   reference_bytes=before.get('reference_bytes'))
        if before['result'] != 'OBSERVED':
            row.update(result='UNAVAILABLE', reason='history unavailable; preserve capacity/pending/error outcome')
            atomic(output/'series.json', plan)
            break
        refs = before['snapshot']['log_ids']
        current = read(workload.protected_file(target_path))
        row['deployment_before_admission'] = deployment_binding(value,workload)
        if current != target or row['deployment_before_admission'].get('binding') != frozen['binding']:
            row.update(reason='target/deployment/policy changed while growing history; admission not submitted')
            atomic(output/'series.json',plan)
            break
        require(previous_refs is None or refs[:len(previous_refs)] == previous_refs and len(refs) > len(previous_refs),
                'history must strictly grow from the preceding point without resetting the chain')
        previous_refs = refs
        if point.get('expected_records') is not None and len(refs) != point['expected_records']:
            row.update(reason='frozen history length differs; do not grow until a favorable point appears')
            atomic(output/'series.json', plan)
            break
        settings = {'schema': 'argus.e1-trial.v1', 'case': 'legal', 'group': value['group'],
                    'run_id': value['run_id'], 'deployment': value['deployment']}
        atomic(directory/'admission-config.json', settings)
        # E1 owns fresh-subscription collection; never time verify(existing SVID).
        argv = [sys.executable, str(ROOT/'admission_trial.py'), 'attempt', '--new-subscription',
                '--config', str(directory/'admission-config.json'), '--output', str(directory/'attempt'),
                '--timeout', str(value.get('timeout_seconds', 180))]
        row['admission_command'] = command_once(argv, directory/'admission-command', value.get('timeout_seconds', 180)+30, invoke)
        attempt_file = directory/'attempt'/'attempt.json'
        try:
            attempt = read(attempt_file)
            require(attempt.get('run_id') == value['run_id'] and attempt.get('target') == target
                    and attempt.get('group') == value['group']
                    and attempt.get('deployment_sha256') == frozen['binding']['deployment_sha256'],
                    'admission attempt run/target/group/deployment differs')
            row.update(attempt=attempt, attempt_sha256=sha(attempt_file))
            nonce = attempt.get('accepted_nonce')
            row['provider_timing_evidence'] = accepted_provider_timing(attempt)
            row['provider_timings'] = row['provider_timing_evidence'].get('timings')
            row['admission_to_ready_ms'] = attempt.get('ready_elapsed_ms')
            row['trustee_timings'] = captured_timing(value.get('trustee_capture_dir'), nonce)
            row['admission_result'] = attempt.get('result', 'UNKNOWN')
            row['collection_complete'] = attempt.get('complete') is True
            row['result'] = 'OBSERVED' if row['collection_complete'] and attempt.get('result') in ('ADMITTED','DENIED') else 'UNKNOWN'
        except (OSError, ValueError, KeyError, TypeError) as error:
            row.update(result='UNKNOWN', error_class=type(error).__name__)
        after = snapshot(deployed['trucon_socket_path'])
        atomic(directory/'chain-after.json', after)
        row['chain_after'] = after
        row['history_stable'] = before.get('snapshot') == after.get('snapshot') and after['result'] == 'OBSERVED'
        row['target_stable'] = read(workload.protected_file(target_path)) == target
        row['deployment_after_admission'] = deployment_binding(value,workload)
        row['deployment_policy_stable'] = row['deployment_after_admission'].get('binding') == frozen['binding']
        if not row['history_stable'] or not row['target_stable'] or not row['deployment_policy_stable']:
            row.update(result='UNKNOWN', reason='history, target, deployment or policy changed during admission measurement')
        row['completed_at_ms'] = now_ms()
        atomic(output/'series.json', plan)
        if (row['admission_command'].get('state') != 'COMPLETED' or not row['target_stable']
                or not row['history_stable'] or not row['deployment_policy_stable']
                or row['result'] != 'OBSERVED'):
            row.setdefault('reason', 'admission outcome is unresolved; no later growth or subscription submitted')
            atomic(output/'series.json', plan)
            break
    plan['completed_at_ms'] = now_ms()
    atomic(output/'series.json', plan)
    return plan


def collect_history(series_directory, capture_root, output):
    """Join transferred Trustee originals after the two hosts finish capture."""
    directory = Path(series_directory)
    result = read(directory/'series.json')
    require(result.get('schema') == 'argus.history-cost.v1', 'unsupported cost series')
    result['source_series_sha256'] = sha(directory/'series.json')
    result['replayed_admission'] = False
    for row in result['points']:
        if not row.get('attempt_sha256'):
            continue
        attempt_file = directory/row['id']/'attempt'/'attempt.json'
        require(sha(attempt_file) == row['attempt_sha256'], 'original admission attempt changed')
        attempt = read(attempt_file)
        require(attempt.get('run_id') == result['run_id'] and attempt.get('target') == result['target']
                and attempt.get('deployment_sha256') == result.get('frozen_deployment',{}).get('binding',{}).get('deployment_sha256'),
                'attempt association differs')
        row['trustee_timings'] = (captured_timing(capture_root, attempt.get('accepted_nonce')) if result['group'] == 'full_argus'
                                  else {'result':'NOT_APPLICABLE', 'reason':'native baseline has no custom workload history appraisal'})
    atomic(output, result)
    return result


def subscription_probe(deployment, run_id, timeout):
    argv = [str(deployment.bin/'spiffe-helper'), '-config', str(deployment.etc/'helper.conf'),
            '-probe-broker', '-probe-run-id', run_id, '-probe-timeout', str(timeout)+'s']
    try:
        completed = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout+5)
        require(len(completed.stdout) <= 65536, 'probe metadata too large')
        result = json.loads(completed.stdout)
        require(result.get('schema') == 'argus.subscription-probe.v1' and result.get('run_id') == run_id,
                'probe result belongs to another run')
        return result
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return {'result': 'UNKNOWN', 'error_class': type(error).__name__, 'run_id': run_id}


def pending_receipt(value, other, output):
    """Reuse E1's live, protected outbox query instead of a handwritten flag."""
    try:
        config_path = value['pending_config']
        config = read(config_path)
        require(config.get('run_id') == value['run_id'] and config.get('group') == value['group']
                and config.get('case') == 'record_pending' and config.get('deployment') == value['other_deployment'],
                'pending configuration does not identify service A in this run')
        completed = subprocess.run([sys.executable, str(ROOT/'admission_trial.py'), 'pending', '--config', config_path,
                                    '--output', str(output)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        require(completed.returncode == 0, 'live pending query failed')
        receipt = read(output)
        require(receipt.get('schema') == 'argus.e1-pending.v1' and receipt.get('run_id') == value['run_id']
                and receipt.get('deployment_sha256') == digest(other) and receipt.get('workload_id') == other['workload']['id'],
                'pending receipt identifies a different service')
        return receipt
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        return {'result': 'UNKNOWN', 'error_class': type(error).__name__}


def shared_snapshot(config_path, phase, output, *, runtime_loader=load_runtime, snapshot=chain_snapshot,
                    probe=subscription_probe):
    value = configuration(config_path)
    require(phase in ('before', 'pending', 'confirmed'), 'invalid shared phase')
    require(not Path(output).exists(), 'snapshot output exists')
    workload, deployed = runtime_loader(value)
    other = read(workload.protected_file(value['other_deployment']))
    require(other['trucon_socket_path'] == deployed['trucon_socket_path'] and other['tc_api_url'] == deployed['tc_api_url'],
            'A and B must share the same controlled chain endpoint')
    require(other['workload']['id'] != deployed['workload']['id']
            and other['paths']['records_dir'] != deployed['paths']['records_dir'], 'two distinct service instances required')
    d = workload.Deployment(deployed)
    seconds = value.get('shared_window_seconds', 30)
    require(type(seconds) is int and 1 <= seconds <= 300, 'shared window must be 1..300 seconds')
    result = {'schema': 'argus.shared-cost-snapshot.v1', 'run_id': value['run_id'], 'group': value['group'],
              'phase': phase, 'configuration_sha256': sha(config_path), 'started_at_ms': now_ms(),
              'target_id': d.identity['target_id'], 'workload_a': other['workload']['id'], 'workload_b': deployed['workload']['id'],
              'target': read(workload.protected_file(d.target)), 'status': workload.status(deployed),
              'chain': snapshot(deployed['trucon_socket_path']), 'scope': 'read-only same-chain observation; no invalidation inferred'}
    result['complete'] = False
    atomic(output, result)
    if phase == 'pending':
        result['mutation_before'] = pending_receipt(value, other, str(output)+'.mutation-before.json')
    end = time.monotonic()+seconds
    result['chain_samples'] = [result['chain']]
    # A second subscription uses the approved Helper executable without its
    # publisher. The original Helper/NGINX remain running throughout the window.
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending_probe = pool.submit(probe, d, value['run_id'], seconds)
        while time.monotonic() < end:
            time.sleep(min(1, max(0, end-time.monotonic())))
            result['chain_samples'].append(snapshot(deployed['trucon_socket_path']))
        result['probe'] = pending_probe.result()
    result['status_after'] = workload.status(deployed)
    result['target_after'] = read(workload.protected_file(d.target))
    if phase == 'pending':
        result['mutation_after'] = pending_receipt(value, other, str(output)+'.mutation-after.json')
    result['completed_at_ms'] = now_ms()
    result['complete'] = True
    atomic(output, result)
    return result


def shared_collect(paths, load_directory, output, clock_uncertainty_ms=0):
    """Keep new subscription and pre-existing connection effects separate."""
    require(type(clock_uncertainty_ms) is int and clock_uncertainty_ms >= 0, 'clock uncertainty required')
    rows = [read(p) for p in paths]
    require([r.get('phase') for r in rows] == ['before', 'pending', 'confirmed'], 'three ordered phase snapshots required')
    first = rows[0]
    require(all(r.get('schema') == 'argus.shared-cost-snapshot.v1' and all(r.get(k) == first.get(k)
                for k in ('run_id', 'group', 'configuration_sha256', 'target_id', 'target', 'workload_a', 'workload_b')) for r in rows),
            'shared observations have different run/target/chain configuration')
    require(all(rows[n]['completed_at_ms'] < rows[n+1]['started_at_ms'] for n in (0, 1)), 'snapshots overlap')
    directory = Path(load_directory)
    load = read(directory/'load-result.json')
    require(load.get('run_id') == first['run_id'] and load.get('server_id') == first['target_id'], 'load run/server differs')
    require(load.get('requests_sha256') == sha(directory/'requests.jsonl'), 'load source changed')
    requests = [json.loads(line) for line in (directory/'requests.jsonl').read_text().splitlines() if line.strip()]
    require(all(r.get('run_id') == first['run_id'] for r in requests), 'request belongs to another run')
    pending = rows[1]
    # These samples bracket the same observation window; they do not establish
    # the full pending interval. E1 mutation/barrier evidence supplies that.
    mutations = [pending.get(k, {}) for k in ('mutation_before', 'mutation_after')]
    mutation_matched = all(m.get('pending_observed') is True and m.get('mutation_matches_target') is True
                           and m.get('run_id') == first['run_id'] and m.get('workload_id') == first['workload_a']
                           and m.get('chain_id') == 'default' for m in mutations)
    mutation_matched &= bool(mutations[0].get('mutation_id')) and mutations[0].get('mutation_id') == mutations[1].get('mutation_id')
    left = mutations[0].get('observed_at_ms', pending['started_at_ms'])
    right = mutations[1].get('observed_at_ms', pending['completed_at_ms'])
    mutation_matched &= pending['started_at_ms'] <= left < right <= pending['completed_at_ms']
    before_samples = first.get('chain_samples', [first.get('chain',{})])
    normal_verified = (first.get('complete') is True and bool(before_samples)
                       and all(s.get('result') == 'OBSERVED' and s.get('http_status') == 200 for s in before_samples))
    prior = {r.get('connection_id') for r in requests if normal_verified
             and r['started_at_ms'] > first['started_at_ms']+clock_uncertainty_ms
             and r.get('completed_at_ms', float('inf')) < first['completed_at_ms']-clock_uncertainty_ms
             and r.get('outcome') == 'success' and r.get('memory_result') == 'nonempty'} - {None}
    during = [r for r in requests if r['started_at_ms'] > left+clock_uncertainty_ms
              and r.get('completed_at_ms', right) < right-clock_uncertainty_ms]
    reused = [r for r in during if r.get('connection_id') in prior and r.get('connection_reused') is True]
    probes = [r.get('probe', {'result': 'UNKNOWN'}) for r in rows]
    for r, probe in zip(rows, probes):
        require(probe.get('run_id') == first['run_id'], 'probe run differs')
        if probe.get('result') == 'OBSERVED':
            require(probe.get('target') == first['target'] and r['started_at_ms'] <= probe['started_at_ms']
                    <= probe['completed_at_ms'] <= r['completed_at_ms'], 'probe target/window differs')
    helpers = {r.get(name, {}).get('helper_invocation_id') for r in rows for name in ('status', 'status_after')}
    stable_helper = len(helpers) == 1 and None not in helpers and '' not in helpers
    stable_target = all(r.get('target_after') == first['target'] for r in rows)
    result = {'schema': 'argus.shared-cost.v1', 'run_id': first['run_id'], 'group': first['group'],
              'snapshots_sha256': [sha(p) for p in paths], 'load_sha256': sha(directory/'load-result.json'),
              'unchanged_existing_helper': stable_helper, 'unchanged_target': stable_target,
              'pending_mutation_association': 'OBSERVED' if mutation_matched else 'UNKNOWN',
              'chain_observations': [r['chain'] for r in rows], 'new_subscription': dict(zip(('before','pending','confirmed'), probes)),
              'existing_traffic': {'result': 'OBSERVED' if mutation_matched and all(r.get('complete') is True for r in rows)
                  and stable_helper and stable_target and load.get('measurement_complete') is True and reused else 'UNKNOWN',
                  'normal_before_window_verified':normal_verified,
                  'connection_anchor':'successful nonempty request wholly inside verified normal before snapshot',
                  'requests_in_observed_window': len(during), 'preexisting_connection_requests': len(reused),
                  'nonempty_successes': sum(r.get('outcome') == 'success' and r.get('memory_result') == 'nonempty' for r in reused),
                  'failures': sum(r.get('outcome') != 'success' for r in reused)},
              'scope': 'probe and traffic are separate observations; 409 alone does not prove a particular A mutation',
              'clock_uncertainty_ms': clock_uncertainty_ms, 'policy_invalid_receiver': 'UNKNOWN'}
    atomic(output, result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest='action', required=True)
    for name in ('history', 'shared-snapshot'):
        c = commands.add_parser(name); c.add_argument('--config', required=True); c.add_argument('--output', required=True)
        if name == 'shared-snapshot': c.add_argument('--phase', choices=('before', 'pending', 'confirmed'), required=True)
    c = commands.add_parser('history-collect')
    for name in ('series-directory', 'trustee-captures', 'output'):
        c.add_argument('--'+name, required=True)
    c = commands.add_parser('shared-collect')
    for name in ('before', 'pending', 'confirmed', 'load-directory', 'output'):
        c.add_argument('--'+name, required=True)
    c.add_argument('--clock-uncertainty-ms', type=int, required=True)
    args = p.parse_args()
    if args.action == 'history': value = history_series(args.config, args.output)
    elif args.action == 'history-collect': value = collect_history(args.series_directory, args.trustee_captures, args.output)
    elif args.action == 'shared-snapshot': value = shared_snapshot(args.config, args.phase, args.output)
    else: value = shared_collect([args.before, args.pending, args.confirmed], args.load_directory, args.output, args.clock_uncertainty_ms)
    print(json.dumps({'schema': value['schema'], 'run_id': value['run_id']}))


if __name__ == '__main__':
    main()
