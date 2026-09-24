"""Exercise confirmed history through the existing chain-state query."""
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
    original = database.get_chain_records
    monkeypatch.setattr(database, "get_chain_records", lambda chain_id, **kwargs: original(chain_id, path, **kwargs))
    original_state = database.get_chain_state
    monkeypatch.setattr(database, "get_chain_state", lambda chain_id: original_state(chain_id, path))
    app = FastAPI()
    app.include_router(query.router)
    return TestClient(app), path


def test_complete_ordered_snapshot(snapshot):
    client, _ = snapshot
    assert client.get("/chain-state?include_history=true").json() == {
        "chain_id": "default", "head_record_id": "3", "head_log_id": "3" * 64,
        "sequence_num": 3, "mr_value": "0" * 96, "updated_at": "now",
        "log_ids": [str(x) * 64 for x in (1, 2, 3)],
    }


def test_history_preserves_numeric_references_and_ignores_mixed_cached_head(snapshot):
    client, path = snapshot
    database.update_chain_state("default", "stale", 99, mr_value="f" * 96, head_log_id="old", db_path=path)
    with database.get_db_connection(path) as conn:
        conn.execute("UPDATE commit_queue SET log_id='42' WHERE sequence_num=3")
        conn.commit()
    result = client.get("/chain-state?include_history=true").json()
    assert (result["head_log_id"], result["sequence_num"], result["mr_value"]) == ("42", 3, "0" * 96)
    assert result["log_ids"][-1] == "42"
    # The ordinary diagnostic query retains its existing response contract.
    ordinary = client.get("/chain-state").json()
    assert ordinary["head_log_id"] == "old"
    assert "log_ids" not in ordinary


def test_bounded_metadata_query_reuses_chain_reader(snapshot):
    rows = database.get_chain_records("default", limit=2, metadata_only=True)
    assert len(rows) == 2 and "payload" not in rows[0].keys()
    assert len(database.get_chain_records("default")) == 3


@pytest.mark.parametrize("sql", [
    "UPDATE commit_queue SET status='PENDING' WHERE sequence_num=2",
    "UPDATE commit_queue SET status='SUBMITTING' WHERE sequence_num=2",
    "UPDATE commit_queue SET status='FAILED_RETRYABLE' WHERE sequence_num=2",
    "UPDATE commit_queue SET status='FAILED_TERMINAL' WHERE sequence_num=2",
    "DELETE FROM commit_queue WHERE sequence_num=2",
    "UPDATE commit_queue SET log_id=NULL WHERE sequence_num=2",
    "UPDATE commit_queue SET log_id=(SELECT log_id FROM commit_queue WHERE sequence_num=1) WHERE sequence_num=2",
    "UPDATE commit_queue SET mr_value=NULL WHERE sequence_num=3",
    "DELETE FROM commit_queue",
])
def test_snapshot_never_hides_missing_or_unuploaded_events(snapshot, sql):
    client, path = snapshot
    with database.get_db_connection(path) as conn:
        conn.execute(sql)
        conn.commit()
    assert client.get("/chain-state?include_history=true").status_code == 409


def test_snapshot_bound(snapshot, monkeypatch):
    client, _ = snapshot
    monkeypatch.setattr(query, "MAX_HISTORY_ENTRIES", 2)
    assert client.get("/chain-state?include_history=true").status_code == 409


@pytest.mark.parametrize("method,path,transport,uid,allowed", [
    ("GET", "/chain-state", "uds", "0", True),
    ("GET", "/chain-state", "uds", "1000", False),
    ("GET", "/chain-state", "http_compat", "0", False),
    ("POST", "/commit", "uds", "0", False),
    ("GET", "/state", "uds", "0", False),
])
def test_provider_caller_is_root_uds_read_only(method, path, transport, uid, allowed):
    request = Request({"type": "http", "method": method, "path": path, "headers": []})
    request.state.auth_transport, request.state.peer_uid = transport, uid
    assert (authorize_caller("argus_provider", request) is None) == allowed
