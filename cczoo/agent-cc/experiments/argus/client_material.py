"""Read current publisher generations for steady API sampling (not a fault oracle)."""
import hashlib
from pathlib import Path
import re
import ssl
import subprocess
import threading
import time

from common import read, require


def certificate_identity(path):
    result = subprocess.run(['openssl', 'x509', '-in', str(path), '-noout', '-ext', 'subjectAltName'],
                            capture_output=True, text=True, timeout=10, check=True)
    uris = re.findall(r'URI:([^,\s]+)', result.stdout)
    require(len(uris) == 1 and re.fullmatch(r'spiffe://[a-zA-Z0-9.-]+/[a-zA-Z0-9/_.-]+', uris[0]) is not None,
            'one concrete SPIFFE client URI required')
    return uris[0]


def certificate_public_key(path):
    result = subprocess.run(['openssl', 'x509', '-in', str(path), '-noout', '-pubkey'],
                            capture_output=True, timeout=10, check=True)
    return hashlib.sha256(result.stdout).digest()


def snapshot(directory):
    directory = Path(directory)
    require(directory.is_dir() and not directory.is_symlink(), 'unsafe credential directory')
    ready = directory / 'ready.json'
    require(ready.is_file() and not ready.is_symlink(), 'missing protected publisher lease')
    lease = read(ready); now = time.time_ns() // 1000000
    require(lease.get('version') == 1 and re.fullmatch(r'generation-[a-z0-9-]+', lease.get('generation', '')),
            'invalid credential generation')
    require(type(lease.get('lease_until')) is int and now < lease['lease_until'] <= now + 5000
            and type(lease.get('expires_at')) is int and lease['expires_at'] > now, 'expired publisher lease')
    generation = directory / lease['generation']
    require(generation.is_dir() and not generation.is_symlink(), 'unsafe credential generation')
    paths = {kind:str(generation / name) for kind,name in (('cert','svid.pem'),('key','key.pem'),('bundle','bundle.pem'))}
    for file in paths.values():
        require(Path(file).is_file() and not Path(file).is_symlink(), 'unsafe credential file')
    return dict(paths, generation=lease['generation'], serial=lease.get('serial'), expires_at=lease['expires_at'])


def resolve_credentials(item, min_validity_ms=0):
    if item.get('credentials_dir'):
        require(not any(item.get(key) for key in ('cert', 'key', 'bundle')), 'choose publisher directory or fixed material')
        material = snapshot(item['credentials_dir'])
        require(material['expires_at'] - time.time_ns() // 1000000 >= min_validity_ms,
                'credential expires inside observation window')
    else:
        require(all(item.get(key) for key in ('cert', 'key', 'bundle')), 'complete fixed material required')
        material = {key:item[key] for key in ('cert', 'key', 'bundle')}
    if item.get('client_id'):
        require(certificate_identity(material['cert']) == item['client_id'], 'client identity differs from configuration')
    return {key:material[key] for key in ('cert', 'key', 'bundle')}


class ContextProvider:
    """Refresh before each new request; active stream closure is tested elsewhere."""
    def __init__(self, directory, client_id=None):
        self.directory, self.client_id = directory, client_id
        self.guard, self.material, self.context = threading.Lock(), None, None

    def __call__(self):
        with self.guard:
            material = snapshot(self.directory)
            if material != self.material:
                identity = certificate_identity(material['cert'])
                if self.client_id is None: self.client_id = identity
                require(identity == self.client_id, 'publisher client identity changed')
                context = ssl.create_default_context(cafile=material['bundle']); context.check_hostname = False
                context.load_cert_chain(material['cert'], material['key'])
                require(snapshot(self.directory) == material, 'credential generation changed during read')
                self.material, self.context = material, context
            return self.context
