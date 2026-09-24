#!/usr/bin/env python3
"""Resolve legacy numeric Rekor indexes without changing any measured payload.

Optional operator normalization; workload admission accepts numeric indexes.
Run against a backup first. All network queries finish before one transaction
updates references. Roll back if a record being migrated disappears or no longer
has its original log_id and CONFIRMED status. Trustee verifies the fetched entry
cryptographically during admission.
"""
import argparse
import json
import re
import sqlite3
import urllib.request
from urllib.parse import urlsplit


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise ValueError("Rekor redirects are not allowed")


def resolve(origin, index):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(origin + "/api/v1/log/entries?logIndex=" + index, timeout=10) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("Rekor response exceeds size limit")
    result = json.loads(raw)
    if len(result) != 1:
        raise ValueError("expected exactly one Rekor entry")
    uuid, entry = next(iter(result.items()))
    if not re.fullmatch(r"(?:[0-9a-f]{64}|[0-9a-f]{80})", uuid) or entry["logIndex"] != int(index):
        raise ValueError("Rekor index/UUID mismatch")
    return uuid


def backfill(path, origin, *, apply=False, lookup=resolve):
    url = urlsplit(origin)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
        raise ValueError("Rekor must be a configured HTTPS origin")
    # mode=rw refuses to create a new empty database on a path typo.
    from pathlib import Path
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=rw", uri=True)
    try:
        rows = conn.execute("SELECT record_id,log_id FROM commit_queue WHERE chain_id='default' AND status='CONFIRMED' ORDER BY sequence_num").fetchall()
        changes = [(record, old, lookup(origin.rstrip("/"), old)) for record, old in rows
                   if isinstance(old, str) and old.isascii() and old.isdigit() and len(old) < 64]
        if apply:
            conn.execute("BEGIN IMMEDIATE")
            for record, old, new in changes:
                cursor = conn.execute("UPDATE commit_queue SET log_id=? WHERE record_id=? AND log_id=? AND status='CONFIRMED'", (new, record, old))
                if cursor.rowcount != 1:
                    raise ValueError("record changed while resolving UUIDs; retry")
                conn.execute("UPDATE commit_queue SET prev_log_id=? WHERE chain_id='default' AND prev_log_id=?", (new, old))
                conn.execute("UPDATE chain_state SET head_log_id=? WHERE chain_id='default' AND head_log_id=?", (new, old))
            conn.commit()
        return changes
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--rekor-url", required=True)
    parser.add_argument("--apply", action="store_true", help="write the resolved reference changes")
    args = parser.parse_args()
    print(json.dumps(backfill(args.db, args.rekor_url, apply=args.apply), indent=2))
