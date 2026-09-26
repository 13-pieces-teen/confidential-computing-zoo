"""Functional selection, explicit measurement attempts and inexpensive logs."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import runner
import common
from analysis import analyze
from common import atomic, read, sha
from test_runner import config


def test_functional_cases_do_not_require_performance_load():
    m = config(); del m['load']
    runner.validate(m)
    m['cases'][0]['experiment'] = 'E5'
    with pytest.raises(ValueError, match='E5'):
        runner.validate(m)


def test_selected_preflight_ignores_unused_groups_and_load(tmp_path):
    m = config(); m['artifacts'] = []
    for group, arm in m['arms'].items():
        folder = tmp_path / group; folder.mkdir()
        business, load = folder / 'business.json', folder / 'load.json'
        atomic(business, {}); atomic(load, {})
        arm.update(variant_output=str(folder), business_config=str(business), load_config=str(load),
                   artifacts=[str(business), str(load)])
        m['artifacts'] += arm['artifacts']
    m['cases'][0]['operations'][0]['argv'].append('{business_config}')
    cfg, output = tmp_path / 'config.json', tmp_path / 'output'
    atomic(cfg, m); runner.prepare(cfg, output)
    (tmp_path / 'no_close' / 'business.json').unlink()
    (tmp_path / 'full_argus' / 'load.json').unlink()
    inspected = []
    def inspect(path):
        inspected.append(Path(path).name)
        return {'variant':Path(path).name,'artifact_integrity':'PASS','effective_config':'PASS','payload_ready':True}
    with patch('variants.inspect_variant', side_effect=inspect):
        result = runner.preflight(output, 'client', case='memory', group='full_argus')
        assert result['result'] == 'PASS' and inspected == ['full_argus']
        assert runner.preflight(output, 'client', group='no_close')['result'] == 'FAIL'
    with pytest.raises(ValueError, match='no runs'):
        runner.preflight(output, 'client', only_run='typo')


def measurement_config(tmp_path):
    m = config()
    m['groups'] = ['full_argus']; m['arms'] = {'full_argus': m['arms']['full_argus']}; m['seeds'] = [1]
    load = tmp_path / 'load.json'; atomic(load, {'frozen':True,'instances':[{}]})
    m['arms']['full_argus']['load_config'] = str(load)
    op = m['cases'][0]['operations'][0]
    op.update(kind='measure', safe_retry='get-measurement',
              argv=[sys.executable, str(Path(runner.__file__).with_name('step.py')), 'load', 'run',
                    '--config','{load_config}','--output','{run_dir}'], result='step-result.json')
    cfg, output = tmp_path / 'config.json', tmp_path / 'out'
    atomic(cfg, m); runner.prepare(cfg, output)
    return m, output, runner.plan(m)[0]


def fake_measurement(argv, **kwargs):
    env = kwargs['env']; attempt = int(env['ARGUS_ATTEMPT'])
    directory = Path(argv[argv.index('--output') + 1]); native = directory / 'load'; native.mkdir()
    raw = native / 'requests.jsonl'
    raw.write_text(json.dumps({'run_id':env['ARGUS_RUN_ID'],'request_id':'a'+str(attempt),
                              'phase':'measurement','outcome':'success','latency_ms':10 if attempt > 1 else 999})+'\n')
    result = {'run_id':env['ARGUS_RUN_ID'],'result':'PASS' if attempt > 1 else 'UNKNOWN',
              'measurement_complete':attempt > 1,'measurement_seconds':1,'requests_sha256':sha(raw)}
    atomic(native / 'load-result.json', result)
    atomic(directory / 'step-result.json', dict(result, schema='argus.step.v1', operation_id=env['ARGUS_OPERATION_ID'],
           source='load/load-result.json',source_sha256=sha(native / 'load-result.json')))
    return subprocess.CompletedProcess(argv, 0)


def test_get_replacement_attempt_preserves_unknown_and_paired_block(tmp_path):
    m, output, run = measurement_config(tmp_path)
    with patch.object(runner, 'preflight', return_value={'result':'PASS'}), patch.object(runner, 'run_logged', side_effect=fake_measurement) as command:
        assert runner.execute(output,'client',only_run=run['run_id'])['result'] == 'UNKNOWN'
        original = output / 'runs' / run['run_id'] / 'load' / 'requests.jsonl'
        before = original.read_bytes()
        with pytest.raises(ValueError, match='new-attempt'):
            runner.execute(output,'client',resume=True,only_run=run['run_id'])
        assert command.call_count == 1
        assert runner.execute(output,'client',resume=True,only_run=run['run_id'],new_attempt=True)['result'] == 'COMPLETE'
        assert original.read_bytes() == before
    verdict = runner.collect(output)['runs'][0]
    assert verdict['attempt'] == 2 and verdict['interrupted_attempts'] == 1
    assert verdict['block_id'] == run['block_id'] and verdict['run_id'] == run['run_id']
    state = read(output/'state.json')['operations'][run['run_id']+'/business']
    assert state['previous_attempts'][0]['verdict'] == 'UNKNOWN'
    analyze(output)
    summary = read(output/'analysis.json')
    assert summary['summaries'][0]['p95_ms']['mean'] == 10
    assert summary['summaries'][0]['p95_ms']['n_runs'] == 1
    assert summary['interrupted_measurement_attempts'] == 1


@pytest.mark.parametrize('kind,body', [('mutation',False),('fault',False),('measure',True)])
def test_new_attempt_never_replays_mutation_fault_or_post(tmp_path, kind, body):
    m, output, run = measurement_config(tmp_path)
    with patch.object(runner, 'preflight', return_value={'result':'PASS'}), patch.object(runner,'run_logged',side_effect=fake_measurement):
        runner.execute(output,'client',only_run=run['run_id'])
    # Probe the retry predicate against the actual bound sampler configuration.
    op = copy.deepcopy(m['cases'][0]['operations'][0]); op['kind'] = kind
    if body:
        atomic(tmp_path/'load.json', {'instances':[{}],'body_file':'synthetic.json'})
    assert not runner.safe_get_measurement(op, m['arms']['full_argus'], tmp_path)
    if body:
        with patch.object(runner, 'preflight', return_value={'result':'PASS'}), patch.object(runner,'run_logged') as command:
            with pytest.raises(ValueError, match='mutations/faults/POST'):
                runner.execute(output,'client',resume=True,only_run=run['run_id'],new_attempt=True)
            command.assert_not_called()


def test_completed_unknown_measurement_is_not_retryable(tmp_path):
    _, output, run = measurement_config(tmp_path)
    def completed_unknown(argv, **kwargs):
        result = fake_measurement(argv, **kwargs)
        directory = Path(argv[argv.index('--output') + 1])
        path = directory/'step-result.json'
        evidence = read(path); evidence['measurement_complete'] = True; atomic(path,evidence)
        return result
    with patch.object(runner,'preflight',return_value={'result':'PASS'}), patch.object(runner,'run_logged',side_effect=completed_unknown) as command:
        assert runner.execute(output,'client',only_run=run['run_id'])['result'] == 'UNKNOWN'
        with pytest.raises(ValueError, match='interrupted measurement'):
            runner.execute(output,'client',resume=True,only_run=run['run_id'],new_attempt=True)
        assert command.call_count == 1


def test_buffered_measurements_only_fsync_on_normal_completion(tmp_path):
    with patch.object(common.os,'fsync') as sync:
        with common.measurement_log(tmp_path/'requests.jsonl') as stream:
            for i in range(25):
                common.write_measurement(stream, {'request':i})
            assert sync.call_count == 0
        assert sync.call_count == 1
    with patch.object(common.os,'fsync') as sync:
        with pytest.raises(RuntimeError), common.measurement_log(tmp_path/'interrupted.jsonl') as stream:
            common.write_measurement(stream, {'request':1})
            raise RuntimeError('interrupted')
        assert sync.call_count == 0


def test_diagnostics_are_bounded_redacted_and_stdout_is_separate(tmp_path):
    path = tmp_path/'diagnostic.json'
    p = common.run_logged([sys.executable,'-c',
        "import sys; print('PUBLIC'); sys.stderr.write('x'*50000+'\\nValueError: missing config\\nX-API-Key: private-key\\nprivate-exact\\n')"],
        diagnostic=path,stage='fixture',timeout=5,secrets=['private-exact'],stdout=subprocess.PIPE)
    assert p.stdout == b'PUBLIC\r\n' or p.stdout == b'PUBLIC\n'
    diagnostic = read(path)
    assert len(diagnostic['stderr_tail']) <= 16384
    assert 'missing config' in diagnostic['stderr_tail']
    assert 'private-key' not in path.read_text() and 'private-exact' not in path.read_text()
    assert 'PUBLIC' not in path.read_text()
    if os.name == 'posix':
        assert path.stat().st_mode & 0o077 == 0


def test_timeout_has_a_diagnostic_record(tmp_path):
    with pytest.raises(subprocess.TimeoutExpired):
        common.run_logged([sys.executable,'-c',"import time,sys; print('waiting',file=sys.stderr,flush=True); time.sleep(10)"],
                          diagnostic=tmp_path/'diagnostic.json',stage='timeout',timeout=.1)
    assert read(tmp_path/'diagnostic.json')['error_class'] == 'TimeoutExpired'
