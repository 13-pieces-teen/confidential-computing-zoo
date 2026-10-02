"""E3 source association and recovery measurements; no hardware result claims."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import atomic, sha
from lifecycle_evidence import helper_events, reestablished
import lifecycle_evidence
from lifecycle_trial import recovery_access, collect
from test_lifecycle_evidence import snapshots


def fixture(replacement=False):
    a, b = snapshots()
    for snap in (a, b):
        snap['target'].update(boot_id='boot', launch_id='launch', policy_id='policy')
    b['status']['helper_invocation_id'] = 'newhelper'
    if replacement:
        b['target'].update(container_id='d'*64, launch_id='newlaunch', start_time='456')
        b['launch'].update(launch_id='newlaunch', container_id='d'*64)
    target = b['target']
    fields = ' '.join('%s=%s' % (key, target[key]) for key in ('launch_id', 'container_id', 'pid', 'start_time')) + ' policy=policy subscription_id=newhelper'
    rows = [dict(__CURSOR='c1', __REALTIME_TIMESTAMP='1300000', _BOOT_ID='boot', _SYSTEMD_UNIT='helper.service',
                 _SYSTEMD_INVOCATION_ID='newhelper', MESSAGE='workload subscription '+fields),
            dict(__CURSOR='c2', __REALTIME_TIMESTAMP='1500000', _BOOT_ID='boot', _SYSTEMD_UNIT='helper.service',
                 _SYSTEMD_INVOCATION_ID='newhelper', MESSAGE='target SVID published serial=11 '+fields)]
    archive = dict(schema='argus.helper-journal.v1', config_sha256='config', source='trusted_local_journal_cursor_query',
                   helper_unit='helper.service', coverage_started_at_ms=900, coverage_ended_at_ms=2200,
                   anchor={'boot_id':'boot'}, records=rows)
    admission = dict(schema='argus.e1-observation.v1', run_id='run', actual_admission='ADMITTED', target_check={'result':'MATCH'},
                     registered_target=target, deployment_sha256='config', target_id=b['target_id'],
                     status_after=b['status'], production_verification={'target':target}, started_at_ms=1600, completed_at_ms=1900,
                     accepted_workload_nonce='A'*43)
    return a, b, archive, admission


@pytest.mark.parametrize('replacement', [False, True])
def test_new_subscription_and_new_launch_have_explicit_evidence(replacement):
    a, b, archive, admission = fixture(replacement)
    events = helper_events(archive, a, b)
    assert events['subscription_starts'] == events['svid_publications'] == events['distinct_svid_serials'] == 1
    kind = 'replacement-launch' if replacement else 'workload-resubscribe'
    result = reestablished(a, b, kind, events, admission)
    assert result['result'] == 'PASS' and result['hardware_acceptance'] == 'NOT_RUN'
    assert reestablished(a, b, kind, events, None)['result'] == 'UNKNOWN'


@pytest.mark.parametrize('change', ['gap','duplicate','invocation','unit','boot','target'])
def test_journal_incomplete_or_misattributed_is_unknown(change):
    a, b, archive, _ = fixture()
    if change == 'gap': archive['coverage_started_at_ms'] = 1500
    if change == 'duplicate': archive['records'].append(archive['records'][0])
    if change == 'invocation': archive['records'][0]['_SYSTEMD_INVOCATION_ID'] = 'other'
    if change == 'unit': archive['records'][0]['_SYSTEMD_UNIT'] = 'other'
    if change == 'boot': archive['anchor']['boot_id'] = 'other'
    if change == 'target': archive['records'][0]['MESSAGE'] = archive['records'][0]['MESSAGE'].replace('pid=42','pid=99')
    assert helper_events(archive, a, b)['result'] == 'UNKNOWN'


def test_same_container_restart_is_not_new_controlled_launch():
    a, b, archive, admission = fixture()
    assert reestablished(a, b, 'replacement-launch', helper_events(archive,a,b), admission)['result'] == 'UNKNOWN'


def test_recovery_requires_actual_memory_response_on_new_svid(tmp_path):
    a,b,_,_ = fixture()
    ready = dict(schema='argus.command-readiness.v1', result='OBSERVED', run_id='run', target_id=b['target_id'],
                 config_sha256='config', helper_invocation_id='newhelper', target_serial='11', ready_observed_at_ms=1500)
    ready_path=tmp_path/'ready.json'; atomic(ready_path,ready)
    rows=[dict(run_id='run', phase='measurement', request_id='request', outcome='success', memory_result='nonempty',
               peer_identity_verified=True, peer_spiffe_id=b['target_id'], peer_svid_serial='11', started_at_ms=1700, completed_at_ms=1800)]
    trace=tmp_path/'requests.jsonl'; trace.write_text(json.dumps(rows[0])+'\n')
    path=tmp_path/'load-result.json'; receipt=dict(run_id='run', measurement_complete=True,workload_kind='memory_query', requests_sha256=sha(trace));atomic(path,receipt)
    observation=dict(run_id='run',target_id=b['target_id'])
    result=recovery_access(ready_path,path,observation,(a,b),20)
    assert result['result']=='OBSERVED' and result['readiness_to_response_ms']==dict(lower_ms=280,upper_ms=320)
    rows[0]['peer_svid_serial']='10';trace.write_text(json.dumps(rows[0])+'\n');receipt['requests_sha256']=sha(trace);atomic(path,receipt)
    assert recovery_access(ready_path,path,observation,(a,b),0)['result']=='UNKNOWN'


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('replacement', [False, True])
def test_collector_new_cases_join_events_admission_and_actual_quote_counts(tmp_path, native, replacement):
    from test_quote_counters import pair
    a,b,archive,admission=fixture(replacement)
    if native:
        a['runtime_variant']=b['runtime_variant']='native_spire_guarded'
        admission['accepted_workload_nonce']=None
    before_counter,after_counter=pair()
    if not native:
        after_counter['workload'].update(attempted=3,generated=2)
    observation=dict(schema='argus.lifecycle-trial.v1',run_id='run',case='replacement-launch' if replacement else 'workload-resubscribe',
                     target_id=b['target_id'],complete=True,started_at_ms=1000,completed_at_ms=2100,before={},after={})
    for phase,snap,counter in [('before',a,before_counter),('after',b,after_counter)]:
        for kind,value in [('workload',snap),('provider',counter)]:
            path=tmp_path/(kind+'-'+phase+'.json');atomic(path,value)
            observation[phase][kind]=dict(result='OBSERVED',source=path.name,sha256=sha(path))
    path=tmp_path/'helper-journal-window.json';atomic(path,archive)
    observation['helper_journal']=dict(result='OBSERVED',source=path.name,sha256=sha(path))
    atomic(tmp_path/'observation.json',observation)
    admission_path=tmp_path/'admission.json';atomic(admission_path,admission)
    result=collect(tmp_path,admission_observation=admission_path)
    assert result['result']=='PASS',result
    assert result['new_workload_evidence']==('NOT_APPLICABLE_NATIVE' if native else 'OBSERVED')
    assert result['helper_events']['svid_publications']==1
    observation['before'].pop('provider');observation['after'].pop('provider');atomic(tmp_path/'observation.json',observation)
    result=collect(tmp_path,admission_observation=admission_path)
    assert result['workload']['result']=='PASS' and result['result']=='UNKNOWN'


def test_capture_journal_checks_retained_cursor_and_explicit_unit(tmp_path, monkeypatch):
    from types import SimpleNamespace
    config={'paths':'fixed'}
    from common import digest
    anchor=dict(schema='argus.helper-journal-anchor.v1',config_sha256=digest(config),helper_unit='isolated-helper.service',
                cursor='trusted-cursor',boot_id='boot',completed_at_ms=900)
    anchor_path=tmp_path/'anchor.json';atomic(anchor_path,anchor)
    calls=[]
    def run(argv):
        calls.append(argv)
        return json.dumps({'__CURSOR':'trusted-cursor'}) if '--cursor=trusted-cursor' in argv else ''
    fake=SimpleNamespace(Deployment=lambda c: SimpleNamespace(unit=lambda _: 'isolated-helper.service'),run=run)
    monkeypatch.setattr(lifecycle_evidence,'runtime',lambda _: (fake,config))
    old_path=lifecycle_evidence.Path
    monkeypatch.setattr(lifecycle_evidence,'Path',lambda value: SimpleNamespace(read_text=lambda:'boot') if value=='/proc/sys/kernel/random/boot_id' else old_path(value))
    observed=lifecycle_evidence.journal_window('config',anchor_path)
    assert observed['source']=='trusted_local_journal_cursor_query'
    assert '--after-cursor=trusted-cursor' in calls[1] and calls[1][calls[1].index('-u')+1]=='isolated-helper.service'
    fake.run=lambda argv: json.dumps({'__CURSOR':'vacuumed-other-cursor'})
    with pytest.raises(ValueError,match='no longer retained'):lifecycle_evidence.journal_window('config',anchor_path)
