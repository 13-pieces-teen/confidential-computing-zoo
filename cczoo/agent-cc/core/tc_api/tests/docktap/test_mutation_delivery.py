"""Crash cuts across the real proxy, outbox API, sequencer and confirmation.

Docker/RTMR/Rekor are fakes; SQLite, HTTP handlers, owner signing and
idempotency are real. No Docker request is replayed during recovery.
"""
import hashlib
import io
import json
import os
import sys
import types
import threading
from concurrent.futures import ThreadPoolExecutor
import urllib.error
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from tc_api.docktap import trucon_client as delivery
from tc_api.docktap.proxy.docker_proxy import DockerProxyServer
from tc_api.docktap.proxy.operation_log import OperationRecord
from tc_api.identity.sigstore_baseline import sign_dsse_with_owner_key
from tc_api.transparency import trucon_submitter
from tc_api.trucon import database as db


class Client:
    def __init__(self, cut=False):
        self.sent, self.cut = [], cut
    def settimeout(self, *_):
        pass
    def sendall(self, data):
        if self.cut:
            raise SystemExit('process died before client reply')
        self.sent.append(data)
    def close(self):
        pass


@pytest.fixture
def rig(tmp_path, monkeypatch):
    # TruCon's native process lock is not exercised by these HTTP unit tests.
    if os.name == 'nt':
        monkeypatch.setitem(sys.modules, 'fcntl', types.SimpleNamespace(LOCK_EX=2, LOCK_NB=4, LOCK_UN=8, flock=lambda *_: None))
        monkeypatch.setitem(sys.modules, 'tc_api.trucon.uds_gateway', types.SimpleNamespace(TruConUnixSocketGateway=Mock))
    from tc_api.trucon import app as service
    path = str(tmp_path / 'queue.db')
    db.init_db(path)
    monkeypatch.setattr(db, 'DB_PATH', path)
    for name in ('insert_record', 'get_chain_state', 'update_chain_state', 'get_record_by_idempotency_key',
                 'get_chain_records', 'create_commit_intent', 'get_commit_intent_by_token',
                 'get_commit_intent_by_idempotency_key', 'get_active_commit_intent_for_chain',
                 'update_commit_intent_status', 'expire_active_commit_intents', 'get_record_by_id', 'get_latest_confirmed_record'):
        original = getattr(db, name)
        def bound(*args, _original=original, **kwargs):
            return _original(*args, **{**kwargs, 'db_path': path})
        monkeypatch.setattr(db, name, bound)
        if hasattr(service, name):
            monkeypatch.setattr(service, name, bound)
    key = ec.generate_private_key(ec.SECP384R1())
    pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    statement = json.dumps({'predicate': {'event_type': 'chain.init', 'digest': 'sha384:' + '1' * 96}})
    root = json.dumps({'_owner_key_signed': True, 'envelope': sign_dsse_with_owner_key(statement, key), 'pub_key_pem': pem})
    db.insert_record('root', 'init', {'bundle': root}, 'PENDING', sequence_num=1, rtmr_extended=True,
                     mr_value='0' * 96, event_digest='sha384:' + '1' * 96)
    db.update_record_confirmed('root', '1' * 64, db_path=path)
    db.update_chain_state('default', 'root', 1, mr_value='0' * 96, head_log_id='1' * 64)
    monkeypatch.setattr(service, '_get_chain_owner_pub_key', lambda _: pem)
    monkeypatch.setattr(service, '_AUTH_DISABLED', True)
    extensions = []
    def extend(index, digest):
        previous = '0' * 96 if not extensions else extensions[-1]
        value = hashlib.sha384(bytes.fromhex(previous) + bytes.fromhex(digest.split(':')[-1])).hexdigest()
        extensions.append(value)
        return value, previous
    monkeypatch.setattr(service, '_local_mr', Mock(extend=extend))
    monkeypatch.setattr(delivery, '_delegation_enabled', lambda: True)
    monkeypatch.setattr(delivery, '_delegation_required', lambda: True)
    monkeypatch.setattr(db, 'get_active_delegation', lambda _: {'scope': list(delivery.LIFECYCLE_OPERATIONS), 'delegation_id': 'delegation-test'})
    monkeypatch.setattr(delivery, 'get_chain_owner_private_key', lambda _: key)
    monkeypatch.setattr(delivery, 'generate_chain_owner_pub_key_pem', lambda _: pem)
    monkeypatch.setattr(delivery, 'SigstoreLogAdapter', lambda: Mock(submit_owner_signed_entry=Mock(return_value=('2' * 64, 2, {}))))
    http = TestClient(service.app)
    posts = []
    def transport(method, route, *, json_body=None, **kwargs):
        if route == '/commit':
            posts.append(json_body)
        response = http.request(method, route, json=json_body)
        if response.status_code >= 400:
            raise urllib.error.HTTPError(route, response.status_code, response.text, {}, io.BytesIO(response.content))
        return response.json()
    monkeypatch.setattr(delivery, 'request_json', transport)
    monkeypatch.setattr(trucon_submitter, 'request_json', transport)
    monkeypatch.setattr('tc_api.docktap.proxy.docker_proxy.log_operation_json', lambda *_: None)
    return types.SimpleNamespace(path=path, http=http, extensions=extensions, posts=posts, transport=transport)


