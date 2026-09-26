import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import runner
import step
from common import atomic, read


def args(tmp_path):
    config = tmp_path / 'config.json'; atomic(config, {})
    return argparse.Namespace(tool='locomo', action='resume', config=str(config), output=str(tmp_path),
                              receipt='step-result.json', instance=None, clients=1, timeout=30)


def test_step_locomo_is_application_completion_not_security(tmp_path):
    atomic(tmp_path / 'locomo/result.json', {'run_id': 'run', 'operation_id': 'op', 'result': 'COMPLETE',
                                          'delivery_compliance': 'NOT_ASSESSED_BY_QA'})
    with patch.dict(os.environ, ARGUS_RUN_ID='run', ARGUS_OPERATION_ID='op'), patch.object(step, 'run_logged', return_value=subprocess.CompletedProcess([], 0)):
        assert step.execute(args(tmp_path)) == 0
    result = read(tmp_path / 'step-result.json')
    assert result['native_result'] == 'COMPLETE'
    assert result['evidence_scope'] == 'application_workload_completion_not_security'
    assert result['source'] == 'locomo/result.json'


def test_step_wrong_operation_or_failed_child_cannot_reuse_complete_native_result(tmp_path):
    for operation, code in [('other', 0), ('op', 1)]:
        atomic(tmp_path / 'locomo/result.json', {'run_id': 'run', 'operation_id': operation, 'result': 'COMPLETE'})
        with patch.dict(os.environ, ARGUS_RUN_ID='run', ARGUS_OPERATION_ID='op'), patch.object(step, 'run_logged', return_value=subprocess.CompletedProcess([], code)):
            assert step.execute(args(tmp_path)) == 1
        assert read(tmp_path / 'step-result.json')['result'] == 'UNKNOWN'


def test_real_runner_step_child_resumes_known_locomo_task_without_repeating_writes(tmp_path):
    # The runner and both subprocess layers are real. Only the external Gateway
    # is a controlled adapter; this is a local protocol test, never remote proof.
    module_root = Path(__file__).resolve().parents[1]
    tests_root = Path(__file__).resolve().parent
    native_bootstrap = tmp_path / 'native_bootstrap.py'
    native_bootstrap.write_text('''import json, pathlib, sys
sys.path.insert(0, ROOT)
sys.path.insert(0, TESTS)
import locomo_run
from test_locomo_run import FakeGateway
class RecordedGateway(FakeGateway):
    def __init__(self): super().__init__(pending=not pathlib.Path(READY).exists())
    def call(self, binding, action, **payload):
        with open(CALLS, 'a') as output: output.write(json.dumps({'action':action, **payload})+'\\n')
        return super().call(binding, action, **payload)
locomo_run.Gateway = RecordedGateway
raise SystemExit(locomo_run.main())
'''.replace('ROOT', repr(str(module_root))).replace('TESTS', repr(str(tests_root)))
        .replace('READY', repr(str(tmp_path / 'ready'))).replace('CALLS', repr(str(tmp_path / 'calls.jsonl'))), encoding='utf-8')
    step_bootstrap = tmp_path / 'step_bootstrap.py'
    step_bootstrap.write_text('''import sys
sys.path.insert(0, ROOT)
import step
original = step.run_logged
def invoke(argv, **options):
    argv[1] = NATIVE
    return original(argv, **options)
step.run_logged = invoke
raise SystemExit(step.main())
'''.replace('ROOT', repr(str(module_root))).replace('NATIVE', repr(str(native_bootstrap))), encoding='utf-8')
    binding = {'source_sample': 'sample', 'container': 'gateway', 'docker_user': '1001:1001', 'config_path': '/config.json',
               'agent_id': 'main', 'account_id': 'eval', 'user_id': 'alice', 'client_spiffe_id': 'spiffe://a/client', 'server_spiffe_id': 'spiffe://a/server'}
    atomic(tmp_path / 'fixture.json', {'schema': 'argus.locomo-derived.v1', 'source_sha256': 'a'*64, 'tasks': [
        {'task_id': 'sample-q1', 'source_sample': 'sample', 'category': 4, 'sessions': [{'source_session': 'session_1',
         'messages': [{'speaker': 'Alice', 'text': 'I visited the museum.'}]}], 'question': 'Where did Alice visit?', 'reference_answer': 'museum'}]})
    atomic(tmp_path / 'locomo.json', {'schema': 'argus.locomo-run.v1', 'fixture': 'fixture.json', 'bindings': [binding], 'poll_attempts': 1, 'poll_seconds': 0})
    argv = [sys.executable, str(step_bootstrap), 'locomo', 'run', '--config', '{locomo_config}', '--output', '{run_dir}', '--timeout', '20']
    resume = argv.copy(); resume[3] = 'resume'
    manifest = {'schema': 'argus.experiment.v1', 'experiment_id': 'lc-test', 'groups': ['full_argus'], 'seeds': [0], 'scales': [1],
        'policy': {'can_reattest': False}, 'scope': 'private_user_memories', 'artifacts': [str(tmp_path / 'locomo.json'), str(tmp_path / 'fixture.json')],
        'arms': {'full_argus': {'identity_namespace': 'full', 'registration_namespace': 'full', 'state_dir': '/experiment', 'locomo_config': str(tmp_path / 'locomo.json')}},
        'cases': [{'name': 'locomo', 'experiment': 'E4', 'operations': [{'id': 'locomo', 'role': 'client', 'kind': 'mutation', 'timeout_s': 25,
            'argv': argv, 'resume_argv': resume, 'operation_id_file': 'locomo/state.json', 'result': 'step-result.json', 'verdict_field': 'result'}]}]}
    atomic(tmp_path / 'suite.json', manifest)
    prepared = runner.prepare(tmp_path / 'suite.json', tmp_path / 'experiment')
    with patch.object(runner, 'preflight', return_value={'result': 'PASS'}):
        assert runner.execute(tmp_path / 'experiment', 'client')['result'] == 'UNKNOWN'
        run = prepared['runs'][0]
        state = read(tmp_path / 'experiment/runs' / run['run_id'] / 'locomo/state.json')
        assert state['run_id'] == run['run_id'] and state['operation_id']
        (tmp_path / 'ready').write_text('ready')
        assert runner.execute(tmp_path / 'experiment', 'client', resume=True)['result'] == 'COMPLETE'
        before = (tmp_path / 'calls.jsonl').read_bytes()
        runner.execute(tmp_path / 'experiment', 'client', resume=True)
        assert (tmp_path / 'calls.jsonl').read_bytes() == before
    calls = [json.loads(line) for line in before.splitlines()]
    assert sum(c.get('route', '').endswith('/commit') for c in calls) == 1
    assert sum(c.get('route', '').endswith('/messages') for c in calls) == 1
    assert sum(c['action'] == 'qa' for c in calls) == 1
    envelope = read(tmp_path / 'experiment/runs' / run['run_id'] / 'step-result.json')
    assert envelope['native_result'] == 'COMPLETE'
    assert envelope['evidence_scope'] == 'application_workload_completion_not_security'
