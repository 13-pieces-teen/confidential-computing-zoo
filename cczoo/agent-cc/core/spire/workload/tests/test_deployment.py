"""Configuration reaches every consumer; fixtures do not claim real TDX."""
import importlib.util
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import test_runtime as contracts
ROOT, runtime = contracts.ROOT, contracts.runtime
from deployment import Deployment, render_services

spec = importlib.util.spec_from_file_location("nginx_hook", ROOT / "scripts/nginx-hook.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


def alternative():
    c = contracts.RuntimeContractTests().config()
    c["identity"] = {"trust_domain": "example.org", "agent_id": "spiffe://example.org/spire/agent/argus_tdx/worker-02",
                     "helper_id": "spiffe://example.org/infra/memory-broker", "target_id": "spiffe://example.org/service/memory",
                     "client_id": "spiffe://example.org/agent/assistant"}
    c["paths"] = {"install_dir": "/opt/memory-stack", "config_dir": "/etc/memory-stack", "records_dir": "/var/log/memory-stack",
                  "spire_bin_dir": "/opt/memory-spire/bin", "run_name": "memory"}
    c["workload"].update(id="memory-prod", config_path="/etc/memory/service.json", data_path="/var/lib/memory",
                         listen_port=2933, tls_port=2943, published_port=3943)
    c["business_url"] = "https://127.0.0.1:3943/health"
    return c


class DeploymentTests(unittest.TestCase):
    def test_persistent_paths_cannot_overlap_runtime_cleanup_directories(self):
        for key in ("install_dir", "config_dir", "records_dir", "spire_bin_dir"):
            for value in ("/run/memory-credentials", "/run/memory-credentials/config",
                          "/run/memory", "/run/memory-workload", "//run/memory-credentials"):
                c = alternative()
                c["paths"][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    Deployment(c)
        # Changing run_name must also detect a collision with its new directories.
        c = alternative()
        c["paths"].update(config_dir="/run/custom-authz", run_name="custom")
        with self.assertRaises(ValueError):
            Deployment(c)

    def test_remote_check_requires_the_local_entry_contract(self):
        c = alternative()
        with patch.object(Path, "read_bytes", return_value=b"approved-helper"):
            expected = {"parent_id": c["identity"]["agent_id"], "selectors": {
                c["identity"][key]: sorted(runtime.selectors(c, c["identity"][key]))
                for key in ("helper_id", "target_id")}}
            def entries(_, identity):
                return [{"id": "approved", "parent_id": expected["parent_id"], "spiffe_id": identity,
                         "selectors": [{"type": s.split(":", 1)[0], "value": s.split(":", 1)[1]}
                                       for s in expected["selectors"][identity]],
                         "additional_attributes": {"disable_x509_svid_prefetch": True}}]
            with patch.object(runtime, "run", return_value="1234"), \
                    patch.object(runtime, "binary_version", return_value="1.15.3"), \
                    patch.object(runtime, "server_entries", side_effect=entries):
                good = runtime.server_check(c)
            self.assertEqual(good["entry_contract"], expected)
            with patch.object(runtime, "run", return_value=json.dumps(good)):
                self.assertEqual(runtime.remote_check(c), good)
            mismatches = [None, {}]
            parent = copy.deepcopy(expected)
            parent["parent_id"] = "spiffe://example.org/spire/agent/argus_tdx/other"
            mismatches.append(parent)
            for key in ("helper_id", "target_id"):
                changed = copy.deepcopy(expected)
                identity = c["identity"][key]
                changed["selectors"][identity] = [value + "-other" for value in changed["selectors"][identity]]
                mismatches.append(changed)
            for value in mismatches:
                with self.subTest(contract=value), patch.object(runtime, "run", return_value=json.dumps({**good, "entry_contract": value})), self.assertRaisesRegex(ValueError, "Entry contract"):
                    runtime.remote_check(c)

    def test_render_refuses_to_replace_live_cleanup_configuration(self):
        with patch.object(runtime, "run", return_value="active"), self.assertRaisesRegex(ValueError, "stop argus-helper"):
            runtime.render(alternative())

    def test_two_configurations_reach_all_templates_and_policy(self):
        for c in (contracts.RuntimeContractTests().config(), alternative()):
            with self.subTest(domain=c["identity"]["trust_domain"]):
                d = Deployment(c)
                rendered = {p.name: d.render(p.read_text()) for directory in ("config", "systemd")
                            for p in (ROOT / directory).iterdir() if p.suffix in (".conf", ".service")}
                self.assertTrue(all("@" not in value for value in rendered.values()))
                self.assertIn(d.identity["target_id"], rendered["helper.conf"])
                self.assertIn('workload_id = "' + d.workload["id"] + '"', rendered["helper.conf"])
                self.assertIn(str(d.workload["tls_port"]), rendered["nginx.conf"])
                self.assertIn(str(d.workload["listen_port"]), rendered["nginx.conf"])
                self.assertIn(d.identity["client_id"], rendered["argus-authz.service"])
                self.assertIn(d.provider_socket.as_posix(), rendered["argus-tdx-provider.service"])
                self.assertIn(d.workload["data_path"], rendered["argus-tdx-provider.service"])
                policy = runtime.policy_bytes(c).decode()
                for value in (d.identity["agent_id"], d.workload["id"], d.workload["config_path"], str(d.workload["listen_port"])):
                    self.assertIn(json.dumps(value), policy)
                self.assertNotIn("@", policy)
                self.assertIn("UpToDate", policy)
                self.assertIn("argus_tdx:agent_id:" + d.identity["agent_id"], runtime.selectors(c, d.identity["target_id"]))

    def test_invalid_or_old_configuration_is_rejected(self):
        cases = [("schema_version", 0), ("previous_agent_unit", "old.service")]
        for key, value in cases:
            c = alternative()
            c[key] = value
            with self.assertRaises(ValueError):
                Deployment(c)
        for section, key, value in (("workload", "listen_port", True), ("workload", "tls_port", 2933),
                                    ("workload", "config_path", "/var/lib/memory/config"),
                                    ("paths", "run_name", "../wrong"), ("paths", "config_dir", "/etc/a;bad"),
                                    ("identity", "target_id", "spiffe://other.org/service/memory")):
            c = alternative()
            c[section][key] = value
            with self.subTest(section=section, key=key), self.assertRaises(ValueError):
                Deployment(c)
        c = alternative()
        del c["identity"]["agent_id"]
        with self.assertRaises(ValueError):
            Deployment(c)

    @unittest.skipUnless(sys.platform == "linux", "generated deployments use Linux absolute paths")
    def test_generated_files_and_cleanup_use_nondefault_paths(self):
        with tempfile.TemporaryDirectory(prefix="deployment-config-") as temporary:
            root = Path(temporary)
            c = alternative()
            c["paths"].update(install_dir=str(root / "install"), config_dir=str(root / "etc"),
                              records_dir=str(root / "records"), spire_bin_dir=str(root / "spire"))
            d = render_services(c)
            self.assertEqual(json.loads((d.etc / "tc-api-workload.json").read_text()), {"schema_version": 1, "workload": c["workload"]})
            self.assertEqual(d.environment.stat().st_mode & 0o777, 0o600)
            self.assertIn(str(d.environment), (d.bin / "nginx-hook.sh").read_text())
            # Exercise publication/reload command construction and actual cleanup
            # without modifying systemd or entering another process namespace.
            d.credentials, d.nginx = root / "credentials", root / "nginx"
            generation = d.credentials / "generation-42"
            generation.mkdir(parents=True)
            (generation / "key.pem").write_text("test key")
            (d.credentials / "current").symlink_to(generation.name)
            (d.credentials / "ready").write_text("42")
            def command(argv, **kwargs):
                if Path(argv[0]).name == "argus-workload":
                    self.assertIn(d.target, argv)
                    return '{"pid":"1234"}'
                return "active" if argv[:2] == ["systemctl", "is-active"] else ""
            with patch.object(hook, "Deployment", return_value=d), patch.object(hook, "run", side_effect=command) as calls:
                hook.hook(c, "publish")
                probe = calls.call_args.args[0]
                self.assertIn("https://127.0.0.1:2943", probe)
                self.assertIn(d.identity["target_id"], probe)
                self.assertIn(d.credentials / "current/key.pem", probe)
                hook.hook(c, "reload")
                hook.hook(c, "clear")
                self.assertEqual(list(d.credentials.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
