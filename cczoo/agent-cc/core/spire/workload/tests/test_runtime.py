"""No hardware claims: exercise deployment rejection boundaries and policy rendering."""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import hashlib
import tempfile
import sys
import os
from pathlib import Path
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("argus_workload_runtime", ROOT / "scripts/workload.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class RuntimeContractTests(unittest.TestCase):
    def config(self):
        c = json.loads((ROOT / "config/environment.example.json").read_text())
        c["approved"] = self.approved()
        return c

    def test_preflight_requires_the_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            binaries = Path(directory)
            for name in ("spiffe-helper", "argus-agent-config", "argus-workload", "spiffe-authz",
                         "spiffe-mtls-probe", "argus-tdx-workloadattestor"):
                (binaries / name).touch(mode=0o755)
            c = self.config()
            deployment = runtime.Deployment(c)
            deployment.bin = binaries
            with patch.object(runtime, "Deployment", return_value=deployment), \
                    patch.object(runtime.shutil, "which", return_value="available"), \
                    patch.object(runtime, "binary_version", return_value="1.15.3"), \
                    patch.object(runtime, "remote_check", return_value={}), \
                    patch.object(runtime, "direct_trustee", return_value="PASS"), \
                    self.assertRaisesRegex(ValueError, "missing executable: .*argus-spire-evidence-provider"):
                runtime.preflight(c)

    def test_start_rejects_running_processes_before_starting_services(self):
        for name in ("spire-agent", "argus-spire-evidence-provider"):
            with self.subTest(provider=name):
                process_command = "/usr/local/bin/" + name + " --socket-path /run/argus/evidence-provider.sock"
                def running_process(argv, **kwargs):
                    if argv[0] == "pgrep" and re.search(argv[2], process_command):
                        return "2345"
                    return ""
                with patch.object(runtime, "preflight", return_value={}), \
                        patch.object(runtime, "render"), \
                        patch.object(runtime, "run", side_effect=running_process) as commands, \
                        self.assertRaisesRegex(ValueError, name + " is still running with PID\\(s\\) 2345"):
                    runtime.start({})
                self.assertFalse(any(call.args[0][0] == "systemctl"
                                     for call in commands.call_args_list))

    def test_start_waits_for_sequential_evidence_and_trustee_requests(self):
        # Readiness after the old 65s deadline must still succeed, without sleep.
        with patch.object(runtime, "preflight", return_value={}), \
                patch.object(runtime, "render"), \
                patch.object(runtime, "run", return_value="") as commands, \
                patch.object(runtime.time, "monotonic", side_effect=[0, 110]), \
                patch.object(runtime, "status", return_value={"ready": True}):
            self.assertTrue(runtime.start(self.config())["ready"])
        self.assertFalse(any(call.args[0][:2] == ["systemctl", "stop"] for call in commands.call_args_list))

    def test_start_still_stops_after_the_configured_budget(self):
        with patch.object(runtime, "preflight", return_value={}), \
                patch.object(runtime, "render"), \
                patch.object(runtime, "run", return_value="") as commands, \
                patch.object(runtime.time, "monotonic", side_effect=[0, 126]), \
                self.assertRaises(TimeoutError):
            runtime.start(self.config())
        commands.assert_called_with(["systemctl", "stop", "argus-helper.service"])

    def test_tc_api_does_not_forward_credentials_on_redirect(self):
        received = []
        class Destination(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{}')
            def log_message(self, *args):
                pass
        destination = ThreadingHTTPServer(("127.0.0.1", 0), Destination)
        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{destination.server_port}/foreign")
                self.end_headers()
            def log_message(self, *args):
                pass
        redirect = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        for server in (destination, redirect):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with patch.dict(os.environ, {"TC_API_BEARER_TOKEN": "test-secret"}), self.assertRaises(HTTPError):
                runtime.tc_request({"tc_api_url": f"http://127.0.0.1:{redirect.server_port}"}, "/api/launch-result/test")
            self.assertEqual(received, [])
        finally:
            for server in (redirect, destination):
                server.shutdown()
                server.server_close()

    def test_status_handles_readiness_removal(self):
        c = self.config()
        with patch.object(runtime, "run", return_value="active"), patch.object(Path, "read_text", side_effect=FileNotFoundError):
            result = runtime.status(c)
        self.assertFalse(result["ready"])
        self.assertIsNone(result["target_serial"])

    def test_status_rejects_legacy_or_incomplete_readiness(self):
        c = self.config()
        for contents in ("", "invalid", "123\n", '{}'):
            with self.subTest(contents=contents), patch.object(runtime, "run", return_value="active"), \
                    patch.object(runtime, "protected_file", side_effect=Path), patch.object(Path, "read_text", return_value=contents):
                result = runtime.status(c)
            self.assertIsNone(result["target_serial"])
            self.assertFalse(result["ready"])

    def test_readiness_binds_current_invocation_target_and_unexpired_certificate(self):
        c = self.config()
        target = {"agent_id": c["identity"]["agent_id"], "workload_id": c["workload"]["id"], "pid": "123"}
        good = {"schema_version": 1, "invocation_id": "1" * 32, "serial": "123",
                "expires_at": "2099-01-01T00:00:00.123456789Z", "target": target}
        cases = [(good, True), (dict(good, invocation_id="2" * 32), False),
                 (dict(good, expires_at="2000-01-01T00:00:00Z"), False),
                 (dict(good, expires_at="2099-01-01T00:00:00"), False),
                 (dict(good, target=dict(target, pid="124")), False), (dict(good, serial="123x"), False)]
        for receipt, expected in cases:
            with self.subTest(receipt=receipt), patch.object(runtime, "protected_file", side_effect=Path), \
                    patch.object(Path, "read_text", side_effect=[json.dumps(receipt), json.dumps(target)]), \
                    patch.object(runtime, "run", side_effect=lambda argv, **kw: "1" * 32 if "show" in argv else "active"):
                self.assertEqual(runtime.status(c)["ready"], expected)

    def verify_records(self):
        target = json.loads((ROOT / "testdata/runtime-data.json").read_text())["runtime_data"]
        target["launch_id"] = "launch-1"
        fields = " ".join(f"{k}={target[k]}" for k in ("launch_id", "container_id", "pid", "start_time"))
        fields += " policy=" + target["policy_id"]
        records = []
        for unit, message in (
            ("argus-helper", "workload subscription " + fields),
            ("argus-workload-agent", "workload EAR accepted " + fields + " nonce=" + "A" * 43 + " ear_sha256=" + "a" * 64),
            ("argus-helper", "target SVID published serial=123 expires=2026-09-17T01:00:00Z " + fields),
        ):
            records.append({"_SYSTEMD_UNIT": unit + ".service", "_SYSTEMD_INVOCATION_ID": "1" * 32,
                            "_BOOT_ID": target["boot_id"].replace("-", ""),
                            "MESSAGE": "2026/09/17 00:00:00 " + message})
        return target, records

    def run_verify(self, target, records, *, invocation_changed=False):
        proof = {"client_spiffe_id": "spiffe://argus.local/agent/openclaw", "server_serial": "123"}
        invocations = iter(("1" * 32, ("2" if invocation_changed else "1") * 32))
        def command(argv, **kwargs):
            if Path(argv[0]).name == "argus-workload":
                return json.dumps(target)
            if Path(argv[0]).name == "spiffe-mtls-probe":
                return json.dumps(proof)
            if argv[0] == "journalctl":
                self.assertEqual(argv[-2:], ["-o", "json"])
                return "\n".join(json.dumps(record) for record in records)
            if argv[:2] == ["systemctl", "show"]:
                return next(invocations)
            self.fail(f"unexpected command: {argv}")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "start.json").write_text(json.dumps({"started_at": "2026-09-17T00:00:00Z"}))
            c = self.config()
            deployment = runtime.Deployment(c)
            deployment.records = root
            with patch.object(runtime, "Deployment", return_value=deployment), \
                    patch.object(runtime, "protected_file", side_effect=Path), \
                    patch.object(runtime, "status", return_value={"ready": True, "target_serial": "123"}), \
                    patch.object(runtime, "remote_check", return_value={}), \
                    patch.object(runtime, "run", side_effect=command):
                return runtime.verify(c)

    def test_verify_accepts_exact_current_invocation_and_rotation(self):
        for wrapper in ('{}', 'level=info msg="{}" plugin_name=argus_tdx'):
            with self.subTest(wrapper=wrapper):
                target, records = self.verify_records()
                records[1]["MESSAGE"] = wrapper.format(records[1]["MESSAGE"])
                # An earlier serial from the same subscription is not a new appraisal.
                rotation = dict(records[-1], MESSAGE=records[-1]["MESSAGE"].replace("serial=123 ", "serial=122 "))
                records.insert(2, rotation)
                result = self.run_verify(target, records)
                self.assertEqual(result["appraisal"], records[1]["MESSAGE"])
                self.assertEqual(result["helper_invocation_id"], "1" * 32)

    def test_verify_rejects_prefixes_and_unrelated_events(self):
        for case in ("launch prefix", "serial prefix", "wrong policy", "wrong container", "reused pid",
                     "old invocation", "other unit", "other boot", "missing subscription",
                     "ear before subscription", "publication before ear", "duplicate field", "missing binding"):
            with self.subTest(case=case):
                target, records = self.verify_records()
                if case == "launch prefix":
                    records[1]["MESSAGE"] = records[1]["MESSAGE"].replace("launch-1 ", "launch-10 ")
                elif case == "serial prefix":
                    records[2]["MESSAGE"] = records[2]["MESSAGE"].replace("serial=123 ", "serial=1234 ")
                elif case == "wrong policy":
                    records[1]["MESSAGE"] = records[1]["MESSAGE"].replace("policy=" + target["policy_id"], "policy=other")
                elif case == "wrong container":
                    records[2]["MESSAGE"] = records[2]["MESSAGE"].replace(target["container_id"], "b" * 64)
                elif case == "reused pid":
                    records[1]["MESSAGE"] = records[1]["MESSAGE"].replace("start_time=" + target["start_time"], "start_time=9999999")
                elif case == "old invocation":
                    records[2]["_SYSTEMD_INVOCATION_ID"] = "2" * 32
                elif case == "other unit":
                    records[1]["_SYSTEMD_UNIT"] = "argus-helper.service"
                elif case == "other boot":
                    records[1]["_BOOT_ID"] = "2" * 32
                elif case == "missing subscription":
                    records.pop(0)
                elif case == "ear before subscription":
                    records[0], records[1] = records[1], records[0]
                elif case == "publication before ear":
                    records[1], records[2] = records[2], records[1]
                elif case == "duplicate field":
                    records[1]["MESSAGE"] += " launch_id=launch-10"
                elif case == "missing binding":
                    records[2]["MESSAGE"] = "target SVID published serial=123 expires=unknown"
                with self.assertRaisesRegex(ValueError, "missing correlated EAR"):
                    self.run_verify(target, records)

    def test_verify_rejects_helper_restart_during_verification(self):
        target, records = self.verify_records()
        with self.assertRaisesRegex(ValueError, "changed during verification"):
            self.run_verify(target, records, invocation_changed=True)

    def approved(self):
        vector = json.loads((ROOT / "testdata/runtime-data.json").read_text())["runtime_data"]
        return {k: vector[k] for k in ("policy_id", "image_config_digest", "config_digest", "executable")} | {
            "mr_td": "1" * 96, "rtmr_0": "2" * 96, "rtmr_1": "3" * 96, "rtmr2_baseline": "4" * 96}

    def test_no_implicit_baseline(self):
        c = json.loads((ROOT / "config/environment.example.json").read_text())
        with self.assertRaises(ValueError):
            runtime.baseline(c)
        c["approved"] = self.approved()
        policy = runtime.policy_bytes(c).decode()
        self.assertNotIn("@IMAGE_CONFIG_DIGEST@", policy)
        self.assertIn(c["approved"]["image_config_digest"], policy)

    def test_policy_artifact_requires_exact_bytes_and_explicit_digest(self):
        c = self.config()
        # A PoC-looking ID alone never drops the strict UpToDate requirement.
        c['approved']['policy_id'] = 'poc-ignore-tcb'
        self.assertIn(b'UpToDate', runtime.policy_bytes(c))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'reviewed.rego'
            data = b'# explicitly reviewed bytes\r\npackage policy\r\n'
            path.write_bytes(data)
            c['approved_policy_artifact'] = {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest()}
            with patch.object(runtime, 'protected_file', side_effect=Path):
                self.assertEqual(runtime.policy_bytes(c), data)
                path.write_bytes(data.replace(b'\r\n', b'\n'))
                with self.assertRaisesRegex(ValueError, 'SHA-256'):
                    runtime.policy_bytes(c)
            del c['approved_policy_artifact']['sha256']
            with self.assertRaises(ValueError):
                runtime.policy_bytes(c)

    def test_audit_all_same_identity_entries(self):
        c = self.config()
        target_id, agent_id = c["identity"]["target_id"], c["identity"]["agent_id"]
        required = runtime.selectors(c, target_id)
        good = {"id": "approved", "spiffe_id": target_id, "parent_id": agent_id,
                "selectors": [{"type": s.split(":", 1)[0], "value": s.split(":", 1)[1]} for s in required],
                "additional_attributes": {"disable_x509_svid_prefetch": True}}
        runtime.audit_entries(c, [good], required, target_id)
        for replacement in (
            {"selectors": [{"type": "unix", "value": "uid:0"}]},
            {"additional_attributes": {}},
            {"parent_id": "spiffe://argus.local/wrong"},
            {"admin": True},
        ):
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                runtime.audit_entries(c, [good, good | replacement], required, target_id)
        with self.assertRaises(ValueError):
            runtime.audit_entries(c, [], required, target_id)


class RemoteClientVerificationTests(unittest.TestCase):
    def approved(self):
        vector = json.loads((ROOT / "testdata/runtime-data.json").read_text())["runtime_data"]
        return {k: vector[k] for k in ("policy_id", "image_config_digest", "config_digest", "executable")} | {
            "mr_td": "1" * 96, "rtmr_0": "2" * 96, "rtmr_1": "3" * 96, "rtmr2_baseline": "4" * 96}

    def config(self):
        c = json.loads((ROOT / "config/environment.example.json").read_text())
        c["approved"] = self.approved()
        return c

    def target(self):
        vector = json.loads((ROOT / "testdata/runtime-data.json").read_text())["runtime_data"]
        return {k: vector[k] for k in ("agent_id", "boot_id", "config_digest", "config_path", "container_id",
                                       "executable", "image_config_digest", "launch_id", "listen_port", "pid",
                                       "policy_id", "start_time", "workload_id")} | {"launch_id": "launch-1"}

    def journal_rows(self, target, serial="123"):
        fields = " ".join(f"{k}={target[k]}" for k in ("launch_id", "container_id", "pid", "start_time"))
        fields += " policy=" + target["policy_id"]
        return [json.dumps({"_SYSTEMD_UNIT": unit + ".service", "_SYSTEMD_INVOCATION_ID": "1" * 32,
                            "_BOOT_ID": target["boot_id"].replace("-", ""),
                            "MESSAGE": "2026/09/17 00:00:00 " + message})
                for unit, message in (
                    ("argus-helper", "workload subscription " + fields),
                    ("argus-workload-agent", "workload EAR accepted " + fields + " nonce=" + "A" * 43 + " ear_sha256=" + "a" * 64),
                    ("argus-helper", f"target SVID published serial={serial} expires=2026-09-17T01:00:00Z " + fields))]

    def probe_setup(self):
        c = self.config()
        deployment = runtime.Deployment(c)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        records = Path(temporary.name) / "records"
        records.mkdir()
        target = self.target()
        (records / "target.json").write_text(json.dumps(target))
        (records / "launch-state.json").write_text(json.dumps({"run_id": "p0-test", "stage": "complete"}))
        (records / "start.json").write_text(json.dumps({"started_at": "2026-09-17T00:00:00Z"}))
        deployment.records = records
        deployment.target = records / "target.json"
        status_value = {"units": {}, "ready": True, "target_serial": "123", "helper_invocation_id": "1" * 32}

        def command(argv, **kwargs):
            if Path(argv[0]).name == "argus-workload":
                return json.dumps(target)
            return ""
        with patch.object(runtime, "Deployment", return_value=deployment), \
                patch.object(runtime, "status", return_value=dict(status_value)), \
                patch.object(runtime, "protected_file", side_effect=Path), \
                patch.object(runtime, "run", side_effect=command):
            request = runtime.probe_request(c, window_seconds=600)
        return c, deployment, records, target, status_value, request

    def returned(self, request, deployment):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        window_start = datetime.fromisoformat(request["not_before"])
        checked_at = (window_start + timedelta(seconds=60)).isoformat()
        output = {"checked_at": checked_at, "client_spiffe_id": deployment.allowed_client_ids[0],
                  "server_spiffe_id": request["service_identity"], "server_serial": "123",
                  "http_status": 200, "result": "PASS"}
        (directory / "probe-output.json").write_text(json.dumps(output))
        receipt = {"schema_version": 1, "probe_id": request["probe_id"], "run_id": request["run_id"],
                   "collected_at": checked_at, "probe_output_sha256": "",
                   "server_identity_seen": request["service_identity"]}
        self.refresh(directory, output, receipt)
        return directory, output, receipt

    def refresh(self, directory, output, receipt):
        (directory / "probe-output.json").write_text(json.dumps(output))
        receipt["probe_output_sha256"] = hashlib.sha256((directory / "probe-output.json").read_bytes()).hexdigest()
        (directory / "probe-receipt.json").write_text(json.dumps(receipt))
        with (directory / "SHA256SUMS").open("w") as stream:
            for name in ("probe-output.json", "probe-receipt.json"):
                stream.write(hashlib.sha256((directory / name).read_bytes()).hexdigest() + "  " + name + "\n")

    def run_probe_verify(self, deployment, records, target, status_value, request, directory, *, target_command=None):
        def command(argv, **kwargs):
            if Path(argv[0]).name == "argus-workload":
                return json.dumps(target if target_command is None else target_command())
            if argv[0] == "journalctl":
                return "\n".join(self.journal_rows(target))
            if argv[:2] == ["systemctl", "show"]:
                return "1" * 32
            return ""
        with patch.object(runtime, "Deployment", return_value=deployment), \
                patch.object(runtime, "status", return_value=dict(status_value)), \
                patch.object(runtime, "protected_file", side_effect=Path), \
                patch.object(runtime, "remote_check", return_value={}), \
                patch.object(runtime, "run", side_effect=command):
            return runtime.probe_verify(self.config(), request["probe_id"], str(directory))

    def test_preflight_local_still_requires_client_files_remote_defers_them(self):
        with tempfile.TemporaryDirectory() as directory:
            binaries = Path(directory)
            for name in ("spiffe-helper", "argus-agent-config", "argus-workload", "spiffe-authz",
                         "spiffe-mtls-probe", "argus-tdx-workloadattestor", "argus-spire-evidence-provider"):
                (binaries / name).touch(mode=0o755)
            c = self.config()
            # Deterministic on any host: these credential files never exist here.
            for k in ("client_cert", "client_key", "client_bundle"):
                c[k] = str(binaries / ("missing-" + k + ".pem"))
            target = self.target()
            deployment = runtime.Deployment(c)
            deployment.bin = binaries

            def command(argv, **kwargs):
                if Path(argv[0]).name == "spiffe-helper":
                    return "0.11.0-argus.1"
                if Path(argv[0]).name == "argus-workload":
                    return json.dumps(target)
                return ""

            with patch.object(runtime, "Deployment", return_value=deployment), \
                    patch.object(runtime.shutil, "which", return_value="available"), \
                    patch.object(runtime, "binary_version", return_value="1.15.3"), \
                    patch.object(runtime, "remote_check", return_value={}), \
                    patch.object(runtime, "direct_trustee", return_value="PASS"), \
                    patch.object(runtime, "run", side_effect=command), \
                    patch.object(runtime.Path, "is_dir", return_value=True), \
                    self.assertRaises(OSError):
                runtime.preflight(c)
            with patch.object(runtime, "Deployment", return_value=deployment), \
                    patch.object(runtime.shutil, "which", return_value="available"), \
                    patch.object(runtime, "binary_version", return_value="1.15.3"), \
                    patch.object(runtime, "remote_check", return_value={}), \
                    patch.object(runtime, "direct_trustee", return_value="PASS"), \
                    patch.object(runtime, "protected_file", side_effect=Path), \
                    patch.object(runtime, "run", side_effect=command), \
                    patch.object(runtime.Path, "is_dir", return_value=True):
                result = runtime.preflight(c, client_mode="remote")
            self.assertEqual(result["client_credentials"], "REMOTE_CLIENT_VERIFICATION")

    def test_probe_request_and_remote_verification_roundtrip(self):
        c, deployment, records, target, status_value, request = self.probe_setup()
        self.assertRegex(request["probe_id"], r"[0-9a-f]{32}")
        self.assertEqual(request["client_verification"], "REMOTE_CLIENT_VERIFICATION")
        self.assertEqual(request["service_identity"], c["identity"]["target_id"])
        self.assertEqual(request["allowed_client_ids"], list(deployment.allowed_client_ids))
        self.assertEqual(request["target_sha256"], hashlib.sha256((records / "target.json").read_bytes()).hexdigest())
        self.assertEqual(request["instance"]["launch_id"], "launch-1")
        self.assertEqual(request["target_serial"], "123")
        self.assertEqual((records / ("probe-request-" + request["probe_id"] + ".json")).stat().st_mode & 0o777, 0o600)
        directory, output, receipt = self.returned(request, deployment)
        result = self.run_probe_verify(deployment, records, target, status_value, request, directory)
        self.assertEqual(result["client_verification"], "REMOTE_CLIENT_VERIFICATION")
        self.assertEqual(result["client_verification_label"], "远端客户端验证")
        self.assertFalse(result["local_verify_executed"])
        self.assertEqual(result["remote"]["server_serial"], "123")
        self.assertEqual(result["target"], target)
        self.assertTrue((records / ("probe-verify-" + request["probe_id"] + ".json")).is_file())

    def test_probe_verify_never_passes_without_valid_remote_evidence(self):
        c, deployment, records, target, status_value, request = self.probe_setup()
        window_start = datetime.fromisoformat(request["not_before"])
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        cases = [("no returned artifacts", lambda: Path(empty.name), None, None, "lack SHA256SUMS")]

        def digest_case():
            directory, output, receipt = self.returned(request, deployment)
            (directory / "SHA256SUMS").write_text("0" * 64 + "  probe-output.json\n" + "1" * 64 + "  probe-receipt.json\n")
            return directory
        cases.append(("digest mismatch", digest_case, None, None, "differs from its digest"))

        def checked_at_case():
            directory, output, receipt = self.returned(request, deployment)
            output["checked_at"] = (window_start - timedelta(seconds=60)).isoformat()
            self.refresh(directory, output, receipt)
            return directory
        cases.append(("checked_at outside window", checked_at_case, None, None, "checked_at is outside the agreed validation window"))

        def collected_case():
            directory, output, receipt = self.returned(request, deployment)
            receipt["collected_at"] = (window_start - timedelta(seconds=60)).isoformat()
            self.refresh(directory, output, receipt)
            return directory
        cases.append(("collection time outside window", collected_case, None, None, "collection time is outside"))

        def receipt_bind_case():
            directory, output, receipt = self.returned(request, deployment)
            receipt["probe_id"] = "0" * 32
            self.refresh(directory, output, receipt)
            return directory
        cases.append(("receipt does not bind", receipt_bind_case, None, None, "does not bind this probe request"))

        def receipt_digest_case():
            directory, output, receipt = self.returned(request, deployment)
            receipt["probe_output_sha256"] = "0" * 64
            (directory / "probe-receipt.json").write_text(json.dumps(receipt))
            with (directory / "SHA256SUMS").open("w") as stream:
                for name in ("probe-output.json", "probe-receipt.json"):
                    stream.write(hashlib.sha256((directory / name).read_bytes()).hexdigest() + "  " + name + "\n")
            return directory
        cases.append(("receipt output digest differs", receipt_digest_case, None, None, "receipt output digest differs"))

        def receipt_identity_case():
            directory, output, receipt = self.returned(request, deployment)
            receipt["server_identity_seen"] = "spiffe://argus.local/service/other"
            self.refresh(directory, output, receipt)
            return directory
        cases.append(("receipt disagrees with tool output", receipt_identity_case, None, None, "disagrees with the tool output"))

        def client_identity_case():
            directory, output, receipt = self.returned(request, deployment)
            output["client_spiffe_id"] = "spiffe://argus.local/agent/other"
            self.refresh(directory, output, receipt)
            return directory
        cases.append(("client identity not allowed", client_identity_case, None, None, "outside the allowed client identities"))

        def server_identity_case():
            directory, output, receipt = self.returned(request, deployment)
            output["server_spiffe_id"] = "spiffe://argus.local/service/other"
            receipt["server_identity_seen"] = output["server_spiffe_id"]
            self.refresh(directory, output, receipt)
            return directory
        cases.append(("wrong service identity", server_identity_case, None, None, "different service identity"))

        def http_case():
            directory, output, receipt = self.returned(request, deployment)
            output["http_status"] = 500
            self.refresh(directory, output, receipt)
            return directory
        cases.append(("HTTP result not successful", http_case, None, None, "not a successful response"))

        def result_case():
            directory, output, receipt = self.returned(request, deployment)
            output["result"] = "FAIL"
            self.refresh(directory, output, receipt)
            return directory
        cases.append(("tool did not report PASS", result_case, None, None, "did not report PASS"))

        def serial_case():
            directory, output, receipt = self.returned(request, deployment)
            output["server_serial"] = "999"
            self.refresh(directory, output, receipt)
            return directory
        cases.append(("peer serial mismatch", serial_case, None, None, "does not match the current target SVID"))

        cases.append(("expired window", lambda: self.returned(request, deployment)[0],
                      lambda r: {**r, "not_after": (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()},
                      None, "window expired"))
        cases.append(("SVID rotated in window", lambda: self.returned(request, deployment)[0], None,
                      lambda s: s.update({"target_serial": "456"}), "rotated within the validation window"))
        cases.append(("Helper invocation changed", lambda: self.returned(request, deployment)[0], None,
                      lambda s: s.update({"helper_invocation_id": "2" * 32}), "Helper invocation changed"))
        cases.append(("workload not ready", lambda: self.returned(request, deployment)[0], None,
                      lambda s: s.update({"ready": False}), "not ready for remote verification"))
        for name, build, request_mutation, status_mutation, expected in cases:
            with self.subTest(case=name):
                directory = build()
                mutated = dict(request)
                if request_mutation is not None:
                    mutated = request_mutation(mutated)
                (records / ("probe-request-" + request["probe_id"] + ".json")).write_text(json.dumps(mutated))
                current_status = dict(status_value)
                if status_mutation is not None:
                    status_mutation(current_status)
                with self.assertRaisesRegex(ValueError, expected):
                    self.run_probe_verify(deployment, records, target, current_status, request, directory)

    def test_probe_verify_rejects_instance_and_fact_changes_and_missing_request(self):
        c, deployment, records, target, status_value, request = self.probe_setup()
        directory, output, receipt = self.returned(request, deployment)
        (records / "target.json").write_text(json.dumps(dict(target, pid="999")))
        with self.assertRaisesRegex(ValueError, "target instance changed"):
            self.run_probe_verify(deployment, records, target, status_value, request, directory)
        (records / "target.json").write_text(json.dumps(target))
        with self.assertRaisesRegex(ValueError, "instance facts changed"):
            self.run_probe_verify(deployment, records, target, status_value, request, directory,
                                  target_command=lambda: dict(target, pid="999"))
        with self.assertRaises(OSError):
            self.run_probe_verify(deployment, records, target, status_value, {**request, "probe_id": "f" * 32}, directory)


if __name__ == "__main__":
    unittest.main()
