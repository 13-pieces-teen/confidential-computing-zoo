import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import ssl
import socket
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import load
import load_fleet
import step
from client_material import ContextProvider, resolve_credentials
from common import read, sha


@pytest.fixture
def tls(tmp_path):
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Argus sampler test CA')])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=1))
          .not_valid_after(now + datetime.timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .sign(ca_key, hashes.SHA256()))
    bundle = tmp_path / 'ca.pem'; bundle.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    def issue(name, usage):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cert = (x509.CertificateBuilder().subject_name(x509.Name([])).issuer_name(ca_name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=1))
                .not_valid_after(now + datetime.timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([x509.UniformResourceIdentifier('spiffe://argus.local/' + name)]), critical=True)
                .add_extension(x509.ExtendedKeyUsage([usage]), critical=True).sign(ca_key, hashes.SHA256()))
        prefix = name.replace('/', '-')
        cert_path, key_path = tmp_path / (prefix + '.pem'), tmp_path / (prefix + '.key')
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        return cert_path, key_path
    server_cert, server_key = issue('service/test', ExtendedKeyUsageOID.SERVER_AUTH)
    clients = {name:issue('agent/' + name, ExtendedKeyUsageOID.CLIENT_AUTH) for name in ('alice', 'bob')}
    seen = []
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def do_GET(self):
            self.handle_api()
        def do_POST(self):
            self.handle_api()
        def handle_api(self):
            payload = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            seen.append({'request_id':self.headers.get('X-Argus-Request-ID'), 'run_id':self.headers.get('X-Argus-Run-ID'),
                         'path':self.path, 'peer_port':self.client_address[1], 'method':self.command,
                         'peer_uri':self.connection.getpeercert()['subjectAltName'][0][1]})
            if self.path == '/slow': time.sleep(.2)
            if self.path == '/drop-after-post':
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            body = b'{"status":"ok"}'
            if self.path == '/api/v1/search/find':
                query = json.loads(payload)['query']
                memories = [] if query == 'empty' else [{'uri':'viking://user/alice/memories/fact', 'level':2,
                                                        'abstract':'private-synthetic-content'}]
                body = json.dumps({'status':'ok', 'result':{'memories':memories}}).encode()
            self.send_response(403 if self.path == '/denied' else 200)
            if self.path == '/close': self.send_header('Connection', 'close'); self.close_connection = True
            self.send_header('Content-Length', str(len(body))); self.end_headers()
            try: self.wfile.write(body)
            except (OSError, ssl.SSLError): pass
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(server_cert, server_key); context.load_verify_locations(bundle)
    context.verify_mode = ssl.CERT_REQUIRED
    server.socket = context.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    yield SimpleNamespace(url=f'https://127.0.0.1:{server.server_port}', bundle=bundle, clients=clients,
                          seen=seen, root=tmp_path, issue=issue)
    server.shutdown(); server.server_close(); worker.join(timeout=3)


def args(tls, output, **changes):
    cert, key = tls.clients['alice']
    return SimpleNamespace(url=tls.url + '/ok', bundle=str(tls.bundle), cert=str(cert), key=str(key),
                           server_id='spiffe://argus.local/service/test', body_file=None, api_key_env=None,
                           api_key_file=None, run_id='paired-run', output=str(output), **{
                               'rate':20, 'concurrency':2, 'warmup':0, 'measure':.25, 'timeout':1, **changes})


def test_real_tls_success_denial_and_peer_check_before_http(tls):
    context = ssl.create_default_context(cafile=str(tls.bundle)); context.check_hostname = False
    context.load_cert_chain(*tls.clients['alice'])
    for number, path, expected in ((0, '/ok', 'success'), (1, '/denied', 'rejected')):
        row = load.request(tls.url + path, context, 'spiffe://argus.local/service/test', None, 'synthetic',
                           1, 'run', number, 'measurement', time.monotonic()+2, 'run-alice')
        assert row['outcome'] == expected
        assert row['request_id'] == tls.seen[-1]['request_id'] == f'run-alice-{number}'
    before = len(tls.seen)
    bad = load.request(tls.url + '/ok', context, 'spiffe://argus.local/service/wrong', None, 'synthetic',
                       1, 'run', 2, 'measurement', time.monotonic()+2)
    assert bad['outcome'] == 'unknown' and bad['error_class'] == 'ValueError'
    assert len(tls.seen) == before


def test_bounded_sampler_records_overload_and_incomplete_requests(tls, tmp_path):
    options = args(tls, tmp_path / 'slow', rate=100, concurrency=1, measure=.08, timeout=.04)
    options.url = tls.url + '/slow'
    result = load.run(options)
    assert result['overload'] > 0 and result['timeout'] > 0
    assert result['result'] == 'UNKNOWN' and result['measurement_complete'] is True
    assert result['requests_sha256'] == sha(tmp_path / 'slow/requests.jsonl')


