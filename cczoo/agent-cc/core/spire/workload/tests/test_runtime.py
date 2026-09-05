"""No hardware claims: exercise deployment rejection boundaries and policy rendering."""
import importlib.util
import json
import os
from pathlib import Path
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
