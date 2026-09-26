"""Observer tests use synthetic receipts; they are not real-TDX acceptance."""
import copy
import hashlib
import http.client
import importlib.util
import json
from pathlib import Path
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import shutil
import subprocess
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


remote = load("remote_acceptance")
node = load("node_attestation_observe")


class TrafficEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.event = {"run_id": "run", "executed": True, "started_at_ms": 5000,
                      "recovery_hold": {"verified": True, "restart": "no"},
                      "target": {"container_id": "a" * 64, "launch_id": "launch", "pid": 123, "start_time": "42"}}
        self.rows = [{"type": "probe_start", "run_id": "run", "at_ms": 0}]
        self.receiver = [{"type": "receiver_start", "run_id": "run", "at_ms": 0}]
        for lane in ("new", "existing"):
            for i, start in enumerate((1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000)):
                request_id = lane + str(i)
                ok = start < 5000
                self.rows.append({"type": "request", "run_id": "run", "request_id": request_id,
                                  "lane": lane, "started_at_ms": start, "completed_at_ms": start + 50,
                                  "ok": ok, "tls_connections": 1})
                if ok:
                    self.receiver.append({"type": "received", "run_id": "run", "request_id": request_id,
                                          "received_body_bytes": 12})
        self.rows.append({"type": "probe_stop", "run_id": "run", "at_ms": 9000, "complete": True})
        self.receiver.append({"type": "receiver_stop", "run_id": "run", "at_ms": 10000, "complete": True})
        # Versioned collector protocol; these remain synthetic test receipts.
        rebuilt = []
        for row in self.receiver:
            if row["type"] == "receiver_start":
                row.update(boundary="asgi_application_read", binding={"instance_id": "a" * 64,
                           "launch_id": "launch", "process": {"pid": 123, "start_time": "42"}})
            elif row["type"] == "received":
                stream, rid = row["request_id"], row["request_id"]
                rebuilt.extend([
                    dict(type="received", run_id="run", at_ms=100, stream_id=stream, request_id=rid,
                         chunk_seq=0, phase="request_enter", received_body_bytes=0),
                    dict(type="receive_pending", run_id="run", at_ms=100, stream_id=stream, request_id=rid, chunk_seq=1),
                    dict(type="received", run_id="run", at_ms=100, stream_id=stream, request_id=rid,
                         chunk_seq=1, phase="body_read", received_body_bytes=12, message_type="http.request", more_body=False),
                    dict(type="request_end", run_id="run", at_ms=100, stream_id=stream, request_id=rid, complete=True)])
                continue
            else:
                row.update(pending_streams=0, issues=0, requests=8)
            rebuilt.append(row)
        self.receiver = rebuilt
        for seq, row in enumerate(self.receiver, 1):
            row.update(schema_version=1, record_seq=seq, collector_id="collector", instance_id="a" * 64)

    def check(self, receiver=True):
        return remote.assess(self.rows, self.event, self.receiver if receiver else None,
                             bound_ms=1000, clock_uncertainty_ms=0)

    def test_no_receiver_cannot_pass(self):
        value = self.check(False)
        self.assertEqual(value["traffic"], "PASS")
        self.assertEqual(value["receiver_delivery"], "NOT_RUN")
        self.assertEqual(value["result"], "UNKNOWN")

    def test_complete_independent_observation_can_pass(self):
        self.assertEqual(self.check()["result"], "PASS")

    def test_legacy_coverage_cannot_claim_the_new_inflight_scenario(self):
        self.rows[0]['inflight_required'] = True
        result = self.check()
        self.assertEqual(result['result'], 'UNKNOWN')
        self.assertEqual(result['inflight_delivery'], 'UNKNOWN')

    def test_collector_gap_pending_old_contract_or_wrong_target_never_pass(self):
        original = copy.deepcopy(self.receiver)
        for mutate in (lambda logs: logs.pop(2),
                       lambda logs: logs[-1].update(complete=False),
                       lambda logs: logs[0].pop("schema_version"),
                       lambda logs: logs[0]["binding"]["process"].update(start_time="different"),
                       lambda logs: logs[-1].update(pending_streams=1),
                       lambda logs: logs[2].update(chunk_seq=100)):
            self.receiver = copy.deepcopy(original)
            mutate(self.receiver)
            self.assertEqual(self.check()["receiver_delivery"], "UNKNOWN")

    def test_truncated_receiver_file_preserves_receipts_but_marks_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receiver.jsonl"
            path.write_text(json.dumps(self.receiver[0]) + '\n{"type":"received"')
            records = remote.receiver_rows(path, "run")
            self.assertEqual(records[0], self.receiver[0])
            self.assertEqual(records[-1]["type"], "receiver_gap")
            self.assertEqual(remote.receiver_rows(path.with_name("missing"), "run")[0]["type"], "receiver_gap")

    def test_empty_or_incomplete_receiver_is_unknown(self):
        for transform in (lambda rows: [], lambda rows: rows[:-1],
                          lambda rows: [r for r in rows if r.get("type") != "received"]):
            with self.subTest(transform=transform):
                evidence = transform(self.receiver)
                value = remote.assess(self.rows, self.event, evidence, bound_ms=1000, clock_uncertainty_ms=0)
                self.assertEqual(value["result"], "UNKNOWN")

    def test_receiver_detects_delivery_even_when_client_reports_failure(self):
        self.receiver.insert(-1, {"type": "received", "run_id": "run", "request_id": "existing6", "received_body_bytes": 19,
                                 "schema_version": 1, "collector_id": "collector", "instance_id": "a" * 64})
        value = self.check()
        self.assertEqual(value["result"], "FAIL")
        self.assertEqual(value["received_body_bytes"], 19)
        self.receiver[0]["binding"]["instance_id"] = "b" * 64
        self.assertEqual(self.check()["receiver_delivery"], "UNKNOWN")

    def test_post_bound_business_success_fails_without_receiver(self):
        next(row for row in self.rows if row.get("request_id") == "new7")["ok"] = True
        self.assertEqual(self.check(False)["result"], "FAIL")

    def test_automatic_new_connection_recovery_is_unknown_without_a_boundary(self):
        del self.event["recovery_hold"]
        next(row for row in self.rows if row.get("request_id") == "new7")["ok"] = True
        self.assertEqual(self.check()["result"], "UNKNOWN")
        next(row for row in self.rows if row.get("request_id") == "existing7")["ok"] = True
        self.assertEqual(self.check()["result"], "FAIL")

    def test_old_lane_reconnect_is_not_old_connection_evidence(self):
        next(row for row in self.rows if row.get("request_id") == "existing7")["tls_connections"] = 2
        self.assertEqual(self.check()["result"], "UNKNOWN")

    def test_failed_fault_or_truncated_probe_is_unknown(self):
        self.event["executed"] = False
        self.assertEqual(self.check()["result"], "UNKNOWN")
        self.event["executed"] = True
        self.rows.pop()
        self.assertEqual(self.check()["result"], "UNKNOWN")

    def test_missing_lane_does_not_pass(self):
        self.rows = [row for row in self.rows if row.get("lane") != "existing"]
        self.assertEqual(self.check()["result"], "UNKNOWN")

    def test_existing_connection_reconnect_is_rejected_before_network(self):
        connection = remote.PinnedConnection("unused", server_id="spiffe://test/service", once=True,
                                             context=ssl.create_default_context())
        connection.connections = 1
        with patch.object(remote.http.client.HTTPSConnection, "connect") as connect:
            with self.assertRaises(ConnectionError):
                connection.connect()
            connect.assert_not_called()

    def test_wrong_identity_is_rejected_after_chain_validation(self):
        class Socket:
            def getpeercert(self):
                return {"subjectAltName": (("URI", "spiffe://test/other"),)}
            def close(self):
                pass
        connection = remote.PinnedConnection("unused", server_id="spiffe://test/service",
                                             context=ssl.create_default_context())
        with patch.object(remote.http.client.HTTPSConnection, "connect", lambda conn: setattr(conn, "sock", Socket())):
            with self.assertRaises(ssl.SSLCertVerificationError):
                connection.connect()


class NodeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.before = {"agent_id": "spiffe://test/spire/agent/argus_tdx/node", "metrics_url": "http://127.0.0.1:9988/metrics",
                       "metric_names": {"attempts": "attempts", "quote_samples": "quotes", "process_start": "start"},
                       "started_at_ms": 1000, "completed_at_ms": 1100,
                       "agent": {"present": True, "serial": "1", "expires_at": 100,
                                 "attestation_type": "argus_tdx", "banned": False},
                       "attempts": 1, "quote_samples": 1, "process_start": 0}
        self.after = copy.deepcopy(self.before)
        self.after.update(started_at_ms=2000, completed_at_ms=2100)
        self.after["agent"]["serial"] = "2"

    def test_renewal_requires_changed_serial_and_unchanged_node_counters(self):
        self.assertEqual(node.assess(self.before, self.after, "renewal")["result"], "PASS")
        self.after["attempts"] += 1
        self.assertEqual(node.assess(self.before, self.after, "renewal")["result"], "FAIL")

    def test_unknown_metrics_are_not_zero(self):
        self.before["quote_samples"] = None
        self.assertEqual(node.assess(self.before, self.after, "renewal")["result"], "UNKNOWN")

    def test_restart_and_counter_reset_are_unknown(self):
        self.after["process_start"] = 1
        self.assertEqual(node.assess(self.before, self.after, "renewal")["result"], "UNKNOWN")
        self.after["process_start"] = 0
        self.after["attempts"] = 0
        self.assertEqual(node.assess(self.before, self.after, "renewal")["result"], "UNKNOWN")

    def test_enrollment_is_separate_from_renewal(self):
        self.before["agent"] = {"present": False}
        self.before.update(attempts=0, quote_samples=0)
        self.assertEqual(node.assess(self.before, self.after, "enrollment")["result"], "PASS")
        self.assertEqual(node.assess(self.before, self.after, "renewal")["result"], "UNKNOWN")

    def test_first_process_enrollment_does_not_need_a_fabricated_zero_metric(self):
        self.before.update(agent={"present": False}, agent_observed_at_ms=1050,
                           attempts=None, quote_samples=None, process_start=None)
        self.after.update(agent_observed_at_ms=2050, process_start=1.5)
        value = node.assess(self.before, self.after, "enrollment", clock_uncertainty_ms=10)
        self.assertEqual(value["result"], "PASS")
        self.assertNotIn("node_protocol_attempts", value)
        self.after["process_start"] = .5
        self.assertEqual(node.assess(self.before, self.after, "enrollment")["result"], "UNKNOWN")

    def test_prometheus_missing_metric_and_label_sum(self):
        text = '# HELP attempts count\nattempts{result="ok"} 2\nattempts{result="error"} 1\n'
        self.assertEqual(node.metric_sum(text, "attempts"), 3)
        self.assertIsNone(node.metric_sum(text, "quotes"))

    def test_selects_exact_public_agent_record(self):
        value = {"agents": [{"id": {"trust_domain": "test", "path": "/spire/agent/argus_tdx/node"},
                             "x509_svid_serial_number": "12", "attestation_type": "argus_tdx"}]}
        self.assertEqual(node.agent_record(value, self.before["agent_id"])["serial"], "12")
        self.assertFalse(node.agent_record(value, "spiffe://test/other")["present"])


