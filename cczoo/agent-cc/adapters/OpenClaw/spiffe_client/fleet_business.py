#!/usr/bin/env python3
"""Real Gateway fleet acceptance; known work is resumed, unknown writes never replayed."""
import argparse
from contextlib import contextmanager
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import signal
import ssl
import subprocess
import time
from urllib.parse import quote, urlsplit
import uuid

import deploy
import fleet
from verify_business import answer

ROOT = Path(__file__).resolve().parent


def atomic(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8') as output:
        os.chmod(temporary, 0o600)
        json.dump(value, output, indent=2)
        output.write('\n'); output.flush(); os.fsync(output.fileno())
    os.replace(temporary, path)
    if os.name == 'posix':
        descriptor = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)


@contextmanager
def lock(directory):
    if os.name != 'posix':
        raise ValueError('fleet execution requires a Linux client Guest')
    import fcntl
    with (directory / '.lock').open('a') as stream:
        try: fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError('fleet run is already active') from None
        try: yield
        finally: fcntl.flock(stream, fcntl.LOCK_UN)


def config(path):
    raw = json.loads(path.read_text())
    required = {'schema_version', 'deployment_config', 'instances'}
    if set(raw) - {'direct_backend_origin', 'wrong_identity'} != required or raw['schema_version'] != 1:
        raise ValueError('invalid fleet business configuration')
    deployment_path = Path(raw['deployment_config'])
    if not deployment_path.is_absolute(): deployment_path = path.parent / deployment_path
    deployment = json.loads(deployment_path.read_text())
    candidates = {c['_instance']: c for c in fleet.validate_fleet(deployment)}
    seen, users, keys, result = set(), set(), set(), []
    if not isinstance(raw['instances'], list) or len(raw['instances']) < 2:
        raise ValueError('at least two independent business instances required')
    for pair_index, item in enumerate(raw['instances']):
        if set(item) - {'agent_id'} != {'name', 'account_id', 'user_id', 'api_key_file'}:
            raise ValueError('invalid business instance fields')
        name = item['name']
        if name not in candidates or name in seen: raise ValueError('missing or duplicate instance')
        for field in ('account_id', 'user_id', 'agent_id'):
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', item.get(field, 'main')):
                raise ValueError('invalid business scope')
        scope = (item['account_id'], item['user_id'])
        if scope in users or item['user_id'].lower() in ('root', 'admin'):
            raise ValueError('distinct non-root user scopes required')
        keyfile = Path(item['api_key_file'])
        if not keyfile.is_absolute(): raise ValueError('api_key_file must be absolute')
        if str(keyfile) in keys: raise ValueError('distinct API key files required')
        seen.add(name); users.add(scope); keys.add(str(keyfile))
        result.append(item | {'deployment': candidates[name], 'agent_id': item.get('agent_id', 'main'), 'pair_index':pair_index})
    if len({i['account_id'] for i in result}) != 1:
        raise ValueError('this private-user experiment requires a shared account')
    return raw, result, hashlib.sha256((json.dumps(raw, sort_keys=True) + json.dumps(deployment, sort_keys=True)).encode()).hexdigest()


def secret(item):
    value = deploy.protected(item['api_key_file'], private=True).read_text().strip()
    if not value or '\n' in value or '\r' in value: raise ValueError('invalid key file')
    return value


