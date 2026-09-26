#!/usr/bin/env python3
"""Render locally; install/register in the OpenClaw Guest; Entries on IP1.

No SSH, TD Quote, Trustee changes, CA replacement or automatic container restart.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
ETC = Path('/etc/argus-openclaw')
RUN = Path('/run/argus-openclaw')
BIN = Path('/opt/argus-workload/bin/spiffe-client-credentials')
SPIRE = Path('/opt/spire-1.15.3/bin')
CLIENT = 'spiffe://argus.local/agent/openclaw'
HELPER = 'spiffe://argus.local/infra/openclaw-helper'
SERVER = 'spiffe://argus.local/service/openviking-cmem'
LABEL = 'org.argus.workload'
INSTANCE_LABEL = 'org.argus.instance'
UNIT = 'argus-openclaw-credentials'


def paths(c):
    name = c.get('_instance')
    return {'etc': ETC / 'instances' / name if name else ETC,
            'run': RUN / 'instances' / name if name else RUN,
            'home': Path('/var/lib/argus-openclaw') / 'instances' / name / 'home' if name else Path('/var/lib/argus-openclaw/home'),
            'unit': f'argus-openclaw-credentials-{name}' if name else UNIT,
            'group': f'argus-oc-{name}' if name else 'argus-openclaw'}


def client_id(c):
    return c.get('_client_id', CLIENT)


def run(argv, timeout=30):
    result = subprocess.run([str(v) for v in argv], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        # Do not print command arguments or Docker inspect environment secrets.
        raise ValueError(f'{Path(argv[0]).name} failed ({result.returncode}): {result.stderr.strip()}')
    return result.stdout.strip()


def validate(c):
    required = {'server_address', 'server_port', 'node_certificate_sha1', 'openclaw_image',
                'image_config_digest', 'helper_sha256', 'openviking_origin', 'container_name',
                'gateway_uid', 'gateway_gid', 'reader_gid', 'gateway_executable', 'gateway_command',
                'server_socket', 'server_unit'}
    if set(c) - {'_instance', '_client_id', 'server_spiffe_id'} != required:
        raise ValueError(f'configuration keys differ: {sorted(set(c) ^ required)}')
    if ('_instance' in c) != ('_client_id' in c):
        raise ValueError('incomplete instance selection')
    if '_instance' in c and (not re.fullmatch(r'[a-z][a-z0-9-]{0,22}', c['_instance'])
                            or not re.fullmatch(r'spiffe://argus\.local/agent/[a-zA-Z0-9/_-]+', c['_client_id'])):
        raise ValueError('invalid instance identity')
    if 'server_spiffe_id' in c and not re.fullmatch(r'spiffe://argus\.local/service/[a-zA-Z0-9/_-]+', c['server_spiffe_id']):
        raise ValueError('invalid server_spiffe_id')
    for key, pattern in [('node_certificate_sha1', r'[0-9a-f]{40}'), ('helper_sha256', r'[0-9a-f]{64}'),
                         ('image_config_digest', r'sha256:[0-9a-f]{64}'),
                         ('openclaw_image', r'[A-Za-z0-9][A-Za-z0-9./:_-]*@sha256:[0-9a-f]{64}'),
                         ('container_name', r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}'),
                         ('server_address', r'[A-Za-z0-9][A-Za-z0-9.:-]*'),
                         ('server_unit', r'[A-Za-z0-9_.@-]+')]:
        if not re.fullmatch(pattern, c.get(key, '')):
            raise ValueError(f'invalid or unfilled {key}')
    for key in ('gateway_uid', 'gateway_gid', 'reader_gid', 'server_port'):
        if type(c[key]) is not int or not 0 < c[key] <= (65535 if key == 'server_port' else 2147483647):
            raise ValueError(f'{key} must be a positive integer')
    if c['reader_gid'] in (c['gateway_uid'], c['gateway_gid']):
        raise ValueError('reader_gid must be a dedicated supplementary group')
    for key in ('gateway_executable', 'server_socket'):
        if not re.fullmatch(r'/[A-Za-z0-9_/.-]+', c[key]) or '..' in c[key].split('/'):
            raise ValueError(f'invalid {key}')
    cmd = c['gateway_command']
    if not isinstance(cmd, list) or not cmd or not all(isinstance(v, str) and v and '\x00' not in v for v in cmd):
        raise ValueError('gateway_command must be an explicit argv array')
    u = urlsplit(c['openviking_origin'])
    if u.scheme != 'https' or not u.hostname or u.username or u.path not in ('', '/') or u.query or u.fragment:
        raise ValueError('openviking_origin must be an HTTPS origin reachable INSIDE the container')
    return c


def agent_id(c):
    return 'spiffe://argus.local/spire/agent/x509pop/' + c['node_certificate_sha1']


def selectors(c, identity):
    if identity == HELPER:
        return {'unix:uid:0', 'unix:path:' + BIN.as_posix(), 'unix:sha256:' + c['helper_sha256']}
    result = {'unix:uid:' + str(c['gateway_uid']), 'unix:path:' + c['gateway_executable'],
            'docker:image_config_digest:' + c['image_config_digest'], 'docker:label:' + LABEL + ':openclaw'}
    if c.get('_instance'):
        result.add('docker:label:' + INSTANCE_LABEL + ':' + c['_instance'])
    return result


def render(c, destination):
    validate(c)
    destination.mkdir(parents=True, exist_ok=False)
    q = json.dumps
    credentials = json.loads((ROOT / 'config/credentials.example.json').read_text())
    credentials.update(agent_spiffe_id=agent_id(c), reader_gid=c['reader_gid'])
    client = json.loads((ROOT / 'config/client.example.json').read_text())
    client['origin'] = c['openviking_origin'].rstrip('/')
    client['serverSpiffeId'] = c.get('server_spiffe_id', SERVER)
    agent = f'''agent {{
    data_dir = "/var/lib/argus-openclaw/spire"
    log_level = "INFO"
    server_address = {q(c['server_address'])}
    server_port = {c['server_port']}
    trust_domain = "argus.local"
    trust_bundle_path = "/etc/argus-openclaw/bootstrap-bundle.pem"
    socket_path = "/run/spire/openclaw/agent.sock"
    experimental {{
        broker {{
            socket_path = "/run/spire/openclaw-broker/broker.sock"
            brokers = [{{
                id = "{HELPER}"
                allowed_reference_types = [{{ type_url = "type.googleapis.com/spiffe.broker.WorkloadPIDReference" }}]
            }}]
        }}
    }}
}}
plugins {{
    NodeAttestor "x509pop" {{
        plugin_data {{
            private_key_path = "/etc/argus-openclaw/node/key.pem"
            certificate_path = "/etc/argus-openclaw/node/cert.pem"
        }}
    }}
    KeyManager "disk" {{ plugin_data {{ directory = "/var/lib/argus-openclaw/spire/keys" }} }}
    WorkloadAttestor "unix" {{ plugin_data {{ discover_workload_path = true }} }}
    WorkloadAttestor "docker" {{ plugin_data {{ docker_socket_path = "unix:///var/run/docker.sock" }} }}
}}
'''
    compose = {'name': 'argus-openclaw', 'services': {'gateway': {
        'image': c['openclaw_image'], 'container_name': c['container_name'],
        'user': f"{c['gateway_uid']}:{c['gateway_gid']}", 'group_add': [str(c['reader_gid'])],
        'init': True, 'restart': 'no', 'entrypoint': c['gateway_command'], 'command': [],
        'labels': {LABEL: 'openclaw'}, 'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
        'environment': {'HOME': '/home/node', 'OPENCLAW_CONFIG_PATH': '/home/node/.openclaw/openclaw.json',
                        'OPENVIKING_SPIFFE_CONFIG': '/etc/argus-openclaw/client.json'},
        'env_file': ['/etc/argus-openclaw/gateway.env'],
        'volumes': [
            '/var/lib/argus-openclaw/home:/home/node/.openclaw',
            '/etc/argus-openclaw/client.json:/etc/argus-openclaw/client.json:ro',
            '/run/argus-openclaw/credentials:/run/argus-openclaw/credentials:ro'],
        'tmpfs': ['/tmp:rw,nosuid,nodev,mode=1777'],
    }}}
    files = {'environment.json': q(c, indent=2), 'credentials.json': q(credentials, indent=2),
             'client.json': q(client, indent=2), 'compose.json': q(compose, indent=2), 'agent.conf': agent,
             'server-x509pop.fragment.hcl': '''# Merge this block INSIDE the existing server plugins block.
# Preserve existing argus_tdx, CA, datastore, bind and upstream settings.
NodeAttestor "x509pop" {
    plugin_data {
        mode = "external_pki"
        ca_bundle_path = "/etc/argus-openclaw/x509pop-ca.pem"
    }
}
''', 'argus-openclaw-agent.service': '''[Unit]
Description=OpenClaw Guest SPIRE Agent (x509pop; TDX remote attestation deferred)
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service
[Service]
Type=simple
User=root
RuntimeDirectory=spire/openclaw spire/openclaw-broker
RuntimeDirectoryMode=0700
StateDirectory=argus-openclaw/spire
ExecStart=/opt/spire-1.15.3/bin/spire-agent run -config /etc/argus-openclaw/agent.conf
Restart=on-failure
RestartSec=2
UMask=0077
[Install]
WantedBy=multi-user.target
''', 'argus-openclaw-credentials.service': (ROOT / 'config/argus-openclaw-credentials.service').read_text()}
    files['argus-openclaw-credentials.service'] = files['argus-openclaw-credentials.service'].replace(
        'After=network.target', 'After=argus-openclaw-agent.service\nRequires=argus-openclaw-agent.service')
    if c.get('_instance'):
        layout = paths(c)
        credentials.update(target_spiffe_id=client_id(c), target_registration_path=(layout['run'] / 'target.json').as_posix(),
                           credentials_dir=(layout['run'] / 'credentials').as_posix())
        client['clientSpiffeId'] = client_id(c)
        gateway = compose['services']['gateway']
        compose['name'] += '-' + c['_instance']
        gateway['labels'][INSTANCE_LABEL] = c['_instance']
        gateway['env_file'] = [(layout['etc'] / 'gateway.env').as_posix()]
        gateway['volumes'] = [layout['home'].as_posix() + ':/home/node/.openclaw',
                              (layout['etc'] / 'client.json').as_posix() + ':/etc/argus-openclaw/client.json:ro',
                              (layout['run'] / 'credentials').as_posix() + ':/run/argus-openclaw/credentials:ro']
        files.update({'credentials.json': q(credentials, indent=2), 'client.json': q(client, indent=2),
                      'compose.json': q(compose, indent=2)})
        files['private-memory.fragment.json'] = q({'plugins': {'entries': {'openviking': {'config': {
            'mode': 'remote', 'baseUrl': client['origin'], 'apiKey': '${OPENVIKING_API_KEY}',
            # Pinned upstream resolves apiKey interpolation, but account/user
            # use environment fallback only when these fields are empty.
            'accountId': '', 'userId': '',
            'recallResources': False, 'recallTargetTypes': ['user'], 'autoCapture': True, 'autoRecall': True,
        }}}}}, indent=2)
        unit = files.pop('argus-openclaw-credentials.service')
        unit = unit.replace('/etc/argus-openclaw/credentials.json', (layout['etc'] / 'credentials.json').as_posix())
        unit = unit.replace('/run/argus-openclaw', layout['run'].as_posix())
        unit = unit.replace('RuntimeDirectory=argus-openclaw', 'RuntimeDirectory=argus-openclaw/instances/' + c['_instance'])
        unit = unit.replace('Group=argus-openclaw', 'Group=' + layout['group'])
        files[layout['unit'] + '.service'] = unit
        # Shared node lifecycle is deliberately separate from per-instance install.
        files.pop('agent.conf')
        files.pop('argus-openclaw-agent.service')
        files.pop('server-x509pop.fragment.hcl')
    files['entries.json'] = q({'parent_id': agent_id(c), 'tdx_remote_attestation': 'NOT_RUN',
                              'entries': {sid: sorted(selectors(c, sid)) for sid in (HELPER, client_id(c))}}, indent=2)
    for name, data in files.items():
        (destination / name).write_bytes((data.rstrip() + '\n').encode('utf-8'))
    hashes = {name: hashlib.sha256((destination / name).read_bytes()).hexdigest() for name in sorted(files)}
    (destination / 'SHA256SUMS').write_bytes(''.join(f'{digest}  {name}\n' for name, digest in hashes.items()).encode())
    return {'rendered': str(destination.resolve()), 'agent_id': agent_id(c), 'tdx_remote_attestation': 'NOT_RUN'}


def protected(path, private=False):
    path = Path(path)
    for item in [path, *path.parents]:
        s = item.lstat()
        if stat.S_ISLNK(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
            raise ValueError(f'unprotected path: {item}')
    if not path.is_file() or (private and path.stat().st_mode & 0o077):
        raise ValueError(f'protected regular file required: {path}')
    return path


def version(binary):
    r = subprocess.run([str(binary), '-version'], capture_output=True, text=True, check=True, timeout=10)
    if (r.stdout + r.stderr).strip() != '1.15.3':
        raise ValueError(f'{binary}: expected SPIRE 1.15.3')


def guest_install(c, source):
    layout = paths(c)
    etc, runtime = layout['etc'], layout['run']
    if etc.exists() and (etc / 'environment.json').exists():
        raise ValueError('deployment already installed; review changes explicitly before replacing configuration')
    version(SPIRE / 'spire-agent')
    if hashlib.sha256(protected(BIN).read_bytes()).hexdigest() != c['helper_sha256']:
        raise ValueError('Helper binary digest mismatch')
    for file in ('bootstrap-bundle.pem', 'node/cert.pem', 'node/key.pem'):
        protected(ETC / file, private=file in ('node/key.pem', 'gateway.env'))
    protected(etc / 'gateway.env', private=True)
    if c.get('_instance'):
        protected(ETC / 'agent.conf')
    fingerprint = run(['openssl', 'x509', '-in', ETC / 'node/cert.pem', '-noout', '-fingerprint', '-sha1']).split('=')[-1].replace(':', '').lower()
    if fingerprint != c['node_certificate_sha1']:
        raise ValueError('node certificate fingerprint mismatch')
    run(['openssl', 'x509', '-in', ETC / 'node/cert.pem', '-checkend', '3600', '-noout'])
    cert_key = run(['openssl', 'x509', '-in', ETC / 'node/cert.pem', '-pubkey', '-noout'])
    if cert_key != run(['openssl', 'pkey', '-in', ETC / 'node/key.pem', '-pubout']):
        raise ValueError('node private key does not match certificate')
    if 'Digital Signature' not in run(['openssl', 'x509', '-in', ETC / 'node/cert.pem', '-noout', '-ext', 'keyUsage']):
        raise ValueError('node certificate requires digitalSignature key usage')
    image = json.loads(run(['docker', 'image', 'inspect', c['openclaw_image']]))[0]
    if image['Id'] != c['image_config_digest']:
        raise ValueError('image config digest mismatch; manifest digest is a different value')
    if json.loads(run(['docker', 'info', '--format', '{{json .SecurityOptions}}'])) and 'userns' in run(['docker', 'info', '--format', '{{json .SecurityOptions}}']):
        raise ValueError('this profile requires ordinary rootful Docker without user namespace remapping')
    import grp
    try:
        group = grp.getgrnam(layout['group'])
        if group.gr_gid != c['reader_gid']:
            raise ValueError('existing argus-openclaw GID differs')
    except KeyError:
        try:
            grp.getgrgid(c['reader_gid'])
        except KeyError:
            run(['groupadd', '--gid', str(c['reader_gid']), layout['group']])
        else:
            raise ValueError('reader GID already belongs to another group')
    for path, mode, uid, gid in [(runtime, 0o750, 0, c['reader_gid']), (runtime / 'credentials', 0o750, 0, c['reader_gid']),
                                  (layout['home'], 0o700, c['gateway_uid'], c['gateway_gid'])]:
        path.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise ValueError(f'no symlink allowed: {path}')
        path.chmod(mode)
        os.chown(path, uid, gid)
    if run(['findmnt', '-n', '-o', 'FSTYPE', '-T', runtime / 'credentials']) != 'tmpfs':
        raise ValueError('credentials must be on tmpfs')
    # Re-render from validated inputs and compare every byte before installation.
    import tempfile
    with tempfile.TemporaryDirectory() as temporary:
        generated = Path(temporary) / 'rendered'
        render(c, generated)
        for file in generated.iterdir():
            if not (source / file.name).is_file() or (source / file.name).read_bytes() != file.read_bytes():
                raise ValueError(f'rendered deployment changed: {file.name}')
        names = ['credentials.json', 'client.json', 'compose.json', 'environment.json']
        if not c.get('_instance'):
            names.append('agent.conf')
        for name in names:
            shutil.copyfile(generated / name, etc / name)
            (etc / name).chmod(0o644 if name == 'client.json' else 0o600)
        units = [layout['unit'] + '.service']
        if not c.get('_instance'):
            units.append('argus-openclaw-agent.service')
        for name in units:
            shutil.copyfile(generated / name, Path('/etc/systemd/system') / name)
    run(['systemctl', 'daemon-reload'])
    return {'installed': str(etc), 'started': False, 'tdx_remote_attestation': 'NOT_RUN'}


def check_gateway(c, pid):
    info = json.loads(run(['docker', 'inspect', c['container_name']]))[0]
    if not info['State']['Running'] or info['Image'] != c['image_config_digest'] or info['Config'].get('Labels', {}).get(LABEL) != 'openclaw':
        raise ValueError('Gateway container is not the pinned running workload')
    if c.get('_instance'):
        from fleet import check_mounts
        if info['Config'].get('Labels', {}).get(INSTANCE_LABEL) != c['_instance']:
            raise ValueError('Gateway instance label differs from Entry selector')
        check_mounts(info, c)
    rows = run(['docker', 'top', c['container_name'], '-eo', 'pid,args']).splitlines()[1:]
    line = next((line for line in rows if line.split(None, 1)[0] == str(pid)), '')
    if not line or not re.search(r'openclaw[- ]gateway|openclaw\S*.*\bgateway\b', line):
        raise ValueError('PID must be the actual Gateway from docker top, not init or docker exec')
    if os.readlink(f'/proc/{pid}/exe') != c['gateway_executable']:
        raise ValueError('Gateway executable differs from Entry selector')
    status = Path(f'/proc/{pid}/status').read_text()
    if int(re.search(r'^Uid:\s+(\d+)', status, re.M)[1]) != c['gateway_uid']:
        raise ValueError('Gateway host UID differs from Entry selector')
    if c.get('_instance'):
        groups = {int(v) for v in re.search(r'^Groups:\s*([^\n]*)', status, re.M)[1].split()}
        if (int(re.search(r'^Gid:\s+(\d+)', status, re.M)[1]) != c['gateway_gid']
                or c['reader_gid'] not in groups or groups - {c['reader_gid'], c['gateway_gid']}):
            raise ValueError('Gateway runtime GID/groups differ from isolated deployment')
    return {'container_id': info['Id'], 'gateway_pid': pid, 'image_config_digest': info['Image']}


def guest_register(c, pid):
    layout = paths(c)
    record = check_gateway(c, pid)
    main_pid = run(['systemctl', 'show', 'argus-openclaw-agent', '--property=MainPID', '--value'])
    if not main_pid.isdigit() or int(main_pid) < 1:
        raise ValueError('Guest Agent is not running')
    version(Path('/proc') / main_pid / 'exe')
    if hashlib.sha256(protected(BIN).read_bytes()).hexdigest() != c['helper_sha256']:
        raise ValueError('Helper binary changed; update and audit its Entry')
    run([BIN, '-config', layout['etc'] / 'credentials.json', '-register-pid', str(pid)])
    check_gateway(c, pid)
    run(['systemctl', 'start', layout['unit']])
    deadline = time.monotonic() + 65
    while time.monotonic() < deadline:
        ready = guest_status(c)
        if ready['ready']:
            return {**record, **ready}
        time.sleep(0.5)
    run(['systemctl', 'stop', layout['unit']])
    raise ValueError('no valid credential lease in 65s; inspect Agent/Helper journals and Entries')


def guest_status(c):
    try:
        lease = json.loads((paths(c)['run'] / 'credentials/ready.json').read_text())
        ready = time.time() * 1000 < lease['lease_until'] <= time.time() * 1000 + 3000
    except (OSError, ValueError, KeyError):
        lease, ready = {}, False
    return {'ready': ready, 'lease': lease, 'agent_id': agent_id(c), 'tdx_remote_attestation': 'NOT_RUN'}


def guest_stop(c):
    layout = paths(c)
    run(['systemctl', 'stop', layout['unit']])
    run([BIN, '-config', layout['etc'] / 'credentials.json', '-clear'])
    (layout['run'] / 'target.json').unlink(missing_ok=True)
    return {'publisher_stopped': True, 'registration_removed': True, 'container_stopped': False}


def id_string(value):
    if isinstance(value, str):
        return value
    return 'spiffe://' + value.get('trust_domain', value.get('trustDomain', '')) + value.get('path', '')


def audit(entries, c, identity):
    if not entries:
        raise ValueError(f'missing Entry: {identity}')
    for e in entries:
        have = {s['type'] + ':' + s['value'] for s in e.get('selectors', [])}
        attr = e.get('additional_attributes', e.get('additionalAttributes', {}))
        if (id_string(e.get('spiffe_id', e.get('spiffeId', {}))) != identity
                or id_string(e.get('parent_id', e.get('parentId', {}))) != agent_id(c)
                or have != selectors(c, identity)
                or e.get('admin') or e.get('downstream') or e.get('store_svid', e.get('storeSvid'))
                or (identity == client_id(c) and not attr.get('disable_x509_svid_prefetch', attr.get('disableX509SvidPrefetch', False)))):
            raise ValueError(f'conflicting Entry {e.get("id")} for {identity}; review it explicitly')


def server_entries(c, apply=False):
    version(SPIRE / 'spire-server')
    pid = run(['systemctl', 'show', c['server_unit'], '--property=MainPID', '--value'])
    if not pid.isdigit() or int(pid) < 1:
        raise ValueError('SPIRE Server is not running')
    version(Path('/proc') / pid / 'exe')
    base = [SPIRE / 'spire-server']
    socket = ['-socketPath', c['server_socket']]
    agents = json.loads(run(base + ['agent', 'list', *socket, '-output', 'json'])).get('agents', [])
    if not any(id_string(a.get('id', {})) == agent_id(c) and not a.get('banned') and a.get('attestation_type', a.get('attestationType')) == 'x509pop' for a in agents):
        raise ValueError('expected x509pop Agent is not enrolled; verify node ID on the running Server')
    def read(identity):
        return json.loads(run(base + ['entry', 'show', *socket, '-spiffeID', identity, '-output', 'json'])).get('entries', [])
    current = {sid: read(sid) for sid in (HELPER, client_id(c))}
    if c.get('_instance'):
        all_entries = json.loads(run(base + ['entry', 'show', *socket, '-output', 'json'])).get('entries', [])
        expected = selectors(c, client_id(c))
        for entry in all_entries:
            have = {s['type'] + ':' + s['value'] for s in entry.get('selectors', [])}
            if (id_string(entry.get('parent_id', entry.get('parentId', {}))) == agent_id(c)
                    and have and have <= expected
                    and id_string(entry.get('spiffe_id', entry.get('spiffeId', {}))) != client_id(c)):
                raise ValueError('another Entry also matches this Gateway; remove overlapping authorization explicitly')
    # Audit all conflicting same-ID Entries before the first mutation.
    for sid, entries in current.items():
        if entries or not apply:
            audit(entries, c, sid)
    for sid, entries in current.items():
        if not entries:
            cmd = base + ['entry', 'create', *socket, '-parentID', agent_id(c), '-spiffeID', sid, '-x509SVIDTTL', '300']
            if sid == client_id(c):
                cmd += ['-disableX509SVIDPrefetch']
            for selector in sorted(selectors(c, sid)):
                cmd += ['-selector', selector]
            run(cmd)
        current[sid] = read(sid)
        audit(current[sid], c, sid)
    return {'agent_id': agent_id(c), 'entries': current, 'tdx_remote_attestation': 'NOT_RUN'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['render', 'render-node', 'guest-install-node', 'guest-install', 'guest-register', 'guest-status', 'guest-stop', 'server-check', 'apply-entries'])
    p.add_argument('--instance', help='required for an instance action on a fleet configuration')
    p.add_argument('--config', type=Path, default=ETC / 'environment.json')
    p.add_argument('--output', type=Path)
    p.add_argument('--source', type=Path, help='rendered deployment directory for guest-install')
    p.add_argument('--pid', type=int, help='actual Gateway PID in the Guest host namespace')
    args = p.parse_args()
    if args.action not in ('render', 'render-node'):
        if sys.platform != 'linux' or os.geteuid() != 0:
            raise ValueError('run this action as root on its designated Linux role')
        protected(args.config)
    from fleet import select, render_node, install_node
    raw = json.loads(args.config.read_text())
    if args.action in ('render-node', 'guest-install-node'):
        result = render_node(raw, args.output) if args.action == 'render-node' else install_node(raw, args.source)
        print(json.dumps(result, indent=2))
        return
    c = select(raw, args.instance)
    if args.action == 'render':
        if not args.output:
            raise ValueError('--output is required (new directory)')
        result = render(c, args.output)
    elif args.action == 'guest-install':
        if not args.source:
            raise ValueError('--source is required')
        result = guest_install(c, args.source)
    elif args.action == 'guest-register':
        if not args.pid or args.pid <= 0:
            raise ValueError('--pid is required')
        result = guest_register(c, args.pid)
    else:
        result = {'guest-status': guest_status, 'guest-stop': guest_stop,
                  'server-check': server_entries, 'apply-entries': lambda c: server_entries(c, True)}[args.action](c)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        print(f'OPENCLAW_DEPLOY=FAIL: {error}', file=sys.stderr)
        sys.exit(1)
