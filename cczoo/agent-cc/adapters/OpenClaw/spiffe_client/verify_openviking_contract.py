#!/usr/bin/env python3
"""Hash-pinned upstream auth predicate checks, not a deployed OpenViking test."""
import argparse
import ast
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import urllib.request

ROOT = Path(__file__).resolve().parent


def selected(source, names, namespace):
    tree = ast.parse(source)
    nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name in names]
    if {node.name for node in nodes} != set(names): raise ValueError('pinned upstream symbols changed')
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, *nodes], type_ignores=[]))
    exec(compile(module, '<hash-pinned-upstream>', 'exec'), namespace)


def verify(cache, fetch=False):
    lock = json.loads((ROOT / 'config/openviking-contract.lock.json').read_text())
    sources = {}
    for name, expected in lock['files'].items():
        path = cache / name
        if fetch and not path.exists():
            url = f'https://raw.githubusercontent.com/{lock["repository"]}/{lock["commit"]}/{name}'
            data = urllib.request.urlopen(url, timeout=30).read(1024*1024)
            if hashlib.sha256(data).hexdigest() != expected: raise ValueError('upstream download hash mismatch')
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected: raise ValueError('upstream source hash mismatch')
        sources[name] = data.decode()
    identity = {}
    selected(sources['openviking/server/identity.py'], ['Role'], identity)
    role = identity['Role']
    assert str(role('user')) == 'user'
    class UnauthenticatedError(ValueError): pass
    class PermissionDeniedError(ValueError): pass
    namespace = {'AuthPlugin':object, 'Role':role, 'UnauthenticatedError':UnauthenticatedError,
                 'PermissionDeniedError':PermissionDeniedError}
    selected(sources['openviking/server/auth/plugins/api_key.py'], ['_remove_header', 'ApiKeyAuthPlugin'], namespace)
    plugin = namespace['ApiKeyAuthPlugin']()
    async def no_oauth(*args, **kwargs): return None
    plugin._try_resolve_oauth_token = no_oauth
    def resolve(key):
        if key != 'synthetic-user-key': raise UnauthenticatedError('bad key')
        return SimpleNamespace(role=role('user'), account_id='shared', user_id='alice')
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(api_key_manager=SimpleNamespace(resolve=resolve))),
                              scope={'headers':[(b'x-openviking-user', b'bob'), (b'x-openviking-account', b'other')]})
    value = asyncio.run(plugin.resolve_identity(request, api_key='synthetic-user-key',
                                                x_openviking_account='other', x_openviking_user='bob'))
    assert (value.account_id, value.user_id, value.role) == ('shared', 'alice', 'user')
    assert request.scope['headers'] == []
    for key in (None, 'invalid'):
        try: asyncio.run(plugin.resolve_identity(request, api_key=key))
        except UnauthenticatedError: pass
        else: raise AssertionError('upstream accepted missing/invalid key')
    # Exercise the actual namespace ownership predicate independently of its URI
    # parser and backend: normalized ownership metadata is a controlled fixture.
    scope = {'NamespaceShapeError':ValueError,
             'resolve_uri':lambda uri, ctx: SimpleNamespace(scope='user', owner_user_id=uri),
             'uri_parts':lambda uri: uri.split('/')}
    selected(sources['openviking/core/namespace.py'], ['is_accessible'], scope)
    ctx = SimpleNamespace(role='user', user=SimpleNamespace(user_id='alice'), actor_peer_id=None)
    assert scope['is_accessible']('alice', ctx)
    assert not scope['is_accessible']('bob', ctx)
    ctx.role = 'admin'
    assert not scope['is_accessible']('bob', ctx)
    ctx.role = 'root'
    assert scope['is_accessible']('bob', ctx)
    return {'result':'PASS', 'source_commit':lock['commit'], 'files':lock['files'],
            'checks':['user key beats forged identity headers', 'missing/invalid key rejected',
                      'private owner predicate denies another user and admin'],
            'scope':'upstream auth/access predicate unit execution',
            'controlled_dependencies':['key store', 'OAuth excluded', 'normalized URI owner metadata'],
            'deployed_http_and_storage':'NOT_RUN'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cache', type=Path, required=True)
    p.add_argument('--fetch', action='store_true')
    args = p.parse_args()
    print(json.dumps(verify(args.cache, args.fetch), indent=2))