def environment(item, key, directory=None, run_id=None):
    c = item['deployment']
    env = os.environ.copy()
    # Do not allow inherited single-instance overrides to redirect a fleet test.
    for name in list(env):
        if name.startswith('DUAL_E2E_') or name in ('OPENCLAW_PLUGIN_DIR', 'OPENCLAW_CONFIG_PATH', 'OPENVIKING_SPIFFE_CONFIG'):
            env.pop(name)
    env.update(OPENCLAW_CONTAINER=c['container_name'], OPENCLAW_USER=f"{c['gateway_uid']}:{c['gateway_gid']}",
               OPENCLAW_CONFIG_PATH='/home/node/.openclaw/openclaw.json',
               OPENVIKING_SPIFFE_CONFIG='/etc/argus-openclaw/client.json', OPENVIKING_API_KEY=key,
               DUAL_E2E_AGENT_ID=item['agent_id'])
    if directory: env['DUAL_E2E_EVIDENCE_DIR'] = str(directory.resolve())
    if run_id: env['DUAL_E2E_RUN_ID'] = run_id
    if os.environ.get('ARGUS_SEED') is not None:
        seed = os.environ['ARGUS_SEED']
        if not re.fullmatch(r'-?[0-9]{1,20}', seed): raise ValueError('invalid experiment seed')
        # Array position is stable across arm-specific renaming and UID mapping.
        # Run/session markers remain unique, so seeded facts cannot recall an old run.
        env['DUAL_E2E_SEED'] = seed + ':client:' + str(item['pair_index'])
    return env


def execute_business(command, env, log, timeout=3600):
    process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        # A shell's timeout must also stop its polling pipeline before releasing
        # the deployment lock. Remote Gateway writes may remain unknown.
        os.killpg(process.pid, signal.SIGTERM)
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)
        return 124


def probe(item, key, **request):
    c = item['deployment']
    spec = {'account_id':item['account_id'], 'user_id':item['user_id'], 'client_spiffe_id':deploy.client_id(c),
            'server_spiffe_id':c.get('server_spiffe_id', deploy.SERVER),
            'key_sha256':hashlib.sha256(key.encode()).hexdigest(), **request}
    cmd = ['docker', 'exec', '-i', '-u', f"{c['gateway_uid']}:{c['gateway_gid']}",
           '-e', 'OPENCLAW_CONFIG_PATH=/home/node/.openclaw/openclaw.json',
           c['container_name'], 'node', '--input-type=module', '-', json.dumps(spec)]
    try:
        response = subprocess.run(cmd, input=(ROOT / 'fleet_probe.mjs').read_text(), capture_output=True,
                                  text=True, env=environment(item, key), timeout=45)
        # Transport audit writes stderr. Only the final structured probe result is persisted.
        return json.loads(response.stdout.splitlines()[-1])
    except (subprocess.SubprocessError, ValueError, IndexError):
        return {'result':'UNKNOWN', 'code':'PROBE_UNAVAILABLE'}


def denial(observation, statuses=(401, 403)):
    if observation.get('result') != 'OBSERVED': return 'UNKNOWN'
    return 'PASS' if observation.get('http_status') in statuses else 'FAIL'


def authenticated(observation):
    return observation.get('result') == 'OBSERVED' and observation.get('http_status') == 200 and observation.get('application_status') == 'ok'


def scope_check(item, key):
    # Pinned OpenViking 0.4.8 /health intentionally returns 200 for invalid keys;
    # resolved fields, plus the mandatory-auth request context, are the oracle.
    health = probe(item, key, route='/health', identity_probe=True)
    context = probe(item, key, route='/api/v1/system/status', identity_probe=True)
    identity = health.get('identity') or {}
    valid = (health.get('http_status') == 200 and identity.get('auth_mode') == 'api_key' and identity.get('account_id') == item['account_id']
             and identity.get('user_id') == item['user_id'] and str(identity.get('role', '')).lower() == 'user'
             and authenticated(context) and (context.get('identity') or {}).get('context_user') == item['user_id'])
    unavailable = any(value.get('result') != 'OBSERVED' for value in (health, context))
    return {'result':'PASS' if valid else 'UNKNOWN' if unavailable else 'FAIL', 'health':health, 'context':context}