def test_fleet_wire_ids_and_saved_ids_match_and_actual_identities_differ(tls, tmp_path):
    instances = []
    for name, (cert, key) in tls.clients.items():
        api = tmp_path / (name + '.api'); api.write_text('synthetic-' + name); api.chmod(0o600)
        instances.append({'name':name, 'cert':str(cert), 'key':str(key), 'api_key_file':str(api),
                          'bundle':str(tls.bundle), 'url':tls.url + '/ok', 'server_id':'spiffe://argus.local/service/test'})
    config = {'instances':instances, 'frozen':True, 'rate':20, 'warmup_seconds':0, 'measurement_seconds':.25}
    path = tmp_path / 'fleet.json'; path.write_text(json.dumps(config))
    result = load_fleet.execute(path, tmp_path / 'results', 'same-run', 2)
    rows = [json.loads(line) for line in (tmp_path / 'results/requests.jsonl').read_text().splitlines()]
    assert result['result'] == 'PASS' and result['measurement_complete']
    assert len({r['request_id'] for r in rows}) == len(rows)
    assert {r['request_id'] for r in rows} == {r['request_id'] for r in tls.seen}
    assert {r['run_id'] for r in rows} == {'same-run'}
    assert result['requests_sha256'] == sha(tmp_path / 'results/requests.jsonl')
    saved = Path(instances[1]['api_key_file']).read_text()
    Path(instances[1]['api_key_file']).write_text(Path(instances[0]['api_key_file']).read_text())
    with pytest.raises(ValueError, match='actual business keys'):
        load_fleet.execute(path, tmp_path / 'duplicate-key', 'run', 2)
    Path(instances[1]['api_key_file']).write_text(saved)
    # Different paths containing the same certificate must not establish two identities.
    duplicate = tmp_path / 'copied.pem'; duplicate.write_bytes(tls.clients['alice'][0].read_bytes())
    config['instances'][1]['cert'] = str(duplicate); path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='actual SPIFFE'):
        load_fleet.execute(path, tmp_path / 'duplicate', 'run', 2)


def test_traversal_rejected_before_creation_and_capacity_is_explicit(tmp_path):
    c = {'instances':[{'name':'../escape'}], 'frozen':True, 'rate':1}
    path = tmp_path / 'config.json'; path.write_text(json.dumps(c))
    with pytest.raises(ValueError, match='safe single'):
        load_fleet.execute(path, tmp_path / 'out', 'run', 1)
    assert not (tmp_path / 'out').exists()
    result = load_fleet.execute(path, tmp_path / 'capacity', 'run', 4)
    assert result['result'] == 'NOT_RUN' and result['reason'] == 'CAPACITY_STOP'
    assert result['measurement_complete'] is False
    assert (tmp_path / 'capacity/requests.jsonl').read_text() == ''


