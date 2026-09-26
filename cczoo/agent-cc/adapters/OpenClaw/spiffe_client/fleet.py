"""Strict fleet selection; a shared trusted publisher and node, isolated Gateways."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

import deploy

NODE_KEYS = {'server_address', 'server_port', 'node_certificate_sha1', 'helper_sha256', 'server_socket', 'server_unit'}
INSTANCE_KEYS = {'name', 'client_spiffe_id', 'openclaw_image', 'image_config_digest', 'openviking_origin',
                 'container_name', 'gateway_uid', 'gateway_gid', 'reader_gid', 'gateway_executable', 'gateway_command'}


def validate_fleet(raw):
    if set(raw) != {'schema_version', 'node', 'instances'} or raw['schema_version'] != 2:
        raise ValueError('fleet requires schema_version=2, node and instances')
    if not isinstance(raw['node'], dict) or set(raw['node']) - {'server_spiffe_id'} != NODE_KEYS:
        raise ValueError('fleet node keys differ')
    if not isinstance(raw['instances'], list) or not raw['instances']:
        raise ValueError('fleet instances must be nonempty')
    seen = {key: set() for key in ('name', 'client_spiffe_id', 'container_name', 'gateway_uid')}
    groups = set()
    selected = []
    for item in raw['instances']:
        if not isinstance(item, dict) or set(item) != INSTANCE_KEYS:
            raise ValueError('fleet instance keys differ')
        c = dict(raw['node']) | {k: v for k, v in item.items() if k not in ('name', 'client_spiffe_id')}
        c.update(_instance=item['name'], _client_id=item['client_spiffe_id'])
        deploy.validate(c)
        for key, values in seen.items():
            if item[key] in values:
                raise ValueError(f'duplicate fleet {key}')
            values.add(item[key])
        # Linux UIDs and GIDs are separate namespaces. Both kinds of GID must be
        # unique across instances, otherwise one Gateway can read another lease.
        current_groups = {item['gateway_gid'], item['reader_gid']}
        if current_groups & groups:
            raise ValueError('fleet gateway/reader GIDs must be disjoint')
        groups |= current_groups
        selected.append(c)
    return selected


def select(raw, instance=None):
    if 'instances' not in raw:
        c = deploy.validate(raw)
        if instance and instance != c.get('_instance', 'default'):
            raise ValueError('selected instance is absent from configuration')
        return c
    candidates = validate_fleet(raw)
    if not instance:
        raise ValueError('--instance is required for a fleet operation')
    for c in candidates:
        if c['_instance'] == instance:
            return c
    raise ValueError('unknown instance')


def check_mounts(info, c):
    layout = deploy.paths(c)
    expected = {
        '/home/node/.openclaw': (layout['home'].as_posix(), True),
        '/etc/argus-openclaw/client.json': ((layout['etc'] / 'client.json').as_posix(), False),
        '/run/argus-openclaw/credentials': ((layout['run'] / 'credentials').as_posix(), False),
    }
    mounts = info.get('Mounts', [])
    actual = {m['Destination']: m for m in mounts if m.get('Type') != 'tmpfs'}
    if set(actual) != set(expected):
        raise ValueError('Gateway mounts must contain only its own declared config, data and credentials')
    for dest, (source, writable) in expected.items():
        m = actual[dest]
        if m.get('Type') != 'bind' or m.get('Source') != source or m.get('RW') is not writable:
            raise ValueError('Gateway mount differs from isolated deployment')
    host = info.get('HostConfig', {})
    if (host.get('Privileged') or host.get('PidMode') == 'host' or host.get('NetworkMode') == 'host'
            or host.get('CapAdd') or set(map(str, host.get('GroupAdd') or [])) != {str(c['reader_gid'])}):
        raise ValueError('Gateway isolation/group settings differ')
    if info.get('Config', {}).get('User') != f"{c['gateway_uid']}:{c['gateway_gid']}":
        raise ValueError('Gateway configured UID/GID differs')


def render_node(raw, destination):
    candidates = validate_fleet(raw)
    if destination is None:
        raise ValueError('--output is required')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    legacy = {k: v for k, v in candidates[0].items() if not k.startswith('_')}
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / 'render'
        deploy.render(legacy, base)
        for name in ('agent.conf', 'argus-openclaw-agent.service', 'server-x509pop.fragment.hcl'):
            shutil.copyfile(base / name, destination / name)
    # Node manifest intentionally contains no per-Gateway inputs or secrets.
    (destination / 'node.json').write_text(json.dumps({key:value for key,value in raw['node'].items()
                                                    if key in NODE_KEYS}, indent=2) + '\n')
    names = sorted(p.name for p in destination.iterdir())
    (destination / 'SHA256SUMS').write_text(''.join(
        f'{hashlib.sha256((destination / name).read_bytes()).hexdigest()}  {name}\n' for name in names))
    return {'rendered': str(destination), 'scope': 'shared_node', 'helper_id': deploy.HELPER,
            'client_instances': len(candidates), 'tdx_remote_attestation': 'NOT_RUN'}


def install_node(raw, source):
    c = validate_fleet(raw)[0]
    if source is None:
        raise ValueError('--source is required')
    source = Path(source)
    deploy.version(deploy.SPIRE / 'spire-agent')
    for name in ('bootstrap-bundle.pem', 'node/cert.pem', 'node/key.pem'):
        deploy.protected(deploy.ETC / name, private=name.endswith('key.pem'))
    cert = deploy.ETC / 'node/cert.pem'
    fingerprint = deploy.run(['openssl', 'x509', '-in', cert, '-noout', '-fingerprint', '-sha1']).split('=')[-1].replace(':', '').lower()
    if fingerprint != c['node_certificate_sha1']:
        raise ValueError('node certificate fingerprint mismatch')
    deploy.run(['openssl', 'x509', '-in', cert, '-checkend', '3600', '-noout'])
    if deploy.run(['openssl', 'x509', '-in', cert, '-pubkey', '-noout']) != deploy.run([
            'openssl', 'pkey', '-in', deploy.ETC / 'node/key.pem', '-pubout']):
        raise ValueError('node private key mismatch')
    with tempfile.TemporaryDirectory() as tmp:
        rendered = Path(tmp) / 'node'
        render_node(raw, rendered)
        for p in rendered.iterdir():
            if not (source / p.name).is_file() or (source / p.name).read_bytes() != p.read_bytes():
                raise ValueError('rendered node deployment changed')
        targets = {'agent.conf': deploy.ETC / 'agent.conf',
                   'argus-openclaw-agent.service': Path('/etc/systemd/system/argus-openclaw-agent.service'),
                   'node.json': deploy.ETC / 'node.json'}
        # First inspect every existing file, then install missing files only.
        for name, target in targets.items():
            if target.exists() and deploy.protected(target).read_bytes() != (rendered / name).read_bytes():
                raise ValueError(f'shared node config differs; stop/review migration explicitly: {target}')
        for name, target in targets.items():
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as output:
                    output.write((rendered / name).read_bytes())
                target.chmod(0o600 if name.endswith('.json') else 0o644)
    deploy.run(['systemctl', 'daemon-reload'])
    return {'installed': True, 'scope': 'shared_node', 'started': False}
