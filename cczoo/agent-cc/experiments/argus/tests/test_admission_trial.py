import copy
import json
from pathlib import Path
import sys
import tempfile
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import atomic, digest, read, sha
from admission_trial import attempt, compare, config, observe, pending_observation, target_check


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
        self.assertEqual(result['raw_quote_and_signed_ear_archive'], 'SEPARATE_ARCHIVE_REQUIRED')

    def test_explicit_attempt_uses_isolated_units_and_fresh_correlated_journal(self):
        self.target.update(boot_id='b' * 32, policy_id='p')
        atomic(self.target_path, self.target)
        units = {'helper': 'ax-test-full-helper.service', 'provider': 'ax-test-full-provider.service', 'agent': 'ax-test-full-agent.service'}
        self.d.unit = units.__getitem__
        invocation, nonce = 'c' * 32, 'A' * 43
        rows = []
        def journal(message, at, role):
            rows.append(json.dumps({'MESSAGE': message, '__REALTIME_TIMESTAMP': str(at * 1000),
                                    '_BOOT_ID': 'b' * 32, '_SYSTEMD_UNIT': units[role], '_SYSTEMD_INVOCATION_ID': invocation}))
        suffix = ' launch_id=launch-a container_id=' + 'a' * 64 + ' pid=7 start_time=8 policy=p subscription_id=' + invocation
        journal('workload subscription' + suffix, 101, 'helper')
        for index, stage in enumerate(('common_target', 'provider', 'evidence_binding', 'remote_appraisal', 'final_target')):
            component = 'provider' if stage == 'provider' else 'plugin'
            journal('argus admission stage ' + json.dumps({'schema': 'argus.admission-stage.v1', 'component': component,
                    'attempt_id': nonce, 'nonce': nonce, 'stage': stage, 'status': 'ALLOW', 'reason_code': 'OBSERVED',
                    'target': self.target}), 102 + index, 'provider' if stage == 'provider' else 'agent')
        journal('target SVID published serial=123' + suffix, 108, 'helper')
        commands = []
        def invoke(argv, **kwargs):
            commands.append(argv)
            if argv[0] == 'systemctl':
                self.status['helper_invocation_id'] = invocation
                return SimpleNamespace(returncode=0, stdout='', stderr='')
            return SimpleNamespace(returncode=0, stdout='\n'.join(rows), stderr='')
        with patch('admission_trial.time.time_ns', side_effect=[100_000_000, 110_000_000]):
            result = attempt(self.trial_path, self.root / 'attempt', new_subscription=True, loaded=(self.workload, self.c), invoke=invoke)
        self.assertEqual(result['result'], 'ADMITTED')
        self.assertEqual(result['accepted_nonce'], nonce)
        self.assertTrue(result['complete'])
        self.assertEqual(commands[0], ['systemctl', 'restart', units['helper']])
        self.assertTrue(all(unit in commands[1] for unit in units.values()))

    def test_old_ready_state_cannot_be_relabelled_new_admission(self):
        self.d.unit = lambda role: 'ax-test-' + role + '.service'
        invoke = lambda *a, **kw: SimpleNamespace(returncode=0, stdout='', stderr='')
        with patch('admission_trial.time.monotonic', side_effect=[0, 2]):
            result = attempt(self.trial_path, self.root / 'attempt', new_subscription=True, timeout_seconds=1,
                             loaded=(self.workload, self.c), invoke=invoke)
        self.assertEqual(result['result'], 'UNKNOWN')
        self.assertFalse(result['complete'])
        self.assertIsNone(result['ready_elapsed_ms'])

    def test_attempt_requires_explicit_new_subscription(self):
        with self.assertRaisesRegex(ValueError, 'explicit --new-subscription'):
            attempt(self.trial_path, self.root / 'attempt', loaded=(self.workload, self.c))

    def test_pending_case_needs_real_interval_and_does_not_assume_baseline_verdict(self):
        before = self.sample(case='record_pending')
        after = self.sample(case='record_pending', started_at_ms=3, completed_at_ms=4,
                            pending={'pending_observed': True, 'mutation_matches_target': True, 'target_check': {'result': 'MATCH'}},
                            target_check={'result': 'MATCH'}, new_admission_attempt={'new_subscription_requested': True,
                                'complete': True, 'pending_entire_attempt': True, 'result': 'ADMITTED'})
        self.assertEqual(compare(before, after)['result'], 'OBSERVED')
        after['new_admission_attempt']['result'] = 'DENIED'
        self.assertEqual(compare(before, after)['result'], 'OBSERVED')
        after['new_admission_attempt']['pending_entire_attempt'] = False
        self.assertFalse(compare(before, after)['case_setup_observed'])

    def test_pending_capture_reads_exact_mutation_and_omits_credentials(self):
        mutation = 'mutation-' + 'a' * 32
        receipt = self.root / 'reached.json'
        atomic(receipt, {'schema': 'argus.lifecycle-barrier-receipt.v1', 'mutation_id': mutation,
                         'point': 'before_confirm', 'classification': 'HELD'})
        value = read(self.trial_path) | {'case': 'record_pending', 'barrier_receipt': str(receipt)}
        self.c['trucon_socket_path'] = '/root-only/trucon.sock'
        class SocketPath:
            def lstat(self):
                return SimpleNamespace(st_mode=stat.S_IFSOCK | 0o600, st_uid=0)
            def resolve(self):
                return self
            def __str__(self):
                return '/root-only/trucon.sock'
        safe_op = {'container': {'id': self.target['container_id']}, 'response': {'status': 204}}
        outbox = {'mutation_id': mutation, 'chain_id': 'default', 'operation_type': 'start', 'status': 'SUBMITTED',
                  'record_id': 'record-1', 'request_json': json.dumps({'op_record': safe_op, 'identity_token': 'SECRET'}),
                  'result_json': json.dumps({'op_record': safe_op}), 'submission_json': 'SIGNED-SECRET'}
        calls = []
        class Connection:
            def __init__(self, path):
                pass
            def request(self, method, path, headers):
                self.path = path; calls.append((method, path, headers))
            def getresponse(self):
                if self.path == '/mutations':
                    return SimpleNamespace(status=200, read=lambda n: json.dumps({'mutations': [outbox]}).encode(), getheader=lambda h: None)
                return SimpleNamespace(status=409, read=lambda n: b'{}', getheader=lambda h: 'MUTATION_PENDING')
            def close(self):
                pass
        with patch('admission_trial.Path', return_value=SocketPath()), patch('admission_trial.TruConConnection', Connection), \
                patch('admission_trial.target_check', return_value={'result': 'MATCH'}):
            result = pending_observation(value, self.c, self.workload)
        self.assertTrue(result['pending_observed'] and result['mutation_matches_target'])
        self.assertEqual(result['mutation']['response_status'], 204)
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertEqual(calls[0][:2], ('GET', '/mutations'))
        self.assertEqual(calls[1][2]['X-TruCon-Caller-Service'], 'argus_provider')


if __name__ == '__main__':
    unittest.main()