def proxy_call(rig, monkeypatch, *, client=None, response=None, docker_cut=False, complete_cut=False):
    request = b'POST /containers/demo/stop HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n'
    response = response if response is not None else b'HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n'
    committer = delivery.TruConCommitter(start_retry_worker=False)
    proxy = DockerProxyServer('/tmp/test.sock', '/var/run/docker.sock', trucon_committer=committer)
    record = OperationRecord(operation={'type': 'stop', 'method': 'POST', 'api_path': '/containers/demo/stop'}, container={'id': 'a' * 64})
    docker = Mock()
    def effect(*_):
        assert db.get_docktap_mutations()[0]['status'] == 'INFLIGHT'
        assert rig.http.get('/chain-state?include_history=true').status_code == 409
        if docker_cut:
            raise SystemExit('Docker effect occurred before process died')
    docker.sendall.side_effect = effect
    import socket
    socket_api = types.SimpleNamespace(**{**vars(socket), 'AF_UNIX': getattr(socket, 'AF_UNIX', 1), 'socket': lambda *_: docker})
    monkeypatch.setattr('tc_api.docktap.proxy.docker_proxy.socket', socket_api)
    monkeypatch.setattr(proxy, '_attestation_gate_enabled', lambda: False)
    monkeypatch.setattr(proxy, '_read_client_request', Mock(side_effect=[(request, None), (None, 'empty')]))
    monkeypatch.setattr(proxy._runtime_adapter, 'parse_operation_metadata', lambda *_: record)
    monkeypatch.setattr(proxy, '_read_docker_response', lambda *_: response)
    monkeypatch.setattr('tc_api.docktap.proxy.docker_proxy.bind_container_target', lambda data, *_: data)
    if complete_cut:
        monkeypatch.setattr(committer, 'complete_mutation', Mock(side_effect=SystemExit('died before saving result')))
    client = client or Client()
    proxy.handle_client(client)
    return committer, docker, client


@pytest.mark.parametrize('cut', ['after_docker_send', 'before_result_save'])
def test_unknown_outcome_survives_restart_and_never_replays_docker(rig, monkeypatch, cut):
    with pytest.raises(SystemExit):
        proxy_call(rig, monkeypatch, docker_cut=cut == 'after_docker_send', complete_cut=cut == 'before_result_save')
    db.init_db(rig.path)  # New process/connection, same guest boot and database.
    resumed = delivery.TruConCommitter(start_retry_worker=False)
    resumed.process_retry_queue()
    mutation = db.get_docktap_mutations()[0]
    assert mutation['status'] == 'INFLIGHT' and mutation['result_json'] is None
    assert rig.http.get('/chain-state').status_code == 409
    assert rig.http.get('/chain-state?include_history=true').status_code == 409
    assert not rig.posts and not rig.extensions


@pytest.mark.parametrize('point,status', [('after_reserve', 'INFLIGHT'), ('after_forward', 'INFLIGHT'), ('after_result', 'RESULT_READY')])
def test_experiment_cut_points_preserve_durable_state(rig, monkeypatch, point, status):
    calls = []
    def checkpoint(current, **fields):
        calls.append((current, fields))
        if current == point:
            assert db.get_docktap_mutations()[0]['status'] == status
            raise SystemExit('operator killed the held experiment process')
    monkeypatch.setattr('tc_api.experiment_barrier.checkpoint', checkpoint)
    with pytest.raises(SystemExit):
        proxy_call(rig, monkeypatch)
    assert calls[-1][0] == point
    assert db.get_docktap_mutations()[0]['status'] == status
    assert not rig.posts and not rig.extensions


