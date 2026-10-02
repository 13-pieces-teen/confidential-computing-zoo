import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from connection_facts import prepare, ReleaseObserver, observed_connection_class, measurement_inputs
from fact_protocol import facts_in


def test_each_lane_and_request_has_a_distinct_complete_fact(tmp_path):
    output = tmp_path / "payloads"
    prepare(output, 7, 3)
    plan = json.loads((output / "payload-plan.json").read_text())
    facts = []
    for lane in ("existing", "new", "inflight"):
        for item in plan[lane]:
            body = (output / item["body_file"]).read_text()
            actual = facts_in(json.loads(body)["query"])
            assert len(actual) == 1 and actual[0]["fact_id"] == item["fact_id"]
            facts.append(item["fact_id"])
            if lane == "inflight": assert body.index("ARGUS_FACT_V1") > 8192
    assert len(facts) == len(set(facts)) == 7


def clock(at):
    return {"at_ms": at, "monotonic_ns": at * 1000000, "clock_id": "boot:client"}


def test_fragmented_prefix_keeps_original_actual_write_interval(tmp_path):
    prepare(tmp_path / "p", 7, 3)
    plan = json.loads((tmp_path / "p/payload-plan.json").read_text())
    item = plan["inflight"][0]
    frame = json.loads((tmp_path / "p" / item["body_file"]).read_text())["query"].split(" ", 1)[1].encode()
    rows = []
    observer = ReleaseObserver([item], rows.append)
    observer.feed(frame[:10], clock(4900), clock(4920), "r", "OBSERVED")
    assert rows == []
    observer.feed(frame[10:], clock(6200), clock(6220), "r", "OBSERVED")
    assert rows[0]["at_ms"] == 4900 and rows[0]["completed_at_ms"] == 4920
    assert rows[0]["source"] == "client_tls_socket_write"
    assert rows[0]["boundary"] == "client_fact_prefix_transport_write"
    assert frame.decode() not in json.dumps(rows)  # No secret/body content is logged.
    observer.feed(frame, clock(7000), clock(7020), "r", "OBSERVED")
    assert len(rows) == 1  # No duplicate prefix record within one attempt.


class BaseConnection:
    def __init__(self):
        self.writes, self.fail = [], False
    def putrequest(self, *args, **kwargs): pass
    def putheader(self, *args, **kwargs): pass
    def send(self, data):
        self.writes.append(data)
        if self.fail:
            raise ConnectionResetError("fixture partial write outcome unknown")


def test_wrapper_observes_send_return_and_failed_send_is_not_known_release(tmp_path):
    import pytest
    prepare(tmp_path / "p", 7, 3)
    plan = json.loads((tmp_path / "p/payload-plan.json").read_text())
    item = plan["existing"][0]
    body = (tmp_path / "p" / item["body_file"]).read_bytes()
    rows, stamps = [], iter(range(6000, 6100))
    cls = observed_connection_class(BaseConnection, [item], rows.append, lambda: clock(next(stamps)))
    conn = cls()
    conn.putrequest("POST", "/")
    conn.putheader("X-Argus-Request-ID", "actual-request")
    conn.send(b"POST / HTTP/1.1\r\n\r\n")
    assert not rows
    conn.send(body)
    assert conn.writes[-1] == body
    assert rows[0]["status"] == "OBSERVED" and rows[0]["request_id"] == "actual-request"
    assert rows[0]["at_ms"] == 6001 and rows[0]["completed_at_ms"] == 6002
    conn.putrequest("POST", "/")
    conn.putheader("X-Argus-Request-ID", "retry")
    conn.send(b"POST / HTTP/1.1\r\n\r\n")
    conn.fail = True
    with pytest.raises(ConnectionResetError): conn.send(body)
    assert rows[1]["status"] == "UNKNOWN" and rows[1]["request_id"] == "retry"


def test_chunked_wrapper_strips_transport_framing_without_changing_it(tmp_path):
    prepare(tmp_path / "p", 7, 3)
    item = json.loads((tmp_path / "p/payload-plan.json").read_text())["inflight"][0]
    body = (tmp_path / "p" / item["body_file"]).read_bytes()
    rows, stamps = [], iter(range(7000, 8000))
    cls = observed_connection_class(BaseConnection, [item], rows.append, lambda: clock(next(stamps)))
    conn = cls(); conn.putrequest("POST", "/")
    conn.putheader("X-Argus-Request-ID", "slow")
    conn.putheader("Transfer-Encoding", "chunked")
    conn.send(b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n")
    split = body.index(b"ARGUS_FACT_V1") + 10
    pieces = [body[:split], body[split:]]
    for chunk in pieces:
        conn.send(("%x\r\n" % len(chunk)).encode() + chunk + b"\r\n")
    conn.send(b"0\r\n\r\n")
    assert len(rows) == 1 and rows[0]["at_ms"] == 7001
    assert conn.writes[-1] == b"0\r\n\r\n"


def test_connection_manifest_joins_fact_ids_and_attempts_without_schedule_release(tmp_path):
    prepare(tmp_path / "p", 7, 3)
    plan = json.loads((tmp_path / "p/payload-plan.json").read_text())
    fact = plan["existing"][0]
    traces = [{"type": "probe_start", "run_id": "run", "at_ms": 1000},
              {"type": "request", "run_id": "run", "request_id": "r", "fact_id": fact["fact_id"], "started_at_ms": 6500},
              {"type": "probe_stop", "run_id": "run", "at_ms": 10000, "complete": True}]
    manifest, native = measurement_inputs({"payload_plan": str(tmp_path / "p/payload-plan.json")}, traces, "run")
    assert len(manifest["facts"]) == 7
    assert native["steps"][0]["request_ids"] == ["r"]
    assert all("released_at_ms" not in s for s in native["steps"])


def test_wrapper_runs_existing_http_client_send_path_without_copying_protocol(tmp_path):
    from timeline import acceptance
    prepare(tmp_path / "p", 7, 3)
    item = json.loads((tmp_path / "p/payload-plan.json").read_text())["existing"][0]
    body = (tmp_path / "p" / item["body_file"]).read_bytes()
    rows, stamps = [], iter(range(8000, 8100))
    cls = observed_connection_class(acceptance.PinnedConnection, [item], rows.append, lambda: clock(next(stamps)))
    conn = cls("example.invalid", server_id="spiffe://test/service")
    class Socket:
        def __init__(self): self.data = []
        def sendall(self, data): self.data.append(data)
        def close(self): pass
    conn.sock = Socket()  # Local fixture only; this does not claim TLS validation.
    conn.request("POST", "/api/v1/search/find", body=body, headers={"X-Argus-Request-ID": "http-r"})
    assert len(conn.sock.data) == 2 and conn.sock.data[1] == body
    assert b"Content-Length:" in conn.sock.data[0]
    assert len(rows) == 1 and rows[0]["request_id"] == "http-r"
    assert rows[0]["status"] == "OBSERVED"


def test_legacy_unframed_body_keeps_transport_measurement_without_fact_claim(tmp_path):
    path = tmp_path / "body.json"; path.write_text('{"query":"synthetic marker"}')
    manifest, native = measurement_inputs({"body_file": str(path)}, [{"type": "probe_start", "run_id": "r", "at_ms": 1}], "r")
    assert manifest["facts"] == [] and native["steps"] == []
