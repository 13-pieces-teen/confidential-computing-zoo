import json
from pathlib import Path
import tempfile
import unittest
import hashlib
import subprocess
from unittest.mock import patch

import test_runtime as contracts
import build_manifest
runtime = contracts.runtime


class ProvenanceTests(unittest.TestCase):
    def test_nested_build_includes_tracked_changes_and_new_target_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            root = repo / "cczoo/agent-cc/core/spire/workload"
            for folder in ("scripts", "config", "policy", "systemd", "target", "bin"):
                (root / folder).mkdir(parents=True)
            tracked = root / "target/linux.go"
            tracked.write_text("package target\n")
            def git(*args):
                subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
            git("init")
            git("add", ".")
            git("-c", "user.name=Argus fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture")
            tracked.write_text("package target\n// changed\n")
            added = root / "target/progress.go"
            added.write_text("package target\n// new source\n")
            (root / "bin/helper").write_bytes(b"fixture")
            result = build_manifest.generate(root, root, ["bin/helper"])
            self.assertNotEqual(result["tracked_patch_sha256"], hashlib.sha256(b"").hexdigest())
            self.assertIn(added.relative_to(repo).as_posix(), result["untracked_source_sha256"])

    def test_content_changes_are_detected_in_binaries_and_installed_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for folder in ("scripts", "config", "policy", "systemd", "bin"):
                (root / folder).mkdir()
            (root / "bin/helper").write_bytes(b"original")
            (root / "scripts/tool.py").write_text("pass\n")
            m = {"schema_version": 1, "artifacts": {"bin/helper": build_manifest.sha(root / "bin/helper")},
                 "installed_sources": {"scripts/tool.py": build_manifest.sha(root / "scripts/tool.py")}, "build_inputs": {}}
            build_manifest.verify(root, root, m, ["bin/helper"])
            (root / "scripts/tool.py").write_text("changed\n")
            with self.assertRaisesRegex(ValueError, "changed since build"):
                build_manifest.verify(root, root, m, ["bin/helper"])
            (root / "bin/helper").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "checksum"):
                build_manifest.verify(root, root, m, ["bin/helper"])

    def test_runtime_manifest_detects_mismatch_without_exposing_configuration(self):
        c = contracts.RuntimeContractTests().config()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            d = runtime.Deployment(c)
            d.install, d.bin, d.etc, d.records, d.target = root, root / "bin", root / "etc", root / "records", root / "target.json"
            d.bin.mkdir()
            (d.bin / "helper").write_bytes(b"current")
            m = {"schema_version": 1, "artifacts": {"bin/helper": "0" * 64}, "source_revision": "test"}
            (root / "build-manifest.json").write_text(json.dumps(m))
            with patch.object(runtime, "Deployment", return_value=d), patch.object(runtime, "protected_file", side_effect=Path):
                result = runtime.runtime_manifest(c)
            self.assertEqual(result["build_integrity"], "MISMATCH")
            self.assertNotIn("tc_api_user_id", json.dumps(result))
            self.assertEqual(result["remote_acceptance"], "NOT_RUN")

    def test_runtime_source_change_cannot_keep_build_integrity_match(self):
        c = contracts.RuntimeContractTests().config()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            d = runtime.Deployment(c)
            d.install, d.bin, d.etc, d.records, d.target = root, root / "bin", root / "etc", root / "records", root / "target.json"
            d.bin.mkdir()
            (root / "scripts").mkdir()
            (d.bin / "helper").write_bytes(b"current")
            source = root / "scripts/launch_state.py"
            source.write_text("original")
            m = {"schema_version": 1, "artifacts": {"bin/helper": build_manifest.sha(d.bin / "helper")},
                 "installed_sources": {"scripts/launch_state.py": build_manifest.sha(source)}}
            (root / "build-manifest.json").write_text(json.dumps(m))
            with patch.object(runtime, "Deployment", return_value=d), patch.object(runtime, "protected_file", side_effect=Path):
                self.assertEqual(runtime.runtime_manifest(c)["build_integrity"], "MATCH")
                source.write_text("changed")
                result = runtime.runtime_manifest(c)
                self.assertEqual(result["binary_integrity"], "MATCH")
                self.assertEqual(result["build_integrity"], "MISMATCH")


if __name__ == "__main__":
    unittest.main()
