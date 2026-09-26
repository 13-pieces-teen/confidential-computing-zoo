import copy
import http.server
import json
import os
import socketserver
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lifecycle_evidence import rotation, resumed_launch, observe_creates


def snapshots():
    a = {"schema": "argus.lifecycle-snapshot.v1", "started_at_ms": 1000, "completed_at_ms": 1100,
         "config_sha256": "config", "workload_id": "memory", "target_id": "spiffe://test/memory",
         "status": {"ready": True, "target_serial": "10", "helper_invocation_id": "helper"},
         "target": {"pid": 42, "start_time": "123", "container_id": "c" * 64},
         "credential": {"chain_valid": True, "uri_san": ["spiffe://test/memory"], "serial": "10", "certificate_sha256": "old"},
         "launch": {"launch_id": "launch", "stage": "query_timeout", "config_sha256": "config"},
         "containers": [{"launch_id": "launch", "container_id": "c" * 64}]}
    b = copy.deepcopy(a)
    b.update(started_at_ms=2000, completed_at_ms=2100)
    b["status"]["target_serial"] = "11"
    b["credential"].update(serial="11", certificate_sha256="new")
    b["launch"].update(stage="complete", container_id="c" * 64)
    return a, b


def stream():
    return [{"schema": "argus.docker-creates.v1", "record_seq": 1, "type": "create_observer_start", "workload_id": "memory", "at_ms": 500,
             "daemon_peer_uid": 0, "daemon_peer_pid": 123, "until_ms": 3000},
            {"schema": "argus.docker-creates.v1", "record_seq": 2, "type": "create_observer_stop", "workload_id": "memory", "at_ms": 3000, "coverage_until_ms": 3000, "complete": True}]


def result():
    return {"resumed": True, "launch_id": "launch", "container_id": "c" * 64, "config_sha256": "config"}


def test_rotation_is_distinct_from_node_quote_and_business_continuity():
    a, b = snapshots()
    actual = rotation(a, b)
    assert actual["result"] == "PASS"
    assert actual["node_quote_reuse"] == actual["business_continuity"] == "NOT_RUN"
    b["target"]["start_time"] = "456"
    assert rotation(a, b)["result"] == "UNKNOWN"


def test_rotation_does_not_accept_stale_invalid_or_same_svid():
    a, b = snapshots()
    for change in (lambda v: v["status"].update(ready=False),
                   lambda v: v["credential"].update(chain_valid=False),
                   lambda v: v["status"].update(helper_invocation_id="restarted"),
                   lambda v: v["credential"].update(certificate_sha256="old")):
        bad = copy.deepcopy(b)
        change(bad)
        assert rotation(a, bad)["result"] == "UNKNOWN"


def test_same_resume_ids_require_independent_create_coverage():
    a, b = snapshots()
    checked = resumed_launch(a, b, result(), None)
    assert checked["same_operation"] == "PASS"
    assert checked["result"] == checked["duplicate_creation"] == "UNKNOWN"
    assert resumed_launch(a, b, result(), stream())["result"] == "PASS"


def test_incomplete_short_or_wrong_workload_stream_cannot_prove_no_duplicate():
    a, b = snapshots()
    for logs in (stream()[:-1], stream()[1:]):
        assert resumed_launch(a, b, result(), logs)["result"] == "UNKNOWN"
    for change in (lambda v: v[-1].update(complete=False),
                   lambda v: v[-1].update(coverage_until_ms=2000),
                   lambda v: v[0].update(workload_id="other"),
                   lambda v: v[0].update(at_ms=1500)):
        logs = stream()
        change(logs)
        assert resumed_launch(a, b, result(), logs)["result"] == "UNKNOWN"


def test_transient_second_create_fails_even_if_removed_before_after_snapshot():
    a, b = snapshots()
    logs = stream()
    logs.insert(1, {"schema": "argus.docker-creates.v1", "record_seq": 2, "type": "container_create", "workload_id": "memory",
                    "at_ms": 1600, "created_at_ns": 1500 * 1000000, "container_id": "d" * 64, "launch_id": "launch"})
    logs[-1]["record_seq"] = 3
    assert resumed_launch(a, b, result(), logs)["duplicate_creation"] == "FAIL"


def test_changed_launch_or_container_fails_without_repeating_mutation():
    a, b = snapshots()
    evidence = result()
    evidence["launch_id"] = "new-operation"
    assert resumed_launch(a, b, evidence, stream())["same_operation"] == "FAIL"


@pytest.mark.skipif(sys.platform != "linux" or getattr(os, "geteuid", lambda: -1)() != 0,
                    reason="real host Unix HTTP peer credentials require Linux root fixture")
@pytest.mark.parametrize("early_eof", [False, True])
def test_actual_unix_http_create_stream_filters_sanitizes_and_marks_early_eof(tmp_path, early_eof):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *args):
            pass
        def do_GET(self):
            query = parse_qs(urlsplit(self.path).query)
            assert json.loads(query["filters"][0])["event"] == ["create"]
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            row = {"Type": "container", "Action": "create", "timeNano": time.time_ns(),
                   "Actor": {"ID": "c" * 64, "Attributes": {"io.trucon.workload-id": "memory",
                             "io.trucon.launch-id": "launch", "private": "SECRET-not-logged"}}}
            payload = (json.dumps(row) + "\n").encode()
            self.wfile.write(("%x\r\n" % len(payload)).encode() + payload + b"\r\n")
            self.wfile.flush()
            if not early_eof:
                time.sleep(max(0, int(query["until"][0]) - time.time()) + .02)
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
    sock = tmp_path / "docker.sock"
    server = socketserver.UnixStreamServer(str(sock), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        output = tmp_path / "creates.jsonl"
        assert observe_creates(str(sock), "memory", 1, output) is (not early_eof)
        rows = [json.loads(line) for line in output.read_text().splitlines()]
        assert rows[-1]["complete"] is (not early_eof)
        assert rows[1]["container_id"] == "c" * 64
        assert "SECRET" not in output.read_text()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
