"""Exercise packaging guards with fake build artifacts and an isolated install root.

No compiler, SPIRE, systemd, account, or attestation operation is performed.
Build checks require Linux/bash; installer checks also require root because the
real install.sh retains its production root check. Other platforms skip them.
"""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
LINUX_TOOLS = sys.platform == "linux" and all(shutil.which(name) for name in ("bash", "install", "sha256sum"))
ROOT_LINUX = LINUX_TOOLS and os.geteuid() == 0


def executable(path, contents="#!/bin/sh\nexit 0\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)
    path.chmod(0o755)


def artifacts():
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; printf "%s\\n" "${ARGUS_WORKLOAD_ARTIFACTS[@]}"',
         "artifact-list", str(SCRIPTS / "build-artifacts.sh")],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.splitlines()


def manifest(output, names):
    (output / "SHA256SUMS").write_text("".join(
        hashlib.sha256((output / name).read_bytes()).hexdigest() + "  " + name + "\n"
        for name in names
    ))


@unittest.skipUnless(LINUX_TOOLS, "requires Linux bash and coreutils")
class BuildPackagingTests(unittest.TestCase):
    def test_failed_rebuild_invalidates_success_manifest(self):
        with tempfile.TemporaryDirectory(prefix="argus-failed-build-") as directory:
            root = Path(directory)
            output = root / "build"
            provider = "bin/argus-spire-evidence-provider"
            executable(output / provider)
            manifest(output, [provider])
            (output / "SHA256SUMS.tmp").write_text("interrupted older build")
            executable(root / "commands/go", "#!/bin/sh\nexit 27\n")
            result = subprocess.run(
                ["bash", str(SCRIPTS / "build.sh")], capture_output=True, text=True,
                env={**os.environ, "ARGUS_WORKLOAD_BUILD_DIR": str(output),
                     "PATH": str(root / "commands") + os.pathsep + os.environ["PATH"]},
                timeout=10,
            )
            self.assertEqual(result.returncode, 27, result.stderr)
            self.assertFalse((output / "SHA256SUMS").exists())
            self.assertFalse((output / "SHA256SUMS.tmp").exists())


@unittest.skipUnless(ROOT_LINUX, "requires Linux root; host operations are redirected to a temporary directory")
class InstallPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="argus-install-contract-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "build"
        self.host = self.root / "host"
        (self.host / "etc/systemd/system").mkdir(parents=True)
        self.command_dir = self.root / "commands"
        # All account/service operations are stubs. Only coreutils install runs,
        # with every production destination redirected beneath self.host.
        for name in ("getent", "id", "docker", "nsenter", "systemctl", "openssl", "timeout"):
            executable(self.command_dir / name)
        executable(self.command_dir / "nginx", "#!/bin/sh\necho --with-http_auth_request_module >&2\n")
        executable(self.command_dir / "install", '''#!/usr/bin/env bash
set -euo pipefail
values=()
for value in "$@"; do
    case "$value" in
        /opt/*|/etc/*|/run/*|/var/*) values+=("$INSTALL_TEST_HOST$value") ;;
        *) values+=("$value") ;;
    esac
done
printf '%s\\n' "$*" >> "$INSTALL_TEST_LOG"
exec "$INSTALL_REAL_INSTALL" "${values[@]}"
''')
        self.env = {**os.environ, "ARGUS_WORKLOAD_BUILD_DIR": str(self.output),
                    "PATH": str(self.command_dir) + os.pathsep + os.environ["PATH"],
                    "INSTALL_TEST_HOST": str(self.host),
                    "INSTALL_TEST_LOG": str(self.root / "install.log"),
                    "INSTALL_REAL_INSTALL": shutil.which("install")}

    def package(self):
        names = artifacts()
        for name in names:
            executable(self.output / name)
        manifest(self.output, names)
        return names

    def install(self):
        return subprocess.run(["bash", str(SCRIPTS / "install.sh")], env=self.env,
                              capture_output=True, text=True, timeout=10)

    def assert_rejected_before_install(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse((self.root / "install.log").exists(), result.stderr)
        self.assertFalse((self.host / "opt").exists(), result.stderr)

    def test_missing_provider_cannot_install_the_systemd_unit(self):
        names = self.package()
        current = "bin/argus-spire-evidence-provider"
        (self.output / current).unlink()
        manifest(self.output, [name for name in names if name != current])
        result = self.install()
        self.assert_rejected_before_install(result)
        self.assertIn("missing executable build artifact: " + current, result.stderr)

    def test_unlisted_provider_and_modified_spire_are_rejected(self):
        names = self.package()
        manifest(self.output, [name for name in names if name != "bin/argus-spire-evidence-provider"])
        self.assert_rejected_before_install(self.install())
        manifest(self.output, names)
        executable(self.output / "spire-1.15.3/bin/spire-server", "#!/bin/sh\nexit 42\n")
        self.assert_rejected_before_install(self.install())

    def test_complete_package_installs_only_the_hashed_current_payload(self):
        names = self.package()
        for extra in ("unlisted-tool", "unhashed-extra"):
            executable(self.output / "bin" / extra)
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("INSTALL=PASS", result.stdout)
        for name in names:
            destination = (self.host / "opt/argus-workload" / name if name.startswith("bin/")
                           else self.host / "opt" / name)
            self.assertEqual(destination.read_bytes(), (self.output / name).read_bytes())
        for extra in ("unlisted-tool", "unhashed-extra"):
            self.assertFalse((self.host / "opt/argus-workload/bin" / extra).exists())
        unit = (self.host / "etc/systemd/system/argus-tdx-provider.service").read_text()
        self.assertIn("/opt/argus-workload/bin/argus-spire-evidence-provider --agent-id ", unit)


if __name__ == "__main__":
    unittest.main()
