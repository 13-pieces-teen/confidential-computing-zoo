import ast
import copy
import importlib.util
import json
from pathlib import Path
import sys
import shutil
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("argus_variants", HERE / "variants.py")
variants = importlib.util.module_from_spec(spec)
spec.loader.exec_module(variants)


def config():
    value = json.loads((variants.WORKLOAD / "config/environment.example.json").read_text())
    value["approved_policy_artifact"] = {"path": "/etc/approved-existing.rego", "sha256": "a" * 64}
    return value


class VariantTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("go"), "Go compiler required to execute native HCL builder")
    def test_native_builder_actually_preserves_node_and_replaces_workload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.hcl"
            source.write_text('''agent { trust_domain="argus.local" data_dir="/var/lib/preserved-agent" }
plugins {
 NodeAttestor "argus_tdx" { plugin_cmd="/old" plugin_data { proof_key_path="/existing/proof.pem" } }
 WorkloadAttestor "argus_tdx" { plugin_cmd="/old-workload" }
 WorkloadAttestor "unix" {}
}''')
            overlay = root / "overlay.hcl"
            overlay.write_text('''agent { socket_path="/run/experiment/agent.sock" experimental { broker { socket_path="/run/experiment/broker.sock" } } }
plugins { WorkloadAttestor "unix" { plugin_data { discover_workload_path=true } }
WorkloadAttestor "docker" { plugin_data {} } }''')
            builder = root / "builder.go"
            builder.write_text(variants._native_builder_source())
            binary = root / "node-binary"
            binary.write_bytes(b"fixture-node-binary-for-merger-only")
            output = root / "merged.hcl"
            result = subprocess.run(["go", "run", str(builder), "-source", str(source), "-overlay", str(overlay),
                "-output", str(output), "-node-binary", str(binary), "-agent-id", "spiffe://argus.local/spire/agent/argus_tdx/worker-01",
                "-trust-domain", "argus.local", "-evidence-socket", "/run/experiment/provider.sock"],
                cwd=variants.HELPER, capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stderr)
            text = output.read_text()
            self.assertIn('/var/lib/preserved-agent', text)
            self.assertIn('/existing/proof.pem', text)
            self.assertIn('WorkloadAttestor "docker"', text)
            self.assertNotIn('WorkloadAttestor "argus_tdx"', text)
            self.assertIn('NodeAttestor "argus_tdx"', text)

    def test_all_arms_are_executable_sources_and_isolated(self):
        with tempfile.TemporaryDirectory() as temporary:
            all_units, all_ids = set(), set()
            for name in variants.VARIANTS:
                output = Path(temporary) / name
                kwargs = {"static_credentials": {"cert": "/etc/test/cert.pem", "key": "/etc/test/key.pem", "bundle": "/etc/test/bundle.pem"}} if name == "static_mtls" else {}
                m = variants.render_variant(config(), name, output, experiment_id="trial1", **kwargs)
                self.assertFalse(m["payload_ready"])
                self.assertEqual(variants.inspect_variant(output)["effective_config"], "PASS")
                self.assertFalse(all_units.intersection(m["units"].values()))
                all_units.update(m["units"].values())
                for key in ("helper_id", "target_id"):
                    self.assertNotIn(m["config"]["identity"][key], all_ids)
                    all_ids.add(m["config"]["identity"][key])
                self.assertEqual(m["config"]["identity"]["agent_id"], config()["identity"]["agent_id"])
                self.assertEqual(m["config"]["approved_policy_artifact"], config()["approved_policy_artifact"])
                for script in (output / "package/scripts").glob("*.py"):
                    ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
                self.assertIn("ARGUS_EXPERIMENT_INSTALL", (output / "package/scripts/install.sh").read_text())

    def test_tampering_and_unlisted_dropins_are_not_mode_labels(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "no-close"
            m = variants.render_variant(config(), "no_close", output, experiment_id="trial")
            unit = output / "effective" / (m["units"]["nginx"] + ".service")
            unit.write_text(unit.read_text() + "BindsTo=argus-helper.service\n")
            with self.assertRaisesRegex(ValueError, "changed"):
                variants.inspect_variant(output)

    def test_old_output_and_implicit_policy_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "already exists"):
                variants.render_variant(config(), "full_argus", temporary, experiment_id="trial")
            c = config()
            del c["approved_policy_artifact"]
            with self.assertRaisesRegex(ValueError, "approved_policy_artifact"):
                variants.render_variant(c, "full_argus", Path(temporary) / "new", experiment_id="trial")

    def test_native_has_real_official_selectors_and_builder(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "native"
            variants.render_variant(config(), "native_spire_guarded", output, experiment_id="trial", native_uid=1234)
            text = (output / "package/scripts/workload.py").read_text()
            self.assertIn('"unix:uid:1234"', text)
            self.assertIn('"docker:image_config_digest:"', text)
            self.assertIn('"docker:label:io.trucon.workload-id:"', text)
            self.assertNotIn('WorkloadAttestor "argus_tdx"', text)
            self.assertIn('WorkloadAttestor\\x00argus_tdx\\x00', (output / "native-config-builder.go").read_text())
            self.assertIn("plugins.Items = kept", (output / "native-config-builder.go").read_text())

    def test_no_close_retains_cleanup_but_no_implicit_service_stop(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "no-close"
            m = variants.render_variant(config(), "no_close", output, experiment_id="trial")
            helper = (output / "effective" / (m["units"]["helper"] + ".service")).read_text()
            nginx = (output / "effective" / (m["units"]["nginx"] + ".service")).read_text()
            self.assertIn("nginx-hook.sh clear", helper)
            self.assertNotIn("nginx-hook.sh stop", helper)
            self.assertNotIn("BindsTo=", nginx)
            self.assertIn("WatchdogSec=5s", helper)


if __name__ == "__main__":
    unittest.main()