def gateway_question(item, key, marker, output):
    if output.exists():
        operation = output / 'operation.json'
        if operation.exists():
            saved = json.loads(operation.read_text())
            if saved.get('result') in ('PASS', 'FAIL'):
                return saved
        return {'result':'UNKNOWN', 'reason':'QUESTION_SUBMISSION_ALREADY_ATTEMPTED'}
    output.mkdir(mode=0o700)
    query = f'项目 {marker} 的校验码是什么？只回答校验码；如果记忆中没有，只回答 UNKNOWN。'
    atomic(output / 'operation.json', {'status':'submission_unknown', 'query_sha256':hashlib.sha256(query.encode()).hexdigest()})
    c = item['deployment']
    command = ['docker', 'exec', '-u', f"{c['gateway_uid']}:{c['gateway_gid']}", '-e', 'OPENCLAW_CONFIG_PATH=/home/node/.openclaw/openclaw.json',
               c['container_name'], 'openclaw', 'agent', '--agent', item['agent_id'], '--session-key',
               'argus-cross-' + uuid.uuid4().hex, '--message', query, '--timeout', '180', '--json']
    try:
        response = subprocess.run(command, env=environment(item, key), capture_output=True, text=True, timeout=210)
        value = json.loads(response.stdout)
        atomic(output / 'response.json', value)
        actual = answer(output / 'response.json')
        result = {'result':'PASS' if actual == 'UNKNOWN' else 'FAIL', 'answer_sha256':hashlib.sha256(actual.encode()).hexdigest()}
        atomic(output / 'operation.json', result)
        return result
    except (subprocess.SubprocessError, ValueError):
        return {'result':'UNKNOWN', 'reason':'QUESTION_RESULT_UNKNOWN'}


def edge_probe(raw, item, key, kind):
    """Dedicated adversarial edge probes; no secret in subprocess arguments."""
    origin = raw.get('direct_backend_origin') if kind == 'direct_backend' else item['deployment']['openviking_origin']
    if not origin or (kind == 'wrong_identity' and not raw.get('wrong_identity')):
        return {'result':'NOT_RUN', 'reason':'EDGE_PROBE_NOT_CONFIGURED'}
    u = urlsplit(origin)
    if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.path not in ('', '/') or u.query or u.fragment:
        raise ValueError('edge probe requires an explicit HTTP(S) origin')
    connection = None
    try:
        if kind == 'wrong_identity':
            identity = raw['wrong_identity']
            if set(identity) != {'cert_file', 'key_file', 'bundle_file', 'server_spiffe_id'}:
                raise ValueError('invalid wrong_identity references')
            for field in ('cert_file', 'key_file', 'bundle_file'):
                deploy.protected(identity[field], private=field == 'key_file')
            context = ssl.create_default_context(cafile=identity['bundle_file'])
            context.check_hostname = False
            context.load_cert_chain(identity['cert_file'], identity['key_file'])
            connection = http.client.HTTPSConnection(u.hostname, u.port or 443, context=context, timeout=10)
            connection.connect()
            sans = connection.sock.getpeercert().get('subjectAltName', ())
            if [v for k, v in sans if k == 'URI'] != [identity['server_spiffe_id']]:
                return {'result':'UNKNOWN', 'reason':'WRONG_SERVER_IDENTITY'}
        elif u.scheme == 'https':
            connection = http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=10)
        else:
            connection = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=10)
        connection.request('GET', '/api/v1/sessions', headers={'X-API-Key':key,
                           'X-OpenViking-Account':item['account_id'], 'X-OpenViking-User':item['user_id']})
        response = connection.getresponse()
        return {'result':('PASS' if response.status == 403 else 'FAIL') if kind == 'wrong_identity' else 'FAIL',
                'http_status':response.status, 'reason':'HTTP_OBSERVED'}
    except ConnectionRefusedError:
        return {'result':'PASS' if kind == 'direct_backend' else 'UNKNOWN', 'reason':'CONNECTION_REFUSED'}
    except (OSError, ssl.SSLError, http.client.HTTPException):
        return {'result':'UNKNOWN', 'reason':'NETWORK_OR_TLS_INCONCLUSIVE'}
    finally:
        if connection: connection.close()


