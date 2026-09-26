import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import atomic, digest, read, sha
from admission_trial import compare, config, observe, target_check


class AdmissionTrialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.deployment_path = self.root / 'deployment.json'
        self.business_config = self.root / 'ov.json'; self.business_config.write_text('{}')
        self.c = {'approved': {'config_digest': 'sha256:' + sha(self.business_config)}}
        atomic(self.deployment_path, self.c)
        self.trial_path = self.root / 'trial.json'
        atomic(self.trial_path, {'schema': 'argus.e1-trial.v1', 'run_id': 'trial', 'case': 'same_image_new_instance',
                                'group': 'full_argus', 'deployment': str(self.deployment_path)})
        self.target = {'pid': '7', 'container_id': 'a' * 64, 'launch_id': 'launch-a', 'image_config_digest': 'sha256:image', 'start_time': '8'}
        self.target_path = self.root / 'target.json'; atomic(self.target_path, self.target)
        self.d = SimpleNamespace(identity={'target_id': 'spiffe://argus.local/server'}, workload={'id': 'memory', 'config_host_path': str(self.business_config)},
                                 target=self.target_path, bin=self.root, allowed_client_ids=('spiffe://argus.local/client',))
        self.status = {'ready': True, 'target_serial': '123', 'helper_invocation_id': 'invocation'}
        self.verify = {'target': self.target, 'svid_and_business': {'server_spiffe_id': self.d.identity['target_id'],
                       'client_spiffe_id': self.d.allowed_client_ids[0], 'server_serial': '123'},
                       'appraisal': 'workload EAR accepted nonce=' + 'A' * 43}
        self.workload = SimpleNamespace(Deployment=lambda c: self.d, protected_file=Path,
                                       runtime_manifest=lambda c: {'build_integrity': 'MATCH'},
                                       status=lambda c: copy.deepcopy(self.status), verify=lambda c: copy.deepcopy(self.verify))
        self.invoke = lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(self.target), stderr='')

    def sample(self, **changes):
        return {'schema': 'argus.e1-observation.v1', 'run_id': 'trial', 'case': 'same_image_new_instance',
                'group': 'full_argus', 'configuration_sha256': 'cfg', 'deployment_sha256': 'deployment',
                'target_id': 'target', 'workload_id': 'workload', 'started_at_ms': 1, 'completed_at_ms': 2,
                'registered_target': copy.deepcopy(self.target), 'actual_admission': 'ADMITTED', **changes}

    def test_observation_uses_real_verification_and_saves_old_target(self):
        result = observe(self.trial_path, self.root / 'phase', loaded=(self.workload, self.c), invoke=self.invoke)
        self.assertEqual(result['actual_admission'], 'ADMITTED')
        self.assertEqual(result['accepted_workload_nonce'], 'A' * 43)
        self.assertEqual(read(self.root / 'phase/target.json'), self.target)
        self.assertEqual(result['hardware_provenance'], 'NOT_ESTABLISHED_BY_THIS_TOOL')

    def test_current_peer_serial_mismatch_never_becomes_admitted(self):
        self.verify['svid_and_business']['server_serial'] = 'old'
        result = observe(self.trial_path, self.root / 'phase', loaded=(self.workload, self.c), invoke=self.invoke)
        self.assertEqual(result['actual_admission'], 'UNKNOWN')

    def test_verifier_exception_is_not_policy_denial(self):
        def fail(c):
            raise TimeoutError('server unavailable')
        self.workload.verify = fail
        result = observe(self.trial_path, self.root / 'phase', loaded=(self.workload, self.c), invoke=self.invoke)
        self.assertEqual(result['actual_admission'], 'UNKNOWN')
        self.assertEqual(result['verification_error_class'], 'TimeoutError')

    def test_local_semantic_failure_distinct_from_daemon_failure(self):
        runner = lambda *a, **k: SimpleNamespace(returncode=1, stdout='', stderr='workload config changed')
        self.assertEqual(target_check('tool', 'target', self.target, runner)['result'], 'LOCAL_BINDING_REJECTED')
        runner = lambda *a, **k: SimpleNamespace(returncode=1, stdout='', stderr='docker daemon unavailable')
        with patch('admission_trial.Path.exists', return_value=True):
            self.assertEqual(target_check('tool', 'target', self.target, runner)['result'], 'UNKNOWN')

    def test_no_expected_pass_field_is_accepted(self):
        value = read(self.trial_path); value['expected'] = 'PASS'; atomic(self.trial_path, value)
        with self.assertRaisesRegex(ValueError, 'unknown E1'):
            config(self.trial_path)

    def test_same_image_new_instance_requires_old_rejection_and_real_new_admission(self):
        before = self.sample()
        after = self.sample(started_at_ms=3, completed_at_ms=4, reference_content_digest=digest(before),
                            registered_target=self.target | {'container_id': 'b' * 64, 'launch_id': 'launch-b'},
                            old_target_check={'result': 'LOCAL_BINDING_REJECTED'})
        result = compare(before, after)
        self.assertEqual(result['result'], 'OBSERVED')
        self.assertIn('no forged Quote', result['scope'])
        after['actual_admission'] = 'UNKNOWN'
        self.assertEqual(compare(before, after)['result'], 'UNKNOWN')

    def test_replacement_comparison_rejects_unassociated_before(self):
        before = self.sample()
        after = self.sample(started_at_ms=3, completed_at_ms=4, reference_content_digest='different',
                            registered_target=self.target | {'container_id': 'b' * 64, 'launch_id': 'launch-b'},
                            old_target_check={'result': 'LOCAL_BINDING_REJECTED'})
        self.assertFalse(compare(before, after)['case_setup_observed'])

    def test_unrelated_requires_actual_new_activity_and_fresh_full_workload_nonce(self):
        before = self.sample(case='unrelated_activity', accepted_workload_nonce='old')
        after = self.sample(case='unrelated_activity', started_at_ms=3, completed_at_ms=4,
                            accepted_workload_nonce='new', unrelated_launch={'stage': 'complete', 'launch_id': 'other', 'workload_id': 'other'})
        self.assertEqual(compare(before, after)['result'], 'OBSERVED')
        after['accepted_workload_nonce'] = 'old'
        self.assertEqual(compare(before, after)['result'], 'UNKNOWN')
        before['group'] = after['group'] = 'native_spire_guarded'
        self.assertEqual(compare(before, after)['result'], 'OBSERVED')

    def test_config_case_stays_local_not_remote_quote_claim(self):
        before = self.sample(case='config_mismatch', observed_config_digest='old', approved_config_digest='old')
        after = self.sample(case='config_mismatch', started_at_ms=3, completed_at_ms=4, actual_admission='UNKNOWN',
                            observed_config_digest='new', approved_config_digest='old', target_check={'result': 'LOCAL_BINDING_REJECTED'})
        result = compare(before, after)
        self.assertEqual(result['result'], 'OBSERVED')
        self.assertIn('local', result['scope'])
        self.assertEqual(result['raw_quote_and_signed_ear_archive'], 'NOT_PROVIDED_BY_PRODUCTION_CLI')


if __name__ == '__main__':
    unittest.main()
