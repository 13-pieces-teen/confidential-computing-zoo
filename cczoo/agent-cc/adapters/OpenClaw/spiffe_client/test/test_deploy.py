import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('deploy', ROOT / 'deploy.py')
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)
spec = importlib.util.spec_from_file_location('lifecycle', ROOT / 'lifecycle.py')
lifecycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lifecycle)


class DeployTests(unittest.TestCase):
    def config(self):
        c = json.loads((ROOT / 'config/deployment.example.json').read_text())
        return c | {'server_address': '10.0.2.2', 'node_certificate_sha1': 'a'*40,
                    'openclaw_image': 'registry.example/oc@sha256:'+'b'*64,
                    'image_config_digest': 'sha256:'+'c'*64, 'helper_sha256': 'd'*64}

    def test_example_requires_real_pins(self):
        with self.assertRaises(ValueError):
            deploy.validate(json.loads((ROOT / 'config/deployment.example.json').read_text()))
        for changes in ({'openclaw_image': 'openclaw:latest'}, {'gateway_uid': 0}, {'reader_gid': 1000},
                        {'openviking_origin': 'https://u:p@host'}, {'gateway_command': 'sh -c something'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                deploy.validate(self.config() | changes)

    def test_render_reproducible_role_boundaries_and_mounts(self):
        with tempfile.TemporaryDirectory() as temporary:
            first, second = Path(temporary)/'a', Path(temporary)/'b'
            deploy.render(self.config(), first)
            deploy.render(self.config(), second)
            for file in first.iterdir():
                self.assertEqual(file.read_bytes(), (second/file.name).read_bytes())
            agent = (first/'agent.conf').read_text()
            self.assertIn('NodeAttestor "x509pop"', agent)
            self.assertNotIn('argus_tdx', agent)
            self.assertNotIn('trustee', agent.lower())
            self.assertIn('/run/spire/openclaw/agent.sock', agent)
            self.assertIn('unix:path:/opt/argus-workload/bin/spiffe-client-credentials', deploy.selectors(self.config(), deploy.HELPER))
            credentials = json.loads((first/'credentials.json').read_text())
            self.assertEqual(credentials['agent_spiffe_id'], deploy.agent_id(self.config()))
            gateway = json.loads((first/'compose.json').read_text())['services']['gateway']
            self.assertEqual(gateway['restart'], 'no')
            self.assertNotIn('ports', gateway)
            self.assertFalse(any('sock' in mount for mount in gateway['volumes']))
            self.assertTrue(any('credentials:ro' in mount for mount in gateway['volumes']))
            for line in (first/'SHA256SUMS').read_text().splitlines():
                digest, name = line.split('  ')
                self.assertEqual(hashlib.sha256((first/name).read_bytes()).hexdigest(), digest)

    def test_entry_audit_rejects_weak_duplicate_or_wrong_parent(self):
        c = self.config()
        good = {'id': 'one', 'parent_id': deploy.agent_id(c), 'spiffe_id': deploy.CLIENT,
                'selectors': [dict(zip(('type', 'value'), s.split(':', 1))) for s in deploy.selectors(c, deploy.CLIENT)],
                'additional_attributes': {'disable_x509_svid_prefetch': True}}
        deploy.audit([good], c, deploy.CLIENT)
        for changes in ({'parent_id': 'spiffe://argus.local/old-agent'}, {'selectors': [{'type':'unix','value':'uid:1000'}]},
                        {'admin': True}, {'additional_attributes': {}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                deploy.audit([good, good | changes], c, deploy.CLIENT)

    def test_sparse_or_interrupted_rotation_is_not_pass(self):
        rows = [{'started_at_ms': i*200, 'completed_at_ms':i*200+10, 'ok':True, 'ready':True,
                 'client_serial': str(i//10), 'server_serial':'1'} for i in range(20)]
        self.assertEqual(lifecycle.verify(rows, 'rotation', {})['result'], 'PASS')
        rows[5]['ok'] = False
        with self.assertRaises(ValueError): lifecycle.verify(rows, 'rotation', {})
        with self.assertRaises(ValueError): lifecycle.verify([rows[0],rows[-1]], 'rotation', {})

    def test_outage_must_stay_closed_and_recover(self):
        rows = [{'started_at_ms': i*200, 'completed_at_ms':i*200+10,
                 'ok':i<10 or i>=60, 'ready':i<10 or i>=60} for i in range(75)]
        events = {'fault_at_ms':2000,'recover_at_ms':12000}
        self.assertEqual(lifecycle.verify(rows, 'publisher', events)['result'], 'PASS')
        rows[40]['ready'] = True
        with self.assertRaises(ValueError): lifecycle.verify(rows, 'publisher', events)


if __name__ == '__main__': unittest.main()