class RecoveryHoldTests(unittest.TestCase):
    class Deployment:
        def unit(self, role):
            return "argus-helper.service"

    def test_unique_owned_hold_refuses_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "argus-helper.service.d" / "90-argus-acceptance-no-restart.conf"
            commands = []
            def run(argv):
                commands.append(argv)
                return "no" if argv[1] == "show" else ""
            with patch.object(remote, "hold_path", return_value=path):
                evidence = remote.hold_recovery(self.Deployment(), run, "1" * 32)
                self.assertTrue(evidence["verified"])
                self.assertEqual(path.read_bytes(), remote.hold_contents("1" * 32))
                with self.assertRaises(FileExistsError):
                    remote.hold_recovery(self.Deployment(), run, "2" * 32)
                self.assertEqual(path.read_bytes(), remote.hold_contents("1" * 32))
                self.assertEqual(len(commands), 2)

    def test_failed_effective_restart_check_removes_only_owned_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "helper.d" / "control.conf"
            commands = []
            def run(argv):
                commands.append(argv)
                return "on-failure" if argv[1] == "show" else ""
            with patch.object(remote, "hold_path", return_value=path):
                with self.assertRaisesRegex(ValueError, "not applied"):
                    remote.hold_recovery(self.Deployment(), run, "3" * 32)
                self.assertFalse(path.exists())
                self.assertEqual(commands[-1], ["systemctl", "daemon-reload"])

    def test_release_recovers_last_complete_checkpoint_after_half_line(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "control.conf"
            owner = "4" * 32
            contents = remote.hold_contents(owner)
            path.write_bytes(contents)
            record = {"type": "fault", "executed": False, "recovery_hold": {
                "owner": owner, "path": str(path), "sha256": hashlib.sha256(contents).hexdigest()}}
            fault = root / "fault.jsonl"
            fault.write_bytes((json.dumps(record) + '\n{"type":"fault","executed":tr').encode())
            config = root / "config.json"
            config.write_text("{}")
            commands = []
            fake_workload = types.SimpleNamespace(protected_file=Path, Deployment=lambda c: self.Deployment(),
                                                 run=lambda argv: commands.append(argv))
            with patch.dict("sys.modules", {"workload": fake_workload}), \
                    patch.object(remote, "hold_path", return_value=path), patch("builtins.print"):
                remote.release(types.SimpleNamespace(config=str(config), fault=str(fault)))
            self.assertFalse(path.exists())
            self.assertEqual(commands, [["systemctl", "daemon-reload"]])

    def test_fault_checkpoint_rejects_middle_or_terminated_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fault.jsonl"
            good = json.dumps({"type": "fault", "executed": False}) + "\n"
            for data in (good + '{"type":\n', '{"type":\n' + good, '{"type":',
                         good + 'not-json', good + '{"type":oops'):
                with self.subTest(data=data):
                    path.write_text(data)
                    with self.assertRaises(ValueError):
                        remote.fault_checkpoint(path)

    def test_fault_recovery_does_not_relax_receiver_or_probe_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            path.write_text('{"type":"probe_start"}\n{"type":')
            with self.assertRaises(json.JSONDecodeError):
                remote.json_rows(path)


class ResponseReadTests(unittest.TestCase):
    def connection(self):
        return types.SimpleNamespace(serial="123", server_id="spiffe://test/service", peer_verified=True,
                                     peer_sha256="a" * 64, connections=1, close=lambda: None)

    def request(self):
        return dict(type="request", run_id="run", request_id="r", lane="existing", ok=False)

    def test_split_marker_and_chunk_timestamps_without_body_text(self):
        response = types.SimpleNamespace(status=200, length=0)
        chunks = iter([b"private-", b"sentinel", b""])
        response.read1 = lambda n: next(chunks)
        row, emitted = self.request(), []
        remote.read_response(response, row, self.connection(), emitted.append, b"private-sentinel")
        self.assertTrue(row["ok"])
        self.assertTrue(row["response_complete"])
        self.assertTrue(row["sentinel_response"])
        self.assertEqual(row["response_bytes"], 16)
        self.assertEqual([r["chunk_seq"] for r in emitted], [1, 2])
        self.assertTrue(all(r["at_ms"] >= r["read_started_at_ms"] for r in emitted))
        self.assertNotIn("private", json.dumps(emitted))
        self.assertNotIn("sentinel", json.dumps(emitted))

    def test_partial_error_bytes_remain_observable(self):
        response = types.SimpleNamespace(status=200, length=20)
        def fail(n):
            raise http.client.IncompleteRead(b"partial", 13)
        response.read1 = fail
        row, emitted = self.request(), []
        with self.assertRaises(http.client.IncompleteRead):
            remote.read_response(response, row, self.connection(), emitted.append)
        self.assertEqual(row["response_bytes"], 7)
        self.assertEqual(emitted[0]["response_bytes"], 7)
        self.assertFalse(row["ok"])
        self.assertFalse(row["response_complete"])

    def test_content_length_early_eof_and_observation_limit_do_not_succeed(self):
        response = types.SimpleNamespace(status=200, length=12, read1=lambda n: b"")
        with self.assertRaises(http.client.IncompleteRead):
            remote.read_response(response, self.request(), self.connection(), lambda row: None)
        response.read1 = lambda n: b"x" * n
        row = self.request()
        remote.read_response(response, row, self.connection(), lambda row: None, limit=4)
        self.assertEqual(row["response_bytes"], 5)
        self.assertTrue(row["incomplete"])

    def test_late_partial_response_is_failure_even_if_request_started_before_fault(self):
        fixture = TrafficEvidenceTests(); fixture.setUp()
        fixture.rows[0].update(response_observation_version=1, server_id="spiffe://test/service")
        chunk = dict(type="response_chunk", run_id="run", request_id="existing3", lane="existing",
                     at_ms=6500, monotonic_ns=1, clock_id="other-client", chunk_seq=1, response_bytes=7, http_status=200,
                     boundary="client_http_body_read", peer_verified=True, server_id="spiffe://test/service",
                     peer_sha256="a" * 64)
        fixture.rows.append(chunk)
        request = next(r for r in fixture.rows if r.get("request_id") == "existing3")
        request.update(ok=False, error="IncompleteRead", response_bytes=7)
        result = fixture.check()
        self.assertEqual(result["result"], "FAIL")
        self.assertEqual(result["client_response_delivery"], "FAIL")
        self.assertEqual(result["client_response_post_bound_bytes"], 7)
        chunk["peer_verified"] = False
        self.assertNotEqual(fixture.check()["client_response_delivery"], "FAIL")

    def test_cross_host_clock_uncertainty_is_not_bypassed_by_monotonic_values(self):
        row = dict(type="response_chunk", run_id="run", request_id="r", lane="existing", at_ms=6005,
                   monotonic_ns=10**15, clock_id="client", chunk_seq=1, response_bytes=7, http_status=200,
                   boundary="client_http_body_read", peer_verified=True, server_id="spiffe://test/service", peer_sha256="a" * 64)
        rows = [dict(type="probe_start", run_id="run", server_id="spiffe://test/service", response_observation_version=1),
                self.request() | dict(response_bytes=7, response_observation_version=1), row,
                dict(type="probe_stop", run_id="run", complete=True)]
        event = dict(run_id="run", started_at_ms=5000, started_monotonic_ns=1, clock_id="server",
                     recovery_hold=dict(verified=True, restart="no"))
        result = remote.response_observations(rows, event, bound_ms=1000, uncertainty_ms=10)
        self.assertEqual(result["client_response_delivery"], "UNKNOWN")
        self.assertEqual(result["client_response_ambiguous_bytes"], 7)

    def test_observed_late_chunk_survives_missing_final_request_row(self):
        rows = [dict(type="probe_start", run_id="run", server_id="spiffe://test/service", response_observation_version=1),
                dict(type="response_chunk", run_id="run", request_id="interrupted", lane="existing", at_ms=7000,
                     chunk_seq=1, response_bytes=4, http_status=200, boundary="client_http_body_read", peer_verified=True,
                     server_id="spiffe://test/service", peer_sha256="a" * 64)]
        event = dict(run_id="run", started_at_ms=5000, recovery_hold=dict(verified=True, restart="no"))
        result = remote.response_observations(rows, event, bound_ms=1000, uncertainty_ms=0)
        self.assertEqual(result["client_response_delivery"], "FAIL")
        self.assertEqual(result["client_response_post_bound_bytes"], 4)
        rows[1]["at_ms"] = 4000
        self.assertEqual(remote.response_observations(rows, event, bound_ms=1000, uncertainty_ms=0)["client_response_delivery"], "UNKNOWN")

    def test_rejection_body_is_reported_without_being_a_business_delivery_failure(self):
        rows = [dict(type="probe_start", run_id="run", server_id="spiffe://test/service", response_observation_version=1),
                dict(type="response_chunk", run_id="run", request_id="r", lane="existing", at_ms=7000,
                     chunk_seq=1, response_bytes=4, http_status=403, boundary="client_http_body_read", peer_verified=True,
                     server_id="spiffe://test/service", peer_sha256="a" * 64),
                self.request() | dict(response_bytes=4, response_observation_version=1),
                dict(type="probe_stop", run_id="run", complete=True)]
        event = dict(run_id="run", started_at_ms=5000, recovery_hold=dict(verified=True, restart="no"))
        result = remote.response_observations(rows, event, bound_ms=1000, uncertainty_ms=0)
        self.assertEqual(result["client_response_delivery"], "PASS")
        self.assertEqual(result["client_response_non_success_observed_bytes"], 4)
        rows[1]["synthetic_marker_seen"] = True
        self.assertEqual(remote.response_observations(rows, event, bound_ms=1000, uncertainty_ms=0)["client_response_delivery"], "FAIL")


@unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required for the local TLS integration test")
class LiveTLSProbeTests(unittest.TestCase):
    def test_chain_identity_keepalive_and_close_use_real_tls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "openssl.cnf").write_text("[req]\ndistinguished_name=dn\n[dn]\n")
            (root / "leaf.ext").write_text("basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth,clientAuth\nsubjectAltName=URI:spiffe://test/service\n")
            def openssl(*args):
                subprocess.run([shutil.which("openssl"), *args], cwd=root, check=True,
                               capture_output=True, timeout=15)
            openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=TestCA",
                    "-config", "openssl.cnf", "-addext", "basicConstraints=critical,CA:TRUE", "-keyout", "ca.key", "-out", "ca.pem")
            openssl("req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=TestLeaf", "-config", "openssl.cnf",
                    "-keyout", "leaf.key", "-out", "leaf.csr")
            openssl("x509", "-req", "-in", "leaf.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial",
                    "-days", "1", "-extfile", "leaf.ext", "-out", "leaf.pem")
            chunks = []
            class Receiver(BaseHTTPRequestHandler):
                protocol_version = "HTTP/1.1"
                def do_POST(self):
                    if self.headers.get('Transfer-Encoding') == 'chunked':
                        while True:
                            size = int(self.rfile.readline().strip(), 16)
                            if not size:
                                self.rfile.readline()
                                break
                            data = self.rfile.read(size); self.rfile.read(2)
                            chunks.append((time.monotonic(), len(data)))
                    else:
                        self.rfile.read(int(self.headers.get('Content-Length', '0')))
                    self.do_GET()
                def do_GET(self):
                    if self.path == "/partial-stream":
                        self.send_response(200)
                        self.send_header("Content-Length", "6")
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.wfile.write(b"ab"); self.wfile.flush()
                        time.sleep(.03)
                        self.wfile.write(b"cd"); self.wfile.flush()
                        self.close_connection = True
                        return
                    self.send_response(200)
                    self.send_header("Content-Length", "2")
                    if self.path == "/close":
                        self.send_header("Connection", "close")
                        self.close_connection = True
                    self.end_headers()
                    self.wfile.write(b"ok")
                def log_message(self, *args):
                    pass
            server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
            server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_context.load_cert_chain(root / "leaf.pem", root / "leaf.key")
            server_context.load_verify_locations(root / "ca.pem")
            server_context.verify_mode = ssl.CERT_REQUIRED
            server.socket = server_context.wrap_socket(server.socket, server_side=True)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            client_context = ssl.create_default_context(cafile=str(root / "ca.pem"))
            client_context.check_hostname = False
            client_context.load_cert_chain(root / "leaf.pem", root / "leaf.key")
            connection = remote.PinnedConnection("127.0.0.1", server.server_port, context=client_context,
                                                 timeout=2, server_id="spiffe://test/service", once=True)
            try:
                for path in ("/first", "/second", "/close"):
                    connection.request("GET", path)
                    self.assertEqual(connection.getresponse().read(), b"ok")
                self.assertEqual(connection.connections, 1)
                with self.assertRaises(ConnectionError):
                    connection.request("GET", "/must-not-reconnect")
                wrong = remote.PinnedConnection("127.0.0.1", server.server_port, context=client_context,
                                                 timeout=2, server_id="spiffe://test/other")
                with self.assertRaises(ssl.SSLCertVerificationError):
                    wrong.request("GET", "/")
                wrong.close()
                (root / 'body.json').write_text('{"synthetic":"abcdefghijklmnop"}')
                remote.probe(types.SimpleNamespace(url=f'https://127.0.0.1:{server.server_port}/',
                    bundle=str(root/'ca.pem'), cert=str(root/'leaf.pem'), key=str(root/'leaf.key'),
                    server_id='spiffe://test/service', run_id='chunked', output=str(root/'trace.jsonl'),
                    body_file=str(root/'body.json'), method='POST', content_type='application/json', response_marker=None,
                    api_key_env='', timeout=2, duration=1.1, interval=.1, inflight=True))
                records = remote.json_rows(root/'trace.jsonl')
                stream = next(r for r in records if r.get('type') == 'request' and r.get('lane') == 'inflight')
                self.assertTrue(stream['ok'])
                self.assertEqual(stream['tls_connections'], 1)
                self.assertGreater(len(chunks), 2)
                self.assertGreater(chunks[-1][0] - chunks[0][0], .5)
                self.assertEqual(sum(n for _, n in chunks), len((root/'body.json').read_bytes()))
                responses = [r for r in records if r.get('type') == 'response_chunk']
                self.assertTrue(responses)
                self.assertTrue(all(r['peer_verified'] and r['server_id'] == 'spiffe://test/service' for r in responses))
                self.assertEqual(sum(r['response_bytes'] for r in responses if r['request_id'] == stream['request_id']), 2)
                partial = remote.PinnedConnection("127.0.0.1", server.server_port, context=client_context,
                                                  timeout=2, server_id="spiffe://test/service", once=True)
                try:
                    partial.request("GET", "/partial-stream")
                    row, observations = dict(run_id="run", request_id="partial", lane="existing", ok=False), []
                    with self.assertRaises(http.client.IncompleteRead):
                        remote.read_response(partial.getresponse(), row, partial, observations.append)
                    self.assertEqual(row["response_bytes"], 4)
                    self.assertEqual(sum(r["response_bytes"] for r in observations), 4)
                    self.assertFalse(row["response_complete"])
                    self.assertTrue(all(r["peer_verified"] for r in observations))
                finally:
                    partial.close()
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                worker.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
