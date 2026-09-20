import importlib.util
from pathlib import Path

import pytest
from tc_api.trucon import database

spec = importlib.util.spec_from_file_location("backfill_uuids", Path(__file__).resolve().parents[1] / "scripts/backfill_attestation_uuids.py")
backfill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill)


def test_reference_migration_preserves_measured_material_and_is_atomic(tmp_path):
    path = str(tmp_path / "queue.db")
    database.init_db(path)
    for seq in (1, 2):
        database.insert_record(str(seq), "event-" + str(seq), {"signed": "unchanged"}, "CONFIRMED",
            sequence_num=seq, mr_value="0" * 96, event_digest="sha384:" + "a" * 96, db_path=path)
    with database.get_db_connection(path) as conn:
        conn.execute("UPDATE commit_queue SET log_id=record_id")
        conn.commit()
        before = [tuple(row) for row in conn.execute("SELECT payload,mr_value,event_digest FROM commit_queue ORDER BY sequence_num")]
    lookup = lambda origin, index: index * 64
    assert len(backfill.backfill(path, "https://rekor.example", lookup=lookup)) == 2
    with database.get_db_connection(path) as conn:
        assert conn.execute("SELECT log_id FROM commit_queue WHERE record_id='1'").fetchone()[0] == "1"
    def unavailable(origin, index):
        if index == "2": raise RuntimeError("entry unavailable")
        return index * 64
    with pytest.raises(RuntimeError):
        backfill.backfill(path, "https://rekor.example", apply=True, lookup=unavailable)
    with database.get_db_connection(path) as conn:
        assert conn.execute("SELECT log_id FROM commit_queue WHERE record_id='1'").fetchone()[0] == "1"
    backfill.backfill(path, "https://rekor.example", apply=True, lookup=lookup)
    with database.get_db_connection(path) as conn:
        assert [row[0] for row in conn.execute("SELECT log_id FROM commit_queue ORDER BY sequence_num")] == ["1" * 64, "2" * 64]
        assert [tuple(row) for row in conn.execute("SELECT payload,mr_value,event_digest FROM commit_queue ORDER BY sequence_num")] == before
