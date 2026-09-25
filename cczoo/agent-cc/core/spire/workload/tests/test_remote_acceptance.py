"""Observer tests use synthetic receipts; they are not real-TDX acceptance."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import shutil
import subprocess
import tempfile
import threading
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
                      "recovery_hold": {"verified": True, "restart": "no"}}
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

    def test_empty_or_incomplete_receiver_is_unknown(self):
        for transform in (lambda rows: [], lambda rows: rows[:-1],
                          lambda rows: [r for r in rows if r.get("type") != "received"]):
            with self.subTest(transform=transform):
                evidence = transform(self.receiver)
                value = remote.assess(self.rows, self.event, evidence, bound_ms=1000, clock_uncertainty_ms=0)
                self.assertEqual(value["result"], "UNKNOWN")

    def test_receiver_detects_delivery_even_when_client_reports_failure(self):
        self.receiver.insert(-1, {"type": "received", "run_id": "run", "request_id": "existing6", "received_body_bytes": 19})
        value = self.check()
        self.assertEqual(value["result"], "FAIL")
        self.assertEqual(value["received_body_bytes"], 19)

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
            class Receiver(BaseHTTPRequestHandler):
                protocol_version = "HTTP/1.1"
                def do_GET(self):
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
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                worker.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
