"""Exercise official SPIRE config/Entry contracts; no node join or TDX claim."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
import test_runtime as contracts

runtime = contracts.runtime


@unittest.skipUnless(os.environ.get("SPIRE_BIN_DIR") and os.environ.get("ARGUS_WORKLOAD_TOOLS_DIR"),
                     "requires built tools and official SPIRE v1.15.3")
class OfficialSPIRETests(unittest.TestCase):
    def test_generated_config_and_actual_entry_json(self):
        spire = Path(os.environ["SPIRE_BIN_DIR"])
        tools = Path(os.environ["ARGUS_WORKLOAD_TOOLS_DIR"])
        with tempfile.TemporaryDirectory(prefix="argus-spire-contract-") as directory:
            root = Path(directory)
            conf = root / "server.conf"
            socket = root / "server.sock"
            conf.write_text('''server {
 bind_address="127.0.0.1" bind_port=0 trust_domain="argus.local"
 socket_path="%s" data_dir="%s"
 ca_subject { country=["CN"] organization=["Local contract test"] common_name="SPIRE test" }
}
plugins {
 DataStore "sql" { plugin_data { database_type="sqlite3" connection_string="%s" } }
 KeyManager "memory" {}
 NodeAttestor "join_token" {}
}
''' % (socket, root / "data", root / "db.sqlite"))
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
                    c = json.loads((runtime.PACKAGE / "config/environment.example.json").read_text())
                    c["approved"] = contracts.RuntimeContractTests().approved()
                    c["server_socket"] = str(socket)
                    node = root / "node.conf"
                    node.write_text('''agent {
 trust_domain="argus.local" server_address="127.0.0.1" server_port=8081
 data_dir="%s" insecure_bootstrap=true
}
plugins {
 KeyManager "memory" {}
 NodeAttestor "argus_tdx" { plugin_cmd="%s" plugin_data {
  proof_key_path="/existing/proof.key" evidence_socket_path="/existing/provider.sock"
 } }
}
''' % (root / "agent-data", tools / "argus-tdx-nodeattestor-agent"))
                    c["node_agent_config"] = str(node)
                    # This fixture belongs to the test runner, which may be an
                    # unprivileged CI user. Root-file checks are tested separately.
                    with patch.object(runtime, "SPIRE", spire), patch.object(runtime, "BIN", tools), patch.object(runtime, "ETC", root / "rendered"), patch.object(runtime, "protected_file", lambda p: Path(p)):
                        runtime.render(c)
                        runtime.run([spire / "spire-agent", "validate", "-config", runtime.ETC / "agent.conf"])
                        required = runtime.selectors(c, runtime.TARGET_ID)
                        cmd = [spire / "spire-server", "entry", "create", "-socketPath", socket,
                               "-parentID", runtime.AGENT_ID, "-spiffeID", runtime.TARGET_ID,
                               "-x509SVIDTTL", "300", "-disableX509SVIDPrefetch"]
                        for selector in sorted(required):
                            cmd += ["-selector", selector]
                        runtime.run(cmd)
                        entries = runtime.server_entries(c, runtime.TARGET_ID)
                        self.assertEqual(len(entries), 1)
                        runtime.audit_entries(entries, required, runtime.TARGET_ID)
                finally:
                    server.terminate()
                    try:
                        server.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait(timeout=5)
