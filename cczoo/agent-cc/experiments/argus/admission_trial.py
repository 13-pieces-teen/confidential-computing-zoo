#!/usr/bin/env python3
"""Collect fixed E1 observations from the installed production runtime.

No launch, fault, registration, attestation bypass or expected verdict is supplied
by this tool. Existing lifecycle commands perform those operations explicitly.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import time
import http.client
import socket
import os
import stat
import uuid

from common import atomic, digest, read, require, sanitize_diagnostic, sha
from admission_stages import summarize

CASES = ('legal', 'same_image_new_instance', 'unrelated_activity', 'config_mismatch', 'record_pending')
GROUPS = ('full_argus', 'native_spire_guarded')


def config(path):
    value = read(path)
    require(set(value) <= {'schema', 'run_id', 'case', 'group', 'deployment', 'unrelated_deployment', 'barrier_receipt'}, 'unknown E1 configuration field')
    require(value.get('schema') == 'argus.e1-trial.v1' and value.get('case') in CASES and value.get('group') in GROUPS,
            'invalid E1 configuration')
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}', value.get('run_id', '')) is not None, 'fixed run_id required')
    for key in ('deployment', 'unrelated_deployment'):
        if key in value:
            require(Path(value[key]).is_absolute() and Path(value[key]).is_file(), 'existing absolute deployment reference required')
    require('deployment' in value, 'deployment reference required')
    require(value['case'] != 'unrelated_activity' or 'unrelated_deployment' in value, 'unrelated case requires its separate deployment reference')
    require(value['case'] != 'record_pending' or 'barrier_receipt' in value, 'record_pending requires the actual reached barrier receipt')
    return value


def runtime(value):
    from trusted_runtime import load
    workload, _, deployment = load(value['deployment'], require_experiment=True)
    package = Path(deployment['paths']['install_dir'])
    require(read(package / 'build-manifest.json').get('experiment_variant') == value['group'], 'installed experiment arm differs from configured group')
    return workload, deployment


def preflight(value, loaded=None):
    workload, c = loaded or runtime(value)
    d = workload.Deployment(c)
    manifest = workload.runtime_manifest(c)
    require(manifest.get('build_integrity') == 'MATCH', 'installed build manifest verification failed')
    info = {'schema': 'argus.e1-preflight.v1', 'configuration_sha256': digest(value), 'group': value['group'],
            'target_id': d.identity['target_id'], 'workload_id': d.workload['id'], 'source_manifest': manifest,
            'observation_only': True, 'remote_result': 'NOT_RUN'}
    if value['case'] == 'unrelated_activity':
        other = read(workload.protected_file(value['unrelated_deployment']))
        require(other['workload']['id'] != d.workload['id'] and other['paths']['records_dir'] != str(d.records)
                and other['paths']['run_name'] != d.run_name, 'unrelated activity must have independent workload and run records')
        # Launch-only uses the same local measured control path but no additional
        # SPIRE Agent or Helper is started for the unrelated workload.
        require(other['tc_api_url'] == c['tc_api_url'] and other['trucon_socket_path'] == c['trucon_socket_path'],
                'unrelated activity must use the same measured TC API path')
    return info


class TruConConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__('localhost', timeout=5)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def pending_observation(value, c, workload):
    """Read actual outbox/chain state. Never store raw requests or signed bundles."""
    receipt_path = workload.protected_file(value['barrier_receipt'])
    receipt = read(receipt_path)
    require(receipt.get('schema') == 'argus.lifecycle-barrier-receipt.v1'
            and re.fullmatch(r'mutation-[0-9a-f]{32}', receipt.get('mutation_id', '')) is not None, 'invalid reached mutation receipt')
    path = Path(c['trucon_socket_path'])
    info = path.lstat()
    require(path.resolve() == path and stat.S_ISSOCK(info.st_mode) and info.st_uid == 0
            and info.st_mode & 0o077 == 0, 'TruCon must use its root-only UDS')
    deployment = workload.Deployment(c)
    target = read(workload.protected_file(deployment.target))
    observed = {'schema': 'argus.e1-pending.v1', 'run_id': value['run_id'], 'group': value['group'],
                'configuration_sha256': digest(value), 'deployment_sha256': digest(c),
                'target': target, 'workload_id': deployment.workload['id'], 'chain_id': 'default',
                'mutation_id': receipt['mutation_id'],
                'barrier_receipt_sha256': sha(receipt_path), 'barrier_point': receipt['point'],
                'classification': receipt.get('classification'), 'observed_at_ms': time.time_ns() // 1_000_000}
    for route, caller, key in (('/mutations', 'docktap', 'outbox'),
                               ('/chain-state?include_history=true', 'argus_provider', 'history')):
        conn = TruConConnection(str(path))
        try:
            conn.request('GET', route, headers={'X-TruCon-Caller-Service': caller})
            response = conn.getresponse()
            raw = response.read(4 * 1024 * 1024 + 1)
            require(len(raw) <= 4 * 1024 * 1024, 'pending observation exceeds limit')
            observed[key] = {'http_status': response.status, 'reason_code': response.getheader('X-Argus-Reason')}
            if key == 'outbox' and response.status == 200:
                rows = json.loads(raw)['mutations']
                selected = [row for row in rows if row['mutation_id'] == receipt['mutation_id']]
                require(len(selected) <= 1, 'duplicate durable mutation ID')
                if selected:
                    row = selected[0]
                    safe = {k: row.get(k) for k in ('mutation_id', 'chain_id', 'operation_type', 'status', 'record_id', 'created_at', 'updated_at')}
                    request = json.loads(row['request_json'])
                    result = json.loads(row['result_json']) if row.get('result_json') else None
                    op = (result or request)['op_record']
                    safe.update(container_id=op.get('container', {}).get('id'),
                                response_status=op.get('response', {}).get('status'),
                                result_sha256=digest(result) if result else None,
                                submission_sha256=digest(row['submission_json']) if row.get('submission_json') else None)
                    observed['mutation'] = safe
        finally:
            conn.close()
    observed['pending_observed'] = bool(observed.get('mutation', {}).get('status') in ('INFLIGHT', 'RESULT_READY', 'SUBMITTED')
                                        and observed.get('history', {}).get('http_status') == 409
                                        and observed['history']['reason_code'] == 'MUTATION_PENDING')
    observed['mutation_matches_target'] = observed.get('mutation', {}).get('container_id') == target.get('container_id')
    observed['target_check'] = target_check(deployment.bin / 'argus-workload', deployment.target, target)
    observed['hardware_provenance'] = 'NOT_ESTABLISHED_BY_THIS_TOOL'
    return observed


def attempt(config_file, output, *, new_subscription=False, timeout_seconds=180, loaded=None, invoke=subprocess.run):
    """Explicitly request a fresh Helper subscription. Does not launch/replay Docker."""
    require(new_subscription, 'explicit --new-subscription is required; current readiness is not a new admission')
    require(type(timeout_seconds) is int and 1 <= timeout_seconds <= 600, 'attempt timeout must be 1..600 seconds')
    value = config(config_file)
    workload, c = loaded or runtime(value)
    preflight(value, (workload, c))
    d = workload.Deployment(c)
    target = read(workload.protected_file(d.target))
    output = Path(output).resolve()
    require(not output.exists(), 'retain existing attempt and use a new directory')
    output.mkdir(parents=True, mode=0o700)
    prior = workload.status(c)
    result = {'schema': 'argus.e1-attempt.v1', 'run_id': value['run_id'], 'group': value['group'],
              'configuration_sha256': digest(value), 'deployment_sha256': digest(c), 'target': target,
              'attempt_id': uuid.uuid4().hex, 'started_at_ms': time.time_ns() // 1_000_000,
              'previous_helper_invocation_id': prior.get('helper_invocation_id'),
              'command': ['systemctl', 'restart', d.unit('helper')], 'new_subscription_requested': True,
              'timeout_seconds': timeout_seconds, 'units': {role: d.unit(role) for role in ('helper', 'provider', 'agent')}}
    if value['case'] == 'record_pending':
        result['pending_before'] = pending_observation(value, c, workload)
    atomic(output / 'intent.json', result)  # A crash is not silently retried.
    attempt_started = time.monotonic()
    deadline = attempt_started + timeout_seconds
    try:
        command = invoke(result['command'], capture_output=True, text=True, timeout=timeout_seconds)
        result['command_exit_code'] = command.returncode
    except (OSError, subprocess.SubprocessError) as error:
        result['command_error_class'] = type(error).__name__
    while True:
        status = workload.status(c)
        fresh = bool(re.fullmatch(r'[0-9a-f]{32}', status.get('helper_invocation_id') or '')
                     and status['helper_invocation_id'] != prior.get('helper_invocation_id'))
        if (fresh and status.get('ready')) or time.monotonic() >= deadline:
            break
        time.sleep(0.25)
    result['status_after'] = status
    result['ready_elapsed_ms'] = (time.monotonic() - attempt_started) * 1000 if fresh and status.get('ready') else None
    result['completed_at_ms'] = time.time_ns() // 1_000_000
    result['helper_invocation_id'] = status.get('helper_invocation_id') if fresh else None
    try:
        result['target_unchanged'] = read(workload.protected_file(d.target)) == target
    except (OSError, ValueError) as error:
        result['target_unchanged'] = False
        result['target_error_class'] = type(error).__name__
    # Journal remains available even when no Quote/EAR or ready file was produced.
    command = ['journalctl', '-u', d.unit('provider'), '-u', d.unit('agent'), '-u', d.unit('helper'),
               '--since', '@' + str(result['started_at_ms'] / 1000), '--until', '@' + str(result['completed_at_ms'] / 1000),
               '--no-pager', '-o', 'json']
    try:
        journal = invoke(command, capture_output=True, text=True, timeout=20)
        result['journal_exit_code'] = journal.returncode
        contents = journal.stdout if journal.returncode == 0 else ''
    except (OSError, subprocess.SubprocessError) as error:
        result['journal_exit_code'] = None
        result['journal_error_class'] = type(error).__name__
        contents = ''
    path = output / 'admission-stage-journal.jsonl'
    with path.open('x', encoding='utf-8') as stream:
        stream.write(contents)
    path.chmod(0o600)
    result['journal_sha256'] = sha(path)
    result['stages'] = summarize(path.read_text(encoding='utf-8'), target, result['started_at_ms'], result['completed_at_ms'],
                                 result['helper_invocation_id'], group=value['group'], units=result['units'],
                                 ready=bool(fresh and status.get('ready') and result['target_unchanged']))
    result['result'] = result['stages']['new_admission']
    result['complete'] = bool(result['journal_exit_code'] == 0 and result['target_unchanged'] and fresh)
    result['accepted_nonce'] = result['stages']['accepted_nonce']
    result['provider_timings'] = [row for row in result['stages']['stage_receipts']
                                 if row['component'] == 'provider']
    if value['case'] == 'record_pending':
        result['pending_after'] = pending_observation(value, c, workload)
        result['pending_entire_attempt'] = bool(result['pending_before'].get('pending_observed')
                                                and result['pending_after'].get('pending_observed')
                                                and result['pending_before']['mutation_id'] == result['pending_after']['mutation_id'])
    atomic(output / 'attempt.json', result)
    return result


def target_check(binary, registration, expected, invoke=subprocess.run):
    try:
        result = invoke([str(binary), '-action', 'check', '-registration', str(registration)],
                        capture_output=True, text=True, timeout=20)
        if result.returncode == 0:
            observed = json.loads(result.stdout)
            return {'result': 'MATCH' if observed == expected else 'UNKNOWN', 'target': observed}
        error = result.stderr.strip()
        # An unavailable daemon/binary or generic process error is not a denial.
        semantic = ('target belongs to another boot', 'target PID was reused', 'target process is not alive',
                    'target is not in registered container cgroup', 'workload config changed',
                    'upstream is served by another process', 'target process exited',
                    'target ns/pid changed', 'target ns/net changed', 'target exe changed',
                    'container is not running with an actual image config digest')
        changed = error in semantic
        pid = expected.get('pid')
        absent = isinstance(pid, str) and pid.isdigit() and not Path('/proc', pid).exists()
        return {'result': 'LOCAL_BINDING_REJECTED' if changed or absent else 'UNKNOWN',
                'exit_code': result.returncode, 'process_absent': absent, 'diagnostic': sanitize_diagnostic(error)}
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        return {'result': 'UNKNOWN', 'error_class': type(error).__name__}


def observe(config_file, output, reference=None, loaded=None, invoke=subprocess.run, attempt_file=None):
    value = config(config_file)
    workload, c = loaded or runtime(value)
    coverage = preflight(value, (workload, c))
    d = workload.Deployment(c)
    output = Path(output).resolve()
    require(not output.exists(), 'observation directory already exists; retain it and use a new phase directory')
    output.mkdir(parents=True, mode=0o700)
    result = {'schema': 'argus.e1-observation.v1', 'run_id': value['run_id'], 'case': value['case'], 'group': value['group'],
              'configuration_sha256': digest(value), 'deployment_sha256': digest(c), 'started_at_ms': time.time_ns() // 1000000,
              'target_id': d.identity['target_id'], 'workload_id': d.workload['id'],
              'coverage': coverage, 'actual_admission': 'UNKNOWN', 'hardware_provenance': 'NOT_ESTABLISHED_BY_THIS_TOOL'}
    result['status_before'] = workload.status(c)
    result['approved_config_digest'] = c['approved']['config_digest']
    try:
        result['observed_config_digest'] = 'sha256:' + sha(workload.protected_file(d.workload['config_host_path']))
    except (OSError, ValueError):
        result['observed_config_digest'] = None
    if d.target.is_file():
        target = read(workload.protected_file(d.target))
        atomic(output / 'target.json', target)
        result['registered_target'] = target
        result['target_sha256'] = sha(output / 'target.json')
        result['target_check'] = target_check(d.bin / 'argus-workload', d.target, target, invoke)
    if reference is not None:
        previous_dir = Path(reference).resolve()
        previous = read(previous_dir / 'observation.json')
        require(all(previous.get(k) == result[k] for k in ('run_id', 'case', 'group', 'configuration_sha256', 'deployment_sha256', 'target_id', 'workload_id')),
                'reference belongs to another trial/deployment')
        require(previous.get('target_sha256') == sha(previous_dir / 'target.json'), 'reference target artifact changed')
        old = read(workload.protected_file(previous_dir / 'target.json'))
        result['reference_sha256'] = sha(previous_dir / 'observation.json')
        result['reference_content_digest'] = digest(previous)
        result['old_target_check'] = target_check(d.bin / 'argus-workload', previous_dir / 'target.json', old, invoke)
    try:
        verification = workload.verify(c)
        result['production_verification'] = verification
        result['production_verification_status'] = 'COMPLETED'
        journal = verification.get('appraisal_log')
        if journal and Path(journal).is_file():
            with (output / 'appraisal-journal.jsonl').open('xb') as stream:
                stream.write(workload.protected_file(journal).read_bytes())
            (output / 'appraisal-journal.jsonl').chmod(0o600)
            result['appraisal_journal_sha256'] = sha(output / 'appraisal-journal.jsonl')
    except Exception as error:
        result['production_verification_status'] = 'UNAVAILABLE'
        result['verification_error_class'] = type(error).__name__
        result['verification_diagnostic'] = sanitize_diagnostic(str(error))
    result['status_after'] = workload.status(c)
    verify = result.get('production_verification', {})
    proof = verify.get('svid_and_business', {})
    if (result['status_before'] == result['status_after'] and result['status_after'].get('ready') is True
            and result.get('target_check', {}).get('result') == 'MATCH'
            and verify.get('target') == result.get('registered_target')
            and proof.get('server_spiffe_id') == d.identity['target_id']
            and proof.get('client_spiffe_id') in d.allowed_client_ids
            and proof.get('server_serial') == result['status_after'].get('target_serial')):
        result['actual_admission'] = 'ADMITTED'
    appraisal = verify.get('appraisal')
    found = re.search(r'\bnonce=([A-Za-z0-9_-]{43})(?:\s|$)', appraisal or '')
    result['accepted_workload_nonce'] = found[1] if found else None
    if attempt_file is not None:
        path = Path(attempt_file).resolve()
        receipt = read(workload.protected_file(path))
        require(receipt.get('schema') == 'argus.e1-attempt.v1' and
                all(receipt.get(k) == result.get(k) for k in ('run_id', 'group', 'configuration_sha256', 'deployment_sha256'))
                and receipt.get('target') == result.get('registered_target'), 'attempt belongs to another trial or target')
        require(receipt['journal_sha256'] == sha(path.parent / 'admission-stage-journal.jsonl'), 'attempt journal changed')
        require(receipt['completed_at_ms'] <= result['started_at_ms'], 'attempt is not prior to observation')
        result['new_admission_attempt'] = receipt
        result['attempt_sha256'] = sha(path)
        result['first_rejection_stage'] = receipt['stages']['first_rejection_stage']
        result['stage_receipts'] = receipt['stages']['stage_receipts']
    if value['case'] == 'record_pending':
        try:
            result['pending'] = pending_observation(value, c, workload)
        except (OSError, ValueError, KeyError, http.client.HTTPException) as error:
            result['pending'] = {'pending_observed': False, 'result': 'UNKNOWN', 'error_class': type(error).__name__}
    if value['case'] == 'unrelated_activity':
        other = read(workload.protected_file(value['unrelated_deployment']))
        record = Path(other['paths']['records_dir']) / 'launch-state.json'
        if record.is_file():
            launch = read(workload.protected_file(record))
            result['unrelated_launch'] = {k: launch.get(k) for k in ('run_id', 'launch_id', 'container_id', 'stage', 'workload_id', 'updated_at')}
            result['unrelated_launch']['source_sha256'] = sha(record)
    result['completed_at_ms'] = time.time_ns() // 1000000
    atomic(output / 'observation.json', result)
    return result


def compare(before, after):
    require(before.get('schema') == after.get('schema') == 'argus.e1-observation.v1', 'invalid E1 observations')
    require(all(before.get(k) == after.get(k) for k in ('run_id', 'case', 'group', 'configuration_sha256', 'deployment_sha256', 'target_id', 'workload_id')),
            'observations belong to different trials')
    require(before['completed_at_ms'] <= after['started_at_ms'], 'observations overlap or are out of order')
    case, a, b = before['case'], before.get('registered_target', {}), after.get('registered_target', {})
    setup, scope = False, 'production admission observation; not raw Quote/EAR re-verification'
    if case == 'legal':
        setup = bool(b)
        observed = after['actual_admission'] == 'ADMITTED'
    elif case == 'same_image_new_instance':
        setup = bool(a and b and a.get('image_config_digest') == b.get('image_config_digest')
                     and a.get('container_id') != b.get('container_id') and a.get('launch_id') != b.get('launch_id')
                     and after.get('reference_content_digest') == digest(before))
        observed = before['actual_admission'] == after['actual_admission'] == 'ADMITTED' and after.get('old_target_check', {}).get('result') == 'LOCAL_BINDING_REJECTED'
        scope = 'new instance independently admitted; saved old local instance binding rejected; no forged Quote submission'
    elif case == 'unrelated_activity':
        previous, current = before.get('unrelated_launch', {}), after.get('unrelated_launch', {})
        setup = bool(a and a == b and current.get('stage') == 'complete' and current.get('launch_id')
                     and current.get('launch_id') != previous.get('launch_id')
                     and current.get('workload_id') != before['workload_id'])
        observed = before['actual_admission'] == after['actual_admission'] == 'ADMITTED'
        if before['group'] == 'full_argus':
            observed = observed and bool(after.get('accepted_workload_nonce') and before.get('accepted_workload_nonce')
                                        and after['accepted_workload_nonce'] != before['accepted_workload_nonce'])
        scope = 'fresh same-instance admission after a different measured launch; RTMR replay requires separately captured context'
    elif case == 'record_pending':
        pending = after.get('pending', {})
        receipt = after.get('new_admission_attempt', {})
        setup = bool(pending.get('pending_observed') and pending.get('mutation_matches_target')
                     and pending.get('target_check', {}).get('result') == 'MATCH'
                     and after.get('target_check', {}).get('result') == 'MATCH'
                     and receipt.get('new_subscription_requested') and receipt.get('complete')
                     and receipt.get('pending_entire_attempt'))
        decision = receipt.get('result', 'UNKNOWN')
        observed = setup and decision in ('ADMITTED', 'DENIED')
        scope = 'measured new admission during a real pending record; classify actual first rejection, not independent full-history policy benefit'
    else:
        setup = bool(before['actual_admission'] == 'ADMITTED' and before.get('observed_config_digest') == before.get('approved_config_digest')
                     and after.get('observed_config_digest') and after['observed_config_digest'] != after.get('approved_config_digest'))
        observed = after.get('target_check', {}).get('result') == 'LOCAL_BINDING_REJECTED'
        scope = 'local approved-configuration binding; not a claim of remote Trustee-only rejection'
    return {'schema': 'argus.e1-comparison.v1', 'case': case, 'group': before['group'], 'run_id': before['run_id'],
            'case_setup_observed': setup, 'declared_property_observed': bool(observed),
            'result': 'OBSERVED' if setup and observed else 'UNKNOWN', 'scope': scope,
            'actual_admission_before': before['actual_admission'], 'actual_admission_after': after['actual_admission'],
            'raw_quote_and_signed_ear_archive': 'SEPARATE_ARCHIVE_REQUIRED',
            'native_expected_failure': 'NOT_ASSUMED'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for command in ('preflight', 'observe'):
        item = sub.add_parser(command); item.add_argument('--config', required=True); item.add_argument('--output', required=True)
        if command == 'observe':
            item.add_argument('--reference', help='previous observation directory with saved target.json')
            item.add_argument('--attempt', help='attempt.json from an explicit new subscription')
    item = sub.add_parser('attempt')
    item.add_argument('--config', required=True); item.add_argument('--output', required=True)
    item.add_argument('--new-subscription', action='store_true')
    item.add_argument('--timeout', '--timeout-seconds', dest='timeout_seconds', type=int, default=180)
    item = sub.add_parser('pending')
    item.add_argument('--config', required=True); item.add_argument('--output', required=True)
    item = sub.add_parser('compare')
    for name in ('before', 'after', 'output'):
        item.add_argument('--' + name, required=True)
    args = parser.parse_args()
    if args.action == 'preflight':
        result = preflight(config(args.config)); atomic(args.output, result)
    elif args.action == 'observe':
        result = observe(args.config, args.output, args.reference, attempt_file=args.attempt)
    elif args.action == 'attempt':
        result = attempt(args.config, args.output, new_subscription=args.new_subscription, timeout_seconds=args.timeout_seconds)
    elif args.action == 'pending':
        value = config(args.config)
        require(value['case'] == 'record_pending', 'pending capture requires record_pending configuration')
        workload, c = runtime(value)
        preflight(value, (workload, c))
        result = pending_observation(value, c, workload)
        require(not Path(args.output).exists(), 'retain existing pending capture')
        atomic(args.output, result)
    else:
        result = compare(read(args.before), read(args.after))
        result['inputs_sha256'] = {'before': sha(args.before), 'after': sha(args.after)}
        atomic(args.output, result)
    print(json.dumps({'result': result.get('result', result.get('actual_admission', 'PREFLIGHT_ONLY'))}))


if __name__ == '__main__':
    main()
