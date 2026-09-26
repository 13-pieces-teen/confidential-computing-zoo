import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import static_clients
from common import atomic, read


class StaticClientTests(unittest.TestCase):
    def test_unit_has_no_spire_dependencies_and_uses_same_private_lease(self):
        c = {"_instance": "static-a"}
        unit = static_clients.unit_bytes(c, {"cert": "/etc/test/client.pem", "key": "/etc/test/key.pem", "bundle": "/etc/test/bundle.pem"}, "/opt/experiment/argus-static-client").decode()
        self.assertNotIn("Requires=", unit)
        self.assertNotIn("spire-agent", unit)
        self.assertIn("/etc/argus-openclaw/instances/static-a/credentials.json", unit)
        self.assertIn("Group=argus-oc-static-a", unit)
        self.assertIn("ExecStopPost=", unit)

    @unittest.skipUnless(shutil.which("go"), "Go compiler required for isolated static publisher")
    def test_overlay_compiles_and_validates_real_certificate_and_existing_lease(self):
        with tempfile.TemporaryDirectory() as temporary:
            overlay = static_clients.overlay_file(temporary)
            c = read(overlay)
            test_source = Path(temporary) / "static_experiment_test.go"
            test_source.write_text((static_clients.HERE / "static-client/credentials_test.go.txt").read_text(), encoding="utf-8")
            virtual = static_clients.HELPER / "pkg/clientcredentials/static_experiment_test.go"
            self.assertFalse(virtual.exists())
            c["Replace"][str(virtual.resolve())] = str(test_source)
            atomic(overlay, c)
            result = subprocess.run(["go", "test", "-tags", "argus_experiment_static", "-overlay", str(overlay),
                                     "./pkg/clientcredentials", "-run", "TestExperimentStaticMaterialAndLease", "-count=1"],
                                     cwd=static_clients.HELPER, capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertFalse(virtual.exists(), "experiment must not write production Go source")


if __name__ == "__main__": unittest.main()