def isolation(raw, items, keys, directory, state):
    rows = []
    def add(case, caller, owner, observed, result):
        rows.append({'case':case, 'caller':caller, 'owner':owner, 'result':result, 'observation':observed})
        state['isolation'] = rows
        atomic(directory / 'result.json', state)
    owned = {}
    for item in items:
        name = item['name']; evidence = directory / name
        if state['instances'].get(name, {}).get('result') != 'PASS':
            add('owner_memory', name, name, {'reason':'OWNER_BUSINESS_NOT_PASSED'}, 'NOT_RUN'); continue
        marker = (evidence / 'marker.txt').read_text().strip()
        fact = (evidence / 'fact.txt').read_text().strip()
        fact_hash = hashlib.sha256(fact.encode()).hexdigest()
        found = probe(item, keys[name], query=marker, fact_sha256=fact_hash)
        uri = None
        if authenticated(found):
            for candidate in found.get('items', []):
                read = probe(item, keys[name], route='/api/v1/content/read?uri=' + quote(candidate['uri'], safe=''), fact_sha256=fact_hash)
                if authenticated(read) and read.get('contains_fact'):
                    uri = candidate['uri']; break
        add('owner_memory', name, name, found, 'PASS' if uri else 'UNKNOWN')
        if uri: owned[name] = (marker, fact_hash, uri)
        for mode in ('missing', 'invalid'):
            observed = probe(item, keys[name], route='/api/v1/sessions', key_mode=mode)
            add(mode + '_key', name, name, observed, denial(observed))
    for caller in items:
        for owner in items:
            name, victim = caller['name'], owner['name']
            if name == victim: continue
            if victim not in owned:
                add('cross_user', name, victim, {'reason':'OWNER_MEMORY_NOT_OBSERVED'}, 'NOT_RUN'); continue
            marker, fact_hash, uri = owned[victim]
            for forged in (False, True):
                observed = probe(caller, keys[name], route='/api/v1/content/read?uri=' + quote(uri, safe=''),
                                 fact_sha256=fact_hash, **({'forged_user':owner['user_id']} if forged else {}))
                add('forged_user_read' if forged else 'cross_read', name, victim, observed, denial(observed, (403, 404)))
            observed = probe(caller, keys[name], route='/api/v1/system/status', identity_probe=True, forged_user=owner['user_id'])
            safe = authenticated(observed) and (observed.get('identity') or {}).get('context_user') == caller['user_id']
            add('forged_user_context', name, victim, observed, 'PASS' if safe else denial(observed, (403,)))
            observed = probe(caller, keys[name], query=marker, target_uri=uri, fact_sha256=fact_hash)
            result = ('PASS' if not observed.get('contains_fact') and not any(v['uri'] == uri for v in observed.get('items', [])) else 'FAIL') if authenticated(observed) else denial(observed, (403, 404))
            add('cross_search', name, victim, observed, result)
            qa = gateway_question(caller, keys[name], marker, directory / f'cross-{name}-{victim}')
            add('cross_question', name, victim, qa, qa['result'])
    for kind in ('wrong_identity', 'direct_backend'):
        observed = edge_probe(raw, items[0], keys[items[0]['name']], kind)
        add(kind, items[0]['name'], None, observed, observed['result'])


