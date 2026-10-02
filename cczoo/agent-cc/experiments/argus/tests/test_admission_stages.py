import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from admission_stages import summarize

TARGET = {'launch_id': 'launch-a', 'container_id': 'a' * 64, 'pid': '7', 'start_time': '8', 'boot_id': 'b' * 32, 'policy_id': 'p'}
NONCE = 'A' * 43
INVOCATION = 'c' * 32


def row(message, at=20, unit='argus-workload-agent.service'):
    return json.dumps({'MESSAGE': message, '__REALTIME_TIMESTAMP': str(at * 1000),
                       '_BOOT_ID': TARGET['boot_id'], '_SYSTEMD_UNIT': unit, '_SYSTEMD_INVOCATION_ID': INVOCATION})


def subscription(at=10):
    return row('workload subscription launch_id=launch-a container_id=' + 'a' * 64 +
               ' pid=7 start_time=8 policy=p subscription_id=' + INVOCATION, at, 'argus-helper.service')


def stage(name, status='ALLOW', code='OBSERVED', at=20, **changes):
    component = 'provider' if name == 'provider' else 'plugin'
    receipt = {'schema': 'argus.admission-stage.v1', 'component': component, 'attempt_id': NONCE,
               'nonce': NONCE, 'stage': name, 'status': status, 'reason_code': code, 'target': TARGET, **changes}
    return row('argus admission stage ' + json.dumps(receipt), at, 'argus-tdx-provider.service' if component == 'provider' else 'argus-workload-agent.service')


def summary(*rows, **kwargs):
    return summarize('\n'.join(rows), TARGET, 1, 100, INVOCATION, **kwargs)


def test_first_rejection_is_not_a_generic_exception():
    for rows, expected in [([stage('common_target', 'DENY')], 'common_target'),
                           ([stage('common_target'), stage('provider', 'DENY', 'MUTATION_PENDING', 21)], 'provider'),
                           ([stage('common_target'), stage('provider', at=21), stage('evidence_binding', at=22),
                             stage('remote_appraisal', 'DENY', 'EAR_NON_AFFIRMING', 23)], 'remote_appraisal')]:
        result = summary(subscription(), *rows)
        assert result['first_rejection_stage'] == expected
        assert result['new_admission'] == 'DENIED'
        assert result['attempts'][0]['stages'][-1]['status'] == 'NOT_REACHED'
    result = summary(subscription(), stage('common_target'), stage('provider', 'UNKNOWN', 'HISTORY_UNAVAILABLE', 21))
    assert result['first_rejection_stage'] is None and result['new_admission'] == 'UNKNOWN'


def test_missing_ear_after_pending_is_expected_not_an_incomplete_accepted_archive():
    result = summary(subscription(), stage('common_target'), stage('provider', 'DENY', 'MUTATION_PENDING', 21))
    assert result['attempts'][0]['stages'][3]['status'] == 'NOT_REACHED'


def test_missing_earlier_stage_cannot_be_called_first_policy_rejection():
    result = summary(subscription(), stage('remote_appraisal', 'DENY', 'EAR_NON_AFFIRMING'))
    assert result['first_rejection_stage'] is None
    assert result['attempts'][0]['explicit_rejection_stage'] == 'remote_appraisal'


def test_old_window_other_target_and_no_fresh_subscription_are_excluded():
    for rows in [(stage('common_target', 'DENY'),),
                 (subscription(), stage('common_target', 'DENY', at=0)),
                 (subscription(), stage('common_target', 'DENY', target={**TARGET, 'launch_id': 'old'}))]:
        assert summary(*rows)['new_admission'] == 'UNKNOWN'


def test_ready_without_fresh_publication_is_not_new_admission():
    rows = [stage(name, at=20 + index) for index, name in enumerate(('common_target', 'provider', 'evidence_binding', 'remote_appraisal', 'final_target'))]
    assert summary(subscription(), *rows, ready=True)['new_admission'] == 'UNKNOWN'
    publication = row('target SVID published serial=123 launch_id=launch-a container_id=' + 'a' * 64 +
                      ' pid=7 start_time=8 policy=p subscription_id=' + INVOCATION, 30, 'argus-helper.service')
    assert summary(subscription(), *rows, publication, ready=True)['new_admission'] == 'ADMITTED'
    assert summary(subscription(), publication, ready=True, group='native_spire_guarded')['new_admission'] == 'ADMITTED'


def test_conflicting_statuses_keep_unknown():
    result = summary(subscription(), stage('common_target'), stage('common_target', 'DENY', at=21))
    assert result['first_rejection_stage'] is None and result['new_admission'] == 'UNKNOWN'