def test_result_and_sign_barriers_are_honored_by_recovery_worker(rig, monkeypatch):
    committer, docker, _ = proxy_call(rig, monkeypatch)
    forwarded = docker.sendall.call_count
    def hold(point, **fields):
        if point == 'after_result':
            raise SystemExit('held before retry worker signs')
    monkeypatch.setattr('tc_api.experiment_barrier.checkpoint', hold)
    with pytest.raises(SystemExit):
        committer.process_retry_queue()
    assert not rig.posts and not rig.extensions
    def signed(point, **fields):
        if point == 'after_sign':
            assert db.get_docktap_mutations()[0]['submission_json'] is not None
            raise SystemExit('signed bytes durable; no commit yet')
    monkeypatch.setattr('tc_api.experiment_barrier.checkpoint', signed)
    with pytest.raises(SystemExit):
        committer.process_retry_queue()
    assert not rig.posts and not rig.extensions
    monkeypatch.setattr('tc_api.experiment_barrier.checkpoint', lambda *a, **kw: None)
    delivery.TruConCommitter(start_retry_worker=False).process_retry_queue()
    assert docker.sendall.call_count == forwarded == 1
    assert len(rig.extensions) == 1


def test_rtmr_before_db_cut_remains_an_explicit_unresolved_boundary(rig, monkeypatch):
    committer, docker, _ = proxy_call(rig, monkeypatch)
    cut = []
    def checkpoint(point, **fields):
        if point == 'after_rtmr_extend':
            cut.append(fields)
            raise SystemExit('MEASUREMENT_COMMIT_UNKNOWN')
    monkeypatch.setattr('tc_api.experiment_barrier.checkpoint', checkpoint)
    with pytest.raises(BaseException, match='MEASUREMENT_COMMIT_UNKNOWN'):
        committer.process_retry_queue()
    row = db.get_docktap_mutations()[0]
    assert cut[0]['mutation_id'] == row['mutation_id']
    assert len(rig.extensions) == 1 and row['status'] == 'RESULT_READY' and row['record_id'] is None
    assert rig.http.get('/chain-state?include_history=true').status_code == 409
    assert db.get_record_by_idempotency_key(row['mutation_id'], row['chain_id']) is None
    assert docker.sendall.call_count == 1
    # Do not automatically retry this cut or claim measurement reconciliation.


def test_confirmation_barrier_does_not_release_fence_or_replay_docker(rig, monkeypatch):
    committer, docker, _ = proxy_call(rig, monkeypatch)
    committer.process_retry_queue()
    row = db.get_docktap_mutations()[0]
    monkeypatch.setenv('ARGUS_EXPERIMENT_BARRIER_DIR', '/isolated-test-only')
    def checkpoint(point, **fields):
        if point == 'before_confirm':
            assert fields['mutation_id'] == row['mutation_id']
            raise SystemExit('held before confirmation transaction')
    monkeypatch.setattr('tc_api.experiment_barrier.checkpoint', checkpoint)
    with pytest.raises(SystemExit):
        db.update_record_confirmed(row['record_id'], '2' * 64, db_path=rig.path)
    assert db.get_docktap_mutations()[0]['status'] == 'SUBMITTED'
    assert rig.http.get('/chain-state?include_history=true').headers['X-Argus-Reason'] == 'MUTATION_PENDING'
    monkeypatch.setattr('tc_api.experiment_barrier.checkpoint', lambda *a, **kw: None)
    db.update_record_confirmed(row['record_id'], '2' * 64, db_path=rig.path)
    assert not db.get_docktap_mutations() and docker.sendall.call_count == 1