def aggregate(state):
    values = [v['result'] for v in state['instances'].values()] + [v['result'] for v in state['isolation']]
    return 'FAIL' if 'FAIL' in values else 'UNKNOWN' if 'UNKNOWN' in values else 'NOT_RUN' if not values or 'NOT_RUN' in values else 'PASS'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('run', 'resume', 'isolation'))
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--instance')
    a = p.parse_args()
    raw, items, digest = config(a.config)
    if a.instance:
        items = [i for i in items if i['name'] == a.instance]
        if not items: raise ValueError('unknown business instance')
    keys = {i['name']:secret(i) for i in items}
    if len(set(keys.values())) != len(keys): raise ValueError('distinct actual API keys required')
    if a.action == 'run': a.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    elif not a.output.is_dir(): raise ValueError('resume/isolation requires existing evidence directory')
    with lock(a.output):
        if a.action == 'run':
            run_id = os.environ.get('ARGUS_RUN_ID', uuid.uuid4().hex)
            operation_id = os.environ.get('ARGUS_OPERATION_ID')
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,160}', run_id) or (operation_id and not re.fullmatch(r'[A-Za-z0-9_.-]{1,160}', operation_id)):
                raise ValueError('invalid experiment correlation ID')
            state = {'schema_version':1, 'config_sha256':digest, 'run_id':run_id, 'operation_id':operation_id,
                     'pair_seed':os.environ.get('ARGUS_SEED'),
                     'scope':'shared-node independent gateways', 'instances':{}, 'isolation':[], 'result':'NOT_RUN',
                     'completed':False, 'phase':'business'}
            atomic(a.output / 'result.json', state)
        else:
            state = json.loads((a.output / 'result.json').read_text())
            if state['config_sha256'] != digest: raise ValueError('configuration changed; refusing resume')
            if state.get('pair_seed') != os.environ.get('ARGUS_SEED'): raise ValueError('paired experiment seed changed')
            for key, env in (('run_id', 'ARGUS_RUN_ID'), ('operation_id', 'ARGUS_OPERATION_ID')):
                if os.environ.get(env) and os.environ[env] != state.get(key): raise ValueError('experiment correlation changed')
            state.update(completed=False, phase='business' if a.action != 'isolation' else 'isolation')
            atomic(a.output / 'result.json', state)
        if a.action != 'isolation':
            for item in items:
                name = item['name']; directory = a.output / name
                previous = state['instances'].get(name)
                if previous and previous['result'] == 'PASS': continue
                if previous and previous.get('code') in ('RESOLVED_SCOPE_MISMATCH', 'RESOLVED_SCOPE_UNAVAILABLE'):
                    previous = None
                check = scope_check(item, keys[name])
                if check['result'] != 'PASS':
                    # A read-only preflight cannot erase an earlier submission
                    # intent. Keep its resume/unknown-write boundary intact.
                    state['instances'][name] = (previous | {'scope_check':check}) if previous else {
                        'result':check['result'], 'code':'RESOLVED_SCOPE_UNAVAILABLE' if check['result'] == 'UNKNOWN'
                        else 'RESOLVED_SCOPE_MISMATCH', 'scope_check':check}
                    atomic(a.output / 'result.json', state); continue
                command = ['bash', str(ROOT.parent / 'scripts/verify_openclaw_plugin_e2e.sh')]
                if previous:
                    if not (directory / 'processing-events.jsonl').exists() or not (directory / 'run.json').exists():
                        state['instances'][name] = {'result':'UNKNOWN', 'code':'INITIAL_WRITE_UNKNOWN', 'evidence_dir':str(directory)}
                        atomic(a.output / 'result.json', state); continue
                    command += ['--resume', str(directory.resolve())]
                # Persist intent before any mutating Gateway call.
                state['instances'][name] = {'result':'UNKNOWN', 'code':'SUBMISSION_IN_PROGRESS', 'evidence_dir':str(directory)}
                atomic(a.output / 'result.json', state)
                with (a.output / (name + '.log')).open('a', encoding='utf-8') as log:
                    execute_business(command, environment(item, keys[name], directory, state['run_id'] + '-' + name), log)
                if (directory / 'result.json').exists():
                    result = json.loads((directory / 'result.json').read_text())
                    state['instances'][name] = result | {'evidence_dir':str(directory)}
                atomic(a.output / 'result.json', state)
        if not a.instance:
            state['phase'] = 'isolation'
            atomic(a.output / 'result.json', state)
            isolation(raw, items, keys, a.output, state)
        elif not state['isolation']:
            state['isolation'] = [{'case':'fleet_isolation', 'result':'NOT_RUN', 'reason':'SINGLE_INSTANCE_SELECTION'}]
        state['result'] = aggregate(state)
        state.update(completed=True, phase='completed')
        atomic(a.output / 'result.json', state)
        print(json.dumps(state, indent=2))
        return 0 if state['result'] == 'PASS' else 1


if __name__ == '__main__':
    try: raise SystemExit(main())
    except (ValueError, OSError, KeyError) as error:
        print(json.dumps({'result':'UNKNOWN', 'code':'FLEET_SETUP_FAILED', 'reason':str(error)}))
        raise SystemExit(1)
