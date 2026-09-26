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

from common import atomic, digest, read, require, sanitize_diagnostic, sha

CASES = ('legal', 'same_image_new_instance', 'unrelated_activity', 'config_mismatch')
GROUPS = ('full_argus', 'native_spire_guarded')


def config(path):
    value = read(path)
    require(set(value) <= {'schema', 'run_id', 'case', 'group', 'deployment', 'unrelated_deployment'}, 'unknown E1 configuration field')
    require(value.get('schema') == 'argus.e1-trial.v1' and value.get('case') in CASES and value.get('group') in GROUPS,
            'invalid E1 configuration')
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}', value.get('run_id', '')) is not None, 'fixed run_id required')
    for key in ('deployment', 'unrelated_deployment'):
        if key in value:
            require(Path(value[key]).is_absolute() and Path(value[key]).is_file(), 'existing absolute deployment reference required')
    require('deployment' in value, 'deployment reference required')
    require(value['case'] != 'unrelated_activity' or 'unrelated_deployment' in value, 'unrelated case requires its separate deployment reference')
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


def observe(config_file, output, reference=None, loaded=None, invoke=subprocess.run):
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
    else:
        setup = bool(before['actual_admission'] == 'ADMITTED' and before.get('observed_config_digest') == before.get('approved_config_digest')
                     and after.get('observed_config_digest') and after['observed_config_digest'] != after.get('approved_config_digest'))
        observed = after.get('target_check', {}).get('result') == 'LOCAL_BINDING_REJECTED'
        scope = 'local approved-configuration binding; not a claim of remote Trustee-only rejection'
    return {'schema': 'argus.e1-comparison.v1', 'case': case, 'group': before['group'], 'run_id': before['run_id'],
            'case_setup_observed': setup, 'declared_property_observed': bool(observed),
            'result': 'OBSERVED' if setup and observed else 'UNKNOWN', 'scope': scope,
            'actual_admission_before': before['actual_admission'], 'actual_admission_after': after['actual_admission'],
            'raw_quote_and_signed_ear_archive': 'NOT_PROVIDED_BY_PRODUCTION_CLI',
            'native_expected_failure': 'NOT_ASSUMED'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for command in ('preflight', 'observe'):
        item = sub.add_parser(command); item.add_argument('--config', required=True); item.add_argument('--output', required=True)
        if command == 'observe':
            item.add_argument('--reference', help='previous observation directory with saved target.json')
    item = sub.add_parser('compare')
    for name in ('before', 'after', 'output'):
        item.add_argument('--' + name, required=True)
    args = parser.parse_args()
    if args.action == 'preflight':
        result = preflight(config(args.config)); atomic(args.output, result)
    elif args.action == 'observe':
        result = observe(args.config, args.output, args.reference)
    else:
        result = compare(read(args.before), read(args.after))
        result['inputs_sha256'] = {'before': sha(args.before), 'after': sha(args.after)}
        atomic(args.output, result)
    print(json.dumps({'result': result.get('result', result.get('actual_admission', 'PREFLIGHT_ONLY'))}))


if __name__ == '__main__':
    main()