def test_saved_result_recovers_after_process_death_and_only_confirmation_releases_fence(rig, monkeypatch):
    with pytest.raises(SystemExit):
        proxy_call(rig, monkeypatch, client=Client(cut=True))
    mutation = db.get_docktap_mutations()[0]
    assert mutation['status'] == 'RESULT_READY' and mutation['submission_json'] is None
    assert rig.http.get('/chain-state?include_history=true').status_code == 409
    resumed = delivery.TruConCommitter(start_retry_worker=False)
    resumed.process_retry_queue()
    mutation = db.get_docktap_mutations()[0]
    assert mutation['status'] == 'SUBMITTED'
    assert rig.posts[0]['idempotency_key'] == mutation['mutation_id']
    assert len(rig.extensions) == 1
    assert rig.http.get('/chain-state?include_history=true').status_code == 409
    db.update_record_confirmed('unrelated', 'f' * 64, db_path=rig.path)
    assert rig.http.get('/chain-state').status_code == 409
    db.update_record_confirmed(mutation['record_id'], '2' * 64, db_path=rig.path)
    assert not db.get_docktap_mutations()
    assert rig.http.get('/chain-state?include_history=true').json()['log_ids'] == ['1' * 64, '2' * 64]


@pytest.mark.parametrize('accepted', [False, True])
def test_lost_commit_reply_reuses_exact_submission_without_duplicate_extend(rig, monkeypatch, accepted):
    committer, _, _ = proxy_call(rig, monkeypatch)
    transport = rig.transport
    cut = True
    def lossy(method, route, **kwargs):
        nonlocal cut
        if route == '/commit' and cut:
            cut = False
            if accepted:
                transport(method, route, **kwargs)
            raise urllib.error.URLError('lost commit reply')
        return transport(method, route, **kwargs)
    monkeypatch.setattr(trucon_submitter, 'request_json', lossy)
    committer.process_retry_queue()
    stored = db.get_docktap_mutations()[0]
    assert stored['submission_json'] is not None
    resumed = delivery.TruConCommitter(start_retry_worker=False)
    resumed.process_retry_queue()
    assert len(rig.posts) == 1 and len(rig.extensions) == 1
    assert rig.posts[0] == {**json.loads(stored['submission_json']), 'identity_token': None}
    assert db.get_docktap_mutations()[0]['status'] == 'SUBMITTED'


@pytest.mark.parametrize('response', [b'', b'HTTP/1.1 204 No Content\r\n', b'HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\nx'])
def test_missing_or_partial_response_never_becomes_a_successful_event(rig, monkeypatch, response):
    committer, _, client = proxy_call(rig, monkeypatch, response=response)
    assert db.get_docktap_mutations()[0]['status'] == 'INFLIGHT'
    committer.process_retry_queue()
    assert not rig.posts and not rig.extensions
    assert not any(item.startswith(b'HTTP/1.1 2') for item in client.sent)


def test_reservation_failure_does_not_reach_docker(rig, monkeypatch):
    real = rig.transport
    def unavailable(method, route, **kwargs):
        if route == '/mutations/reserve':
            raise urllib.error.URLError('database unavailable')
        return real(method, route, **kwargs)
    monkeypatch.setattr(delivery, 'request_json', unavailable)
    _, docker, client = proxy_call(rig, monkeypatch)
    docker.sendall.assert_not_called()
    assert client.sent[0].startswith(b'HTTP/1.1 503')
    assert not db.get_docktap_mutations()


def test_failed_known_result_is_recorded_and_pending_never_expires(rig, monkeypatch):
    committer, _, client = proxy_call(rig, monkeypatch, response=b'HTTP/1.1 500 Failed\r\nContent-Length: 0\r\n\r\n')
    row = db.get_docktap_mutations()[0]
    assert json.loads(row['result_json'])['op_record']['response']['status'] == 500
    assert client.sent[0].startswith(b'HTTP/1.1 500')
    db.expire_active_commit_intents()
    assert db.has_unresolved_docktap_mutation()
    committer.process_retry_queue()
    from tc_api.trucon.bundles import extract_bundle_predicate
    entries = extract_bundle_predicate(rig.posts[0]['bundle'])['entries']
    assert {'key': 'operation_result', 'value': 'failed'} in entries


@pytest.mark.parametrize('route_suffix', ['reserve', 'result'])
def test_lost_durable_write_ack_is_fail_safe(rig, monkeypatch, route_suffix):
    real = rig.transport
    def lost_ack(method, route, **kwargs):
        result = real(method, route, **kwargs)
        if route.startswith('/mutations/') and route.endswith('/' + route_suffix):
            raise urllib.error.URLError('durable write reply lost')
        return result
    monkeypatch.setattr(delivery, 'request_json', lost_ack)
    committer, docker, client = proxy_call(rig, monkeypatch)
    row = db.get_docktap_mutations()[0]
    assert client.sent[0].startswith(b'HTTP/1.1 503')
    assert rig.http.get('/chain-state').status_code == 409
    if route_suffix == 'reserve':
        docker.sendall.assert_not_called()
        committer.process_retry_queue()
        assert row['status'] == 'INFLIGHT' and not rig.posts
    else:
        assert row['status'] == 'RESULT_READY'
        committer.process_retry_queue()
        assert len(rig.posts) == 1


