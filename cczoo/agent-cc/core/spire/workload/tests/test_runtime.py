"""No hardware claims: exercise deployment rejection boundaries and policy rendering."""
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

    def test_status_requires_a_complete_serial(self):
        c = self.config()
        for contents, expected in (("", None), ("invalid", None), ("123\n", "123")):
            with self.subTest(contents=contents), patch.object(runtime, "run", return_value="active"), patch.object(Path, "read_text", return_value=contents):
                result = runtime.status(c)
            self.assertEqual(result["target_serial"], expected)
            self.assertEqual(result["ready"], expected is not None)

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
            "mr_td": "1" * 96, "rtmr_0": "2" * 96, "rtmr_1": "3" * 96, "rtmr_2": "4" * 96}

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


if __name__ == "__main__":
    unittest.main()
