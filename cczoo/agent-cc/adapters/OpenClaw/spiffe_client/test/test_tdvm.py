"""QEMU argv regression using stub executables; does not boot a VM."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

TDVM = Path(__file__).resolve().parents[4]/'core/spire/tests/tdvm/tdvm.sh'


@unittest.skipUnless(os.name == 'posix', 'Bash process fixture runs on Linux')
class TDVMProfileTests(unittest.TestCase):
    def test_openclaw_needs_no_host_docker_or_service_forwards(self):
        for profile in ('openclaw','openviking'):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary)
                stubs=root/'bin'; stubs.mkdir()
                commands={'ps':'exit 1','ss':'exit 0','guestfish':'cat >/dev/null',
                          'qemu-img':'if [[ "$1" == create ]]; then touch "${@: -1}"; fi',
                          'docker':'touch "$DOCKER_MARKER"; echo 172.17.0.1',
                          'qemu-system-x86_64':'printf "%s\\n" "$@" > "$QEMU_CAPTURE"; exit 47'}
                for name,body in commands.items():
                    file=stubs/name; file.write_text('#!/usr/bin/env bash\n'+body+'\n'); file.chmod(0o755)
                for name in ('base.qcow2','firmware.fd','key.pub'): (root/name).touch()
                env=dict(os.environ, PATH=str(stubs)+':'+os.environ['PATH'],TDVM_PROFILE=profile,
                         TDVM_BASE_IMAGE=str(root/'base.qcow2'),TDVM_OVERLAY_IMAGE=str(root/'overlay.qcow2'),
                         TDVM_FIRMWARE=str(root/'firmware.fd'),TDVM_SSH_PUBLIC_KEY=str(root/'key.pub'),
                         TDVM_RUNTIME_DIR=str(root/'run'),QEMU_CAPTURE=str(root/'argv'),DOCKER_MARKER=str(root/'docker-used'))
                result=subprocess.run(['bash',str(TDVM),'start'],env=env,capture_output=True,text=True)
                self.assertEqual(result.returncode,47,result.stderr)
                args=(root/'argv').read_text()
                self.assertIn('tdx-guest,id=tdx0',args)
                if profile=='openclaw':
                    self.assertFalse((root/'docker-used').exists())
                    self.assertIn('hostfwd=tcp:127.0.0.1:2223-:22',args)
                    self.assertNotIn('1933',args); self.assertNotIn('1943',args)
                else:
                    self.assertTrue((root/'docker-used').exists())
                    self.assertIn('hostfwd=tcp:172.17.0.1:1943-:1943',args)


if __name__=='__main__': unittest.main()
