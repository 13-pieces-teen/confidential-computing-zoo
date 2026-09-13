"""No hardware claims: exercise deployment rejection boundaries and policy rendering."""
import importlib.util
import json
import hashlib
import tempfile
import os
from pathlib import Path
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("argus_workload_runtime", ROOT / "scripts/workload.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class RuntimeContractTests(unittest.TestCase):
    def test_preflight_rejects_an_install_with_only_the_legacy_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            binaries = Path(directory)
            for name in ("spiffe-helper", "argus-agent-config", "argus-workload", "spiffe-authz",
                         "spiffe-mtls-probe", "argus-tdx-workloadattestor", "argus-tdx-evidence-provider"):
                (binaries / name).touch(mode=0o755)
            with patch.object(runtime, "BIN", binaries), \
                    patch.object(runtime.shutil, "which", return_value="available"), \
                    patch.object(runtime, "binary_version", return_value="1.15.3"), \
                    patch.object(runtime, "remote_check", return_value={}), \
                    patch.object(runtime, "direct_trustee", return_value="PASS"), \
                    self.assertRaisesRegex(ValueError, "missing executable: .*argus-spire-evidence-provider"):
                runtime.preflight({"approved": self.approved()})

    def test_start_rejects_both_provider_names_before_starting_services(self):
        for name in ("argus-tdx-evidence-provider", "argus-spire-evidence-provider"):
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
                    runtime.start({"previous_provider_unit": "previous-provider.service"})
                self.assertEqual(commands.call_args_list[0].args[0],
                                 ["systemctl", "stop", "previous-provider.service"])
                self.assertFalse(any(call.args[0][:2] == ["systemctl", "start"]
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
        with patch.object(runtime, "run", return_value="active"), patch.object(Path, "read_text", side_effect=FileNotFoundError):
            result = runtime.status({})
        self.assertFalse(result["ready"])
        self.assertIsNone(result["target_serial"])

    def test_status_requires_a_complete_serial(self):
        for contents, expected in (("", None), ("invalid", None), ("123\n", "123")):
            with self.subTest(contents=contents), patch.object(runtime, "run", return_value="active"), patch.object(Path, "read_text", return_value=contents):
                result = runtime.status({})
            self.assertEqual(result["target_serial"], expected)
            self.assertEqual(result["ready"], expected is not None)

    def approved(self):
        vector = json.loads((ROOT / "testdata/runtime-data.json").read_text())["runtime_data"]
        return {k: vector[k] for k in ("policy_id", "image_config_digest", "config_digest", "executable")} | {
            "mr_td": "1" * 96, "rtmr_0": "2" * 96, "rtmr_1": "3" * 96, "rtmr_2": "4" * 96}

    def test_no_implicit_baseline(self):
        c = json.loads((ROOT / "config/environment.example.json").read_text())
        with self.assertRaises(ValueError):
            runtime.baseline(c)
        c["approved"] = self.approved()
        policy = runtime.render_policy(runtime.baseline(c))
        self.assertNotIn("@IMAGE_CONFIG_DIGEST@", policy)
        self.assertIn(c["approved"]["image_config_digest"], policy)

    def test_policy_artifact_requires_exact_bytes_and_explicit_digest(self):
        c = {"approved": self.approved()}
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
        required = runtime.selectors({"approved": self.approved()}, runtime.TARGET_ID)
        good = {"id": "approved", "spiffe_id": runtime.TARGET_ID, "parent_id": runtime.AGENT_ID,
                "selectors": [{"type": s.split(":", 1)[0], "value": s.split(":", 1)[1]} for s in required],
                "additional_attributes": {"disable_x509_svid_prefetch": True}}
        runtime.audit_entries([good], required, runtime.TARGET_ID)
        for replacement in (
            {"selectors": [{"type": "unix", "value": "uid:0"}]},
            {"additional_attributes": {}},
            {"parent_id": "spiffe://argus.local/wrong"},
            {"admin": True},
        ):
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                runtime.audit_entries([good, good | replacement], required, runtime.TARGET_ID)
        with self.assertRaises(ValueError):
            runtime.audit_entries([], required, runtime.TARGET_ID)


if __name__ == "__main__":
    unittest.main()
