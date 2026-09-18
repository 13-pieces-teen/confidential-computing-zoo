"""Exercise built deployment binaries and SPIRE contracts; no node join or TDX claim."""
import configparser
import hashlib
import http.client
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import test_runtime as contracts
from test_deployment import alternative

runtime = contracts.runtime
EXAMPLE = contracts.RuntimeContractTests().config()
AGENT_ID = EXAMPLE["identity"]["agent_id"]


def unused_loopback_port():
    # SPIRE treats bind_port=0 as its default 8081, not an ephemeral port.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@unittest.skipUnless(sys.platform == "linux" and os.environ.get("ARGUS_WORKLOAD_TOOLS_DIR") and (Path(os.environ["ARGUS_WORKLOAD_TOOLS_DIR"]) / "argus-spire-evidence-provider").is_file(),
                     "requires the built Linux Evidence Provider")
class ProviderDeploymentTests(unittest.TestCase):
    def test_systemd_command_preserves_identity_and_both_versioned_routes(self):
        unit = configparser.ConfigParser(interpolation=None)
        unit.read_string(runtime.Deployment(EXAMPLE).render((runtime.PACKAGE / "systemd/argus-tdx-provider.service").read_text()))
        command = shlex.split(unit["Service"]["ExecStart"])
        command[0] = str(Path(os.environ["ARGUS_WORKLOAD_TOOLS_DIR"]) / Path(command[0]).name)
        identity_index = command.index("--agent-id")
        self.assertEqual(command[identity_index + 1], AGENT_ID)
        missing_identity = command[:identity_index] + command[identity_index + 2:]
        rejected = subprocess.run(missing_identity, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("--agent-id is required", rejected.stderr)

        with tempfile.TemporaryDirectory(prefix="argus-provider-contract-") as directory:
            root = Path(directory)
            provider_socket = root / "provider.sock"
            command[command.index("--socket-path") + 1] = str(provider_socket)
            command[command.index("--workload-registration-path") + 1] = str(root / "target.json")
            with (root / "provider.log").open("w+") as log:
                provider = subprocess.Popen(command, stdout=log, stderr=log)
                try:
                    deadline = time.monotonic() + 5
                    while not provider_socket.exists():
                        if provider.poll() is not None or time.monotonic() >= deadline:
                            log.seek(0)
                            self.fail("systemd Provider command failed: " + log.read())
                        time.sleep(0.02)
                    # Invalid schemas prove both handlers are wired without
                    # attempting hardware Quote generation or reading a target.
                    for route, expected in (("/ra/v1/node-evidence", 422),
                                            ("/ra/v1/workload-evidence", 422),
                                            ("/node-evidence", 404)):
                        with self.subTest(route=route):
                            connection = http.client.HTTPConnection("localhost", timeout=5)
                            try:
                                connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                                connection.sock.settimeout(5)
                                connection.sock.connect(str(provider_socket))
                                connection.request("POST", route, body="{}", headers={"Content-Type": "application/json"})
                                response = connection.getresponse()
                                self.assertEqual(response.status, expected, response.read().decode())
                            finally:
                                connection.close()
                finally:
                    provider.terminate()
                    try:
                        provider.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        provider.kill()
                        provider.wait(timeout=5)


@unittest.skipUnless(os.environ.get("SPIRE_BIN_DIR") and os.environ.get("ARGUS_WORKLOAD_TOOLS_DIR"),
                     "requires built tools and official SPIRE v1.15.3")
class OfficialSPIRETests(unittest.TestCase):
    def test_server_configures_node_plugin_and_rejects_identity_mismatch(self):
        spire = Path(os.environ["SPIRE_BIN_DIR"])
        plugin = Path(os.environ["ARGUS_WORKLOAD_TOOLS_DIR"]) / "argus-tdx-nodeattestor-server"
        checksum = hashlib.sha256(plugin.read_bytes()).hexdigest()
        self.assertEqual(runtime.binary_version(spire / "spire-server"), "1.15.3")
        with tempfile.TemporaryDirectory(prefix="argus-node-config-") as directory:
            root = Path(directory)
            runtime.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
                         "-nodes", "-days", "1", "-subj", "/CN=Local Trustee test CA",
                         "-keyout", root / "ca-key.pem", "-out", root / "ca.pem"])
            runtime.run(["openssl", "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256",
                         "-out", root / "ear-key.pem"])
            runtime.run(["openssl", "pkey", "-in", root / "ear-key.pem", "-pubout", "-out", root / "ear-public.pem"])
            for case, agent_id, expected_error in (
                ("matching", AGENT_ID, None),
                ("mismatched", "spiffe://other.example/spire/agent/argus_tdx/openviking-node",
                 "invalid configuration: agent_id trust domain must match core trust_domain"),
            ):
                with self.subTest(case=case):
                    case_root = root / case
                    case_root.mkdir()
                    server_socket = case_root / "server.sock"
                    settings = {
                        "agent_id": agent_id,
                        "slot_owner_key_sha256": hashlib.sha256(bytes(range(32))).hexdigest(),
                        # No join is attempted. Configure must not require an
                        # external appraisal request; this origin is local only.
                        "trustee_url": "https://127.0.0.1:1",
                        "trustee_ca_path": str(root / "ca.pem"),
                        "trustee_server_name": "trustee.test",
                        "ear_public_key_path": str(root / "ear-public.pem"),
                        "ear_expected_issuer": "https://trustee.test",
                        "ear_expected_profile": "tag:github.com,2024:confidential-containers/Trustee",
                        "policy_id": "argus-local-config-test",
                    }
                    plugin_data = "\n".join(f"  {key} = {json.dumps(value)}" for key, value in settings.items())
                    config = case_root / "server.conf"
                    config.write_text('''server {
 bind_address="127.0.0.1" bind_port=%d trust_domain="argus.local"
 socket_path="%s" data_dir="%s"
 ca_subject { country=["CN"] organization=["Local contract test"] common_name="SPIRE test" }
}
plugins {
 DataStore "sql" { plugin_data { database_type="sqlite3" connection_string="%s" } }
 KeyManager "memory" {}
 NodeAttestor "argus_tdx" {
  plugin_cmd=%s plugin_checksum=%s
  plugin_data {
%s
  }
 }
}
''' % (unused_loopback_port(), server_socket, case_root / "data", case_root / "db.sqlite",
                       json.dumps(str(plugin)), json.dumps(checksum), plugin_data))
                    with (case_root / "server.log").open("w+") as log:
                        server = subprocess.Popen([str(spire / "spire-server"), "run", "-config", str(config)],
                                                  stdout=log, stderr=log)
                        try:
                            deadline = time.monotonic() + 20
                            while server.poll() is None and not server_socket.exists() and time.monotonic() < deadline:
                                time.sleep(0.05)
                            log.seek(0)
                            contents = log.read()
                            if expected_error:
                                self.assertIsNotNone(server.poll(), "invalid NodeAttestor config did not stop SPIRE: " + contents)
                                self.assertNotEqual(server.returncode, 0)
                                # This prefix comes from Configure's error path,
                                # not SPIRE's syntax-only validate command.
                                self.assertIn(expected_error, contents)
                            else:
                                self.assertIsNone(server.poll(), contents)
                                self.assertTrue(server_socket.exists(), "configured SPIRE did not become ready: " + contents)
                                runtime.run([spire / "spire-server", "healthcheck", "-socketPath", server_socket])
                                self.assertIn("plugin_name=argus_tdx", contents)
                        finally:
                            server.terminate()
                            try:
                                server.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                server.kill()
                                server.wait(timeout=5)

    def test_generated_config_and_actual_entry_json(self):
        spire = Path(os.environ["SPIRE_BIN_DIR"])
        tools = Path(os.environ["ARGUS_WORKLOAD_TOOLS_DIR"])
        with tempfile.TemporaryDirectory(prefix="argus-spire-contract-") as directory:
            root = Path(directory)
            conf = root / "server.conf"
            socket = root / "server.sock"
            server_port = unused_loopback_port()
            conf.write_text('''server {
 bind_address="127.0.0.1" bind_port=%d trust_domain="example.org"
 socket_path="%s" data_dir="%s"
 ca_subject { country=["CN"] organization=["Local contract test"] common_name="SPIRE test" }
}
plugins {
 DataStore "sql" { plugin_data { database_type="sqlite3" connection_string="%s" } }
 KeyManager "memory" {}
 NodeAttestor "join_token" {}
}
''' % (server_port, socket, root / "data", root / "db.sqlite"))
            for name in ("spire-server", "spire-agent"):
                self.assertEqual(runtime.binary_version(spire / name), "1.15.3")
            runtime.run([spire / "spire-server", "validate", "-config", conf])
            with (root / "server.log").open("w+") as log:
                server = subprocess.Popen([str(spire / "spire-server"), "run", "-config", str(conf)], stdout=log, stderr=log)
                try:
                    deadline = time.monotonic() + 20
                    while not socket.exists():
                        if server.poll() is not None or time.monotonic() >= deadline:
                            log.seek(0)
                            self.fail("local SPIRE Server failed: " + log.read())
                        time.sleep(0.05)
                    c = alternative()
                    # build.sh nests SPIRE under the build output. Stage an
                    # independent installation so deployment roots stay disjoint
                    # and rendering cannot write hooks into the build artifacts.
                    install = root / "install"
                    (install / "bin").mkdir(parents=True)
                    for name in ("argus-agent-config", "argus-tdx-nodeattestor-agent", "argus-tdx-workloadattestor"):
                        shutil.copy2(tools / name, install / "bin" / name)
                    c["paths"].update(install_dir=str(install), config_dir=str(root / "rendered"),
                                      records_dir=str(root / "records"), spire_bin_dir=str(spire))
                    d = runtime.Deployment(c)
                    c["server_socket"] = str(socket)
                    node = root / "node.conf"
                    node.write_text('''agent {
 trust_domain="example.org" server_address="127.0.0.1" server_port=%d
 data_dir="%s" insecure_bootstrap=true
}
plugins {
 KeyManager "memory" {}
 NodeAttestor "argus_tdx" { plugin_cmd="%s" plugin_data {
  proof_key_path="/existing/proof.key" evidence_socket_path="/existing/provider.sock"
 } }
}
''' % (server_port, root / "agent-data", d.bin / "argus-tdx-nodeattestor-agent"))
                    c["node_agent_config"] = str(node)
                    # This fixture belongs to the test runner, which may be an
                    # unprivileged CI user. Root-file checks are tested separately.
                    real_run = runtime.run
                    def command(argv, **kwargs):
                        if argv[0] in ("systemctl", "install"):
                            return ""
                        return real_run(argv, **kwargs)
                    with patch.object(runtime, "run", side_effect=command), patch.object(runtime, "protected_file", lambda p: Path(p)):
                        runtime.render(c)
                        runtime.run([spire / "spire-agent", "validate", "-config", d.etc / "agent.conf"])
                        required = runtime.selectors(c, d.identity["target_id"])
                        cmd = [spire / "spire-server", "entry", "create", "-socketPath", socket,
                               "-parentID", d.identity["agent_id"], "-spiffeID", d.identity["target_id"],
                               "-x509SVIDTTL", "300", "-disableX509SVIDPrefetch"]
                        for selector in sorted(required):
                            cmd += ["-selector", selector]
                        runtime.run(cmd)
                        entries = runtime.server_entries(c, d.identity["target_id"])
                        self.assertEqual(len(entries), 1)
                        runtime.audit_entries(c, entries, required, d.identity["target_id"])
                finally:
                    server.terminate()
                    try:
                        server.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait(timeout=5)