def test_expired_unsigned_commit_contract_can_be_rebuilt_but_docker_is_not_replayed(rig, monkeypatch):
    committer, docker, _ = proxy_call(rig, monkeypatch)
    def die_before_commit(**kwargs):
        raise SystemExit('signed bytes persisted, process died before commit')
    monkeypatch.setattr(committer, '_post_to_trucon', die_before_commit)
    with pytest.raises(SystemExit):
        committer.process_retry_queue()
    row = db.get_docktap_mutations()[0]
    old = row['submission_json']
    with db.get_db_connection(rig.path) as conn:
        conn.execute("UPDATE commit_intents SET expires_at='2000-01-01T00:00:00'")
        conn.commit()
    resumed = delivery.TruConCommitter(start_retry_worker=False)
    resumed.process_retry_queue()
    row = db.get_docktap_mutations()[0]
    assert row['status'] == 'SUBMITTED' and row['submission_json'] != old
    assert len(rig.extensions) == 1
    docker.sendall.assert_called_once()


def test_record_result_is_immutable_and_signed_commit_must_match_saved_bytes(rig, monkeypatch):
    committer, _, _ = proxy_call(rig, monkeypatch)
    row = db.get_docktap_mutations()[0]
    result = json.loads(row['result_json'])
    result['op_record']['container']['id'] = 'b' * 64
    assert rig.http.post('/mutations/' + row['mutation_id'] + '/result', json=result).status_code == 409
    def cut(**kwargs):
        raise SystemExit('persisted before commit')
    monkeypatch.setattr(committer, '_post_to_trucon', cut)
    with pytest.raises(SystemExit):
        committer.process_retry_queue()
    stored = json.loads(db.get_docktap_mutations()[0]['submission_json'])
    assert rig.http.post('/commit', json={**stored, 'event_digest': 'sha384:' + 'f' * 96}).status_code == 409
    assert not rig.extensions


def test_outbox_serialization_omits_credentials_and_raw_command():
    record = OperationRecord(operation={'type': 'stop', 'api_path': '/containers/demo/stop?token=secret'},
        user={'Authorization': 'Bearer secret'}, params={'password': 'secret'},
        container={'id': 'a' * 64, 'command': ['echo', 'secret']}, response={'status': 204, 'Authorization': 'secret'})
    stored = json.dumps(delivery.TruConCommitter._mutation_record(record))
    assert 'secret' not in stored and 'Authorization' not in stored


def test_history_read_and_mutation_reserve_share_one_linearization_lock(rig, monkeypatch):
    checked, release, attempted, reserved = [threading.Event() for _ in range(4)]
    original = db.has_unresolved_docktap_mutation
    events = []
    def pause_after_check(*args, **kwargs):
        result = original(*args, **kwargs)
        checked.set()
        assert release.wait(3)
        return result
    monkeypatch.setattr(db, 'has_unresolved_docktap_mutation', pause_after_check)
    reader = db.get_chain_records
    def traced_read(*args, **kwargs):
        result = reader(*args, **kwargs)
        events.append('read')
        return result
    monkeypatch.setattr(db, 'get_chain_records', traced_read)
    record = OperationRecord(operation={'type': 'stop'}, container={'id': 'a' * 64})
    def reserve():
        attempted.set()
        value = delivery.TruConCommitter(start_retry_worker=False).begin_mutation(record, 'stop')
        events.append('reserve_ack')
        reserved.set()
        return value
    with ThreadPoolExecutor(max_workers=2) as pool:
        query = pool.submit(rig.http.get, '/chain-state?include_history=true')
        assert checked.wait(3)
        mutation = pool.submit(reserve)
        assert attempted.wait(3)
        assert not reserved.wait(0.1)
        release.set()
        assert query.result(3).status_code == 200
        mutation.result(3)
    assert events.index('read') < events.index('reserve_ack')
    monkeypatch.setattr(db, 'has_unresolved_docktap_mutation', original)
    assert rig.http.get('/chain-state?include_history=true').status_code == 409
