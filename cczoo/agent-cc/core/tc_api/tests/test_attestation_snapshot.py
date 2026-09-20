"""Exercise the real SQLite snapshot and the Provider's read-only caller scope."""
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from tc_api.trucon import database
from tc_api.trucon.auth import authorize_caller
from tc_api.trucon.routers import query


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    path = str(tmp_path / "queue.db")
    database.init_db(path)
    with database.get_db_connection(path) as conn:
        for seq in (1, 2, 3):
            conn.execute("INSERT INTO commit_queue(record_id,chain_id,payload,status,log_id,mr_value,sequence_num,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (str(seq), "default", "{}", "CONFIRMED", str(seq) * 64, "0" * 96, seq, "now"))
        conn.commit()
    original = database.get_attestation_records
    monkeypatch.setattr(database, "get_attestation_records", lambda chain_id, limit: original(chain_id, limit, path))
    app = FastAPI()
    app.include_router(query.router)
    return TestClient(app), path


def test_complete_ordered_snapshot(snapshot):
    client, _ = snapshot
    assert client.get("/attestation-snapshot").json() == {"chain_id": "default", "sequence_num": 3,
        "rtmr": "0" * 96, "rekor_entry_uuids": [str(x) * 64 for x in (1, 2, 3)]}


@pytest.mark.parametrize("sql", [
    "UPDATE commit_queue SET status='PENDING' WHERE sequence_num=2",
    "DELETE FROM commit_queue WHERE sequence_num=2",
    "UPDATE commit_queue SET log_id='42' WHERE sequence_num=2",
    "UPDATE commit_queue SET log_id=(SELECT log_id FROM commit_queue WHERE sequence_num=1) WHERE sequence_num=2",
    "UPDATE commit_queue SET mr_value=NULL WHERE sequence_num=3",
    "DELETE FROM commit_queue",
])
def test_snapshot_never_hides_missing_or_unuploaded_events(snapshot, sql):
    client, path = snapshot
    with database.get_db_connection(path) as conn:
        conn.execute(sql)
        conn.commit()
    assert client.get("/attestation-snapshot").status_code == 409


def test_snapshot_bound(snapshot, monkeypatch):
    client, _ = snapshot
    monkeypatch.setattr(query, "MAX_ATTESTATION_ENTRIES", 2)
    assert client.get("/attestation-snapshot").status_code == 409


@pytest.mark.parametrize("method,path,transport,uid,allowed", [
    ("GET", "/attestation-snapshot", "uds", "0", True),
    ("GET", "/attestation-snapshot", "uds", "1000", False),
    ("GET", "/attestation-snapshot", "http_compat", "0", False),
    ("POST", "/commit", "uds", "0", False),
    ("GET", "/state", "uds", "0", False),
])
def test_provider_caller_is_root_uds_read_only(method, path, transport, uid, allowed):
    request = Request({"type": "http", "method": method, "path": path, "headers": []})
    request.state.auth_transport, request.state.peer_uid = transport, uid
    assert (authorize_caller("argus_provider", request) is None) == allowed