def test_publisher_generation_selected_and_stale_lease_blocks_new_requests(tls, tmp_path):
    directory = tmp_path / 'credentials'; generation = directory / 'generation-one'; generation.mkdir(parents=True)
    for name, source in [('svid.pem',tls.clients['alice'][0]),('key.pem',tls.clients['alice'][1]),('bundle.pem',tls.bundle)]:
        (generation/name).write_bytes(source.read_bytes())
    lease = {'version':1,'generation':'generation-one','serial':'a','lease_until':time.time_ns()//1000000+2500,
             'expires_at':time.time_ns()//1000000+60000}
    ready = directory/'ready.json'; ready.write_text(json.dumps(lease))
    material = resolve_credentials({'credentials_dir':str(directory), 'client_id':'spiffe://argus.local/agent/alice'})
    assert material['cert'] == str(generation/'svid.pem')
    provider = ContextProvider(directory,'spiffe://argus.local/agent/alice')
    row = load.request(tls.url+'/ok',provider,'spiffe://argus.local/service/test',None,None,1,'run',0,'measurement',time.monotonic()+2)
    assert row['outcome'] == 'success'
    count = len(tls.seen); lease['lease_until'] = 1; ready.write_text(json.dumps(lease))
    row = load.request(tls.url+'/ok',provider,'spiffe://argus.local/service/test',None,None,1,'run',1,'measurement',time.monotonic()+2)
    assert row['outcome'] == 'unknown' and len(tls.seen) == count


@pytest.mark.parametrize('completed,operation,expected', [(False,'op','UNKNOWN'), (True,'old','UNKNOWN'), (True,'op','PASS')])
def test_step_requires_completed_matching_fleet_receipt(tmp_path, completed, operation, expected):
    native = tmp_path / 'business'; native.mkdir()
    (native / 'result.json').write_text(json.dumps({'run_id':'run', 'operation_id':operation, 'completed':completed, 'result':'PASS'}))
    (tmp_path / 'config.json').write_text('{"instances":[]}')
    options = SimpleNamespace(tool='fleet', action='resume', output=str(tmp_path), config=str(tmp_path / 'config.json'), instance=None,
                              timeout=1, receipt='step-result.json')
    with patch.dict('os.environ', {'ARGUS_RUN_ID':'run','ARGUS_OPERATION_ID':'op'}), patch.object(step, 'run_logged', return_value=SimpleNamespace(returncode=0)):
        step.execute(options)
    assert read(tmp_path / 'step-result.json')['result'] == expected


def test_durable_writer_failure_cannot_report_complete_measurement(tls):
    with patch.object(load, 'write_measurement', side_effect=OSError('synthetic disk failure')):
        result = load.run(args(tls, tls.root / 'disk-failure', rate=1, measure=.15, concurrency=1))
    assert result['result'] == 'UNKNOWN'
    assert result['measurement_complete'] is False
    assert result['collector_error_classes'] == ['OSError']
    assert result['scheduled_requests'] == 1


def client_context(tls):
    context = ssl.create_default_context(cafile=str(tls.bundle)); context.check_hostname = False
    context.load_cert_chain(*tls.clients['alice'])
    return context


@pytest.mark.parametrize('mode', ['new', 'reuse'])
def test_real_mtls_connections_are_created_or_reused_as_requested(tls, mode):
    context, session = client_context(tls), load.ConnectionSession('test-worker')
    rows = []
    try:
        for number in range(3):
            rows.append(load.request(tls.url + '/ok', context, 'spiffe://argus.local/service/test', None, None,
                                     1, 'run', number, 'measurement', time.monotonic()+2,
                                     session=session, connection_mode=mode))
    finally:
        session.close()
    assert all(row['outcome'] == 'success' for row in rows)
    expected = 3 if mode == 'new' else 1
    # These ports are observed by the real mTLS server, not inferred from flags.
    assert len({item['peer_port'] for item in tls.seen}) == expected
    assert len({row['connection_id'] for row in rows}) == expected
    assert sum(row['connection_created'] for row in rows) == expected
    assert sum(row['connection_reused'] for row in rows) == 3 - expected
    assert all(row['tcp_connect_ms'] > 0 and row['tls_handshake_ms'] > 0 for row in rows if row['connection_created'])
    assert all(row['connect_ms'] == 0 for row in rows if row['connection_reused'])
    assert all(item['peer_uri'] == 'spiffe://argus.local/agent/alice' for item in tls.seen)


def test_server_close_reconnects_next_request_without_replay(tls):
    context, session = client_context(tls), load.ConnectionSession('worker')
    try:
        first = load.request(tls.url+'/close', context, 'spiffe://argus.local/service/test', None, None,
                             1, 'run', 0, 'measurement', time.monotonic()+2, session=session, connection_mode='reuse')
        second = load.request(tls.url+'/ok', context, 'spiffe://argus.local/service/test', None, None,
                              1, 'run', 1, 'measurement', time.monotonic()+2, session=session, connection_mode='reuse')
        unknown = load.request(tls.url+'/drop-after-post', context, 'spiffe://argus.local/service/test', b'{"write":"one"}', None,
                               1, 'run', 2, 'measurement', time.monotonic()+2, session=session, connection_mode='reuse')
        following = load.request(tls.url+'/ok', context, 'spiffe://argus.local/service/test', None, None,
                                 1, 'run', 3, 'measurement', time.monotonic()+2, session=session, connection_mode='reuse')
    finally:
        session.close()
    assert first['outcome'] == second['outcome'] == following['outcome'] == 'success'
    assert second['reconnect'] and second['connect_reason'] == 'server_closed'
    assert unknown['outcome'] == 'unknown' and unknown['submission_state'] == 'unknown'
    assert unknown['connection_reused'] and not unknown['request_replayed']
    assert [item['request_id'] for item in tls.seen] == ['run-0', 'run-1', 'run-2', 'run-3']
    assert following['reconnect'] and following['connect_reason'] == 'previous_error'


def test_reuse_checks_publisher_lease_and_rotates_to_new_identity_material(tls):
    directory = tls.root / 'rotating'; directory.mkdir()
    ready = directory / 'ready.json'
    def publish(name, cert, key, serial):
        generation = directory / name; generation.mkdir()
        for filename, source in [('svid.pem', cert), ('key.pem', key), ('bundle.pem', tls.bundle)]:
            (generation / filename).write_bytes(Path(source).read_bytes())
        ready.write_text(json.dumps({'version':1, 'generation':name, 'serial':serial,
                                    'lease_until':time.time_ns()//1000000+4900,
                                    'expires_at':time.time_ns()//1000000+60000}))
    publish('generation-one', *tls.clients['alice'], 'one')
    provider = ContextProvider(directory, 'spiffe://argus.local/agent/alice')
    session = load.ConnectionSession('rotating-worker')
    def invoke(number):
        return load.request(tls.url+'/ok', provider, 'spiffe://argus.local/service/test', None, None,
                            1, 'run', number, 'measurement', time.monotonic()+2, session=session, connection_mode='reuse')
    try:
        first, reused = invoke(0), invoke(1)
        cert, key = tls.issue('agent/alice', ExtendedKeyUsageOID.CLIENT_AUTH)
        publish('generation-two', cert, key, 'two')
        rotated = invoke(2)
        lease = json.loads(ready.read_text()); lease['lease_until'] = 1; ready.write_text(json.dumps(lease))
        rejected = invoke(3)
        assert session.conn is None
    finally:
        session.close()
    assert first['outcome'] == reused['outcome'] == rotated['outcome'] == 'success'
    assert reused['connection_reused']
    assert rotated['connect_reason'] == 'credentials_rotated' and rotated['reconnect']
    assert first['connection_id'] == reused['connection_id'] != rotated['connection_id']
    assert rejected['outcome'] == 'unknown' and rejected['submission_state'] == 'not_attempted'
    assert len(tls.seen) == 3 and len({item['peer_port'] for item in tls.seen}) == 2


def test_sampler_reuses_per_worker_and_reports_real_connection_timings(tls):
    result = load.run(args(tls, tls.root/'reuse-run', connection_mode='reuse', concurrency=1, rate=15, measure=.45))
    rows = [json.loads(line) for line in (tls.root/'reuse-run/requests.jsonl').read_text().splitlines()]
    assert result['connection_mode'] == 'reuse' and result['connections']['connections_created'] == 1
    assert result['connections']['requests_reusing_connection'] >= 3
    assert result['connections']['tls_handshake_ms']['n'] == 1
    assert result['connections']['api_ms']['n'] == len(rows)
    assert len({r['worker_id'] for r in rows}) == 1
    assert len({r['peer_port'] for r in tls.seen}) == 1


def test_fleet_reuse_keeps_client_identity_and_connections_separate(tls):
    instances = []
    for name, (cert, key) in tls.clients.items():
        api = tls.root/(name+'.secret'); api.write_text('synthetic-'+name); api.chmod(0o600)
        instances.append({'name':name, 'cert':str(cert), 'key':str(key), 'api_key_file':str(api),
                          'bundle':str(tls.bundle), 'url':tls.url+'/ok', 'server_id':'spiffe://argus.local/service/test'})
    config = {'instances':instances, 'frozen':True, 'rate':25, 'warmup_seconds':0, 'measurement_seconds':.45,
              'connection_mode':'reuse', 'concurrency_per_client':1}
    path = tls.root/'fleet-reuse.json'; path.write_text(json.dumps(config))
    result = load_fleet.execute(path, tls.root/'fleet-reuse', 'run', 2)
    assert result['result'] == 'PASS' and result['connections']['connections_created'] == 2
    assert all(item['connections']['requests_reusing_connection'] >= 2 for item in result['per_client'])
    for name in ('alice', 'bob'):
        observed = [item for item in tls.seen if '-'+name+'-' in item['request_id']]
        assert len({item['peer_port'] for item in observed}) == 1
        assert {item['peer_uri'] for item in observed} == {'spiffe://argus.local/agent/'+name}
    assert len({item['peer_port'] for item in tls.seen}) == 2


@pytest.mark.parametrize('query,expected', [('remember', 'PASS'), ('empty', 'FAIL')])
def test_nonempty_memory_workload_is_separate_from_status_http_success(tls, query, expected):
    body = tls.root/'query.json'; body.write_text(json.dumps({'query':query, 'limit':5}))
    options = args(tls, tls.root/'memory-load', connection_mode='reuse', workload_kind='memory_query',
                   concurrency=1, rate=10, measure=.35)
    options.url = tls.url+'/api/v1/search/find'; options.body_file = str(body)
    result = load.run(options)
    trace = (tls.root/'memory-load/requests.jsonl').read_text()
    assert result['result'] == expected and result['workload_kind'] == 'memory_query'
    assert result['success'] > 0
    if query == 'remember': assert result['memory_nonempty_goodput_rps'] > 0
    else: assert result['memory_nonempty_goodput_rps'] == 0 and result['memory_results']['empty'] > 0
    assert 'private-synthetic-content' not in trace and 'viking://user/alice' not in trace


def test_memory_workload_cannot_relabel_status_endpoint(tls):
    options = args(tls, tls.root/'wrong-memory-kind', workload_kind='memory_query')
    options.url = tls.url+'/api/v1/system/status'
    with pytest.raises(ValueError, match='memory search POST'):
        load.run(options)
