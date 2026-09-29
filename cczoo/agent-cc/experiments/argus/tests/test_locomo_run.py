import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import atomic, read
from locomo import convert
from locomo_run import Gateway, configuration, execute, injection_evidence, score, main
from locomo_execution import application_summary


class FakeGateway:
    """Controlled application adapter, never reported as a real Gateway run."""
    def __init__(self, fail=None, pending=False):
        self.calls = []
        self.fail = fail
        self.pending = pending

    def call(self, binding, action, **payload):
        self.calls.append((action, copy.deepcopy(payload)))
        if action == 'preflight':
            return {'result': 'OBSERVED', 'read_only_qa': True, 'scope': {'user_id': binding['user_id']}, 'config_sha256': 'cfg'}
        if action == 'qa':
            return {'result': 'OBSERVED', 'answer': 'museum', 'run_id': 'run-' + payload['session_key'], 'duration_ms': 12}
        route = payload['route']
        if self.fail and self.fail in route:
            return {'result': 'UNKNOWN', 'code': 'INTERRUPTED'}
        if '/tasks/' in route:
            value = {'status': 'running'} if self.pending else {'status': 'completed', 'result': {'memories_extracted': {}}}
        elif route.endswith('/commit'):
            value = {'task_id': 'task-id', 'status': 'accepted', 'archived': True}
        elif route.endswith('/context'):
            value = {'latest_archive_overview': 'archived source'}
        elif payload['method'] == 'GET':
            value = {'commit_count': 1}
        else:
            value = {}
        return {'result': 'OBSERVED', 'value': value, 'receipts': [{'request_id': str(len(self.calls))}]}

    def audit(self, binding, since, session_key):
        return {'result': 'OBSERVED', 'code': 'INJECTION_OBSERVED'}


class LoCoMoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source.json'
        atomic(self.source, [{'sample_id': 'conversation-a', 'conversation': {
            'session_1_date_time': '1:00 pm on 1 May, 2023',
            'session_1': [{'speaker': 'Alice', 'text': 'I visited the museum.', 'dia_id': 'D1:1'}]},
            'qa': [{'question': 'Where did Alice visit?', 'answer': 'museum', 'category': 4, 'evidence': ['D1:1']},
                   {'question': 'What did Alice do?', 'answer': 'visited museum', 'category': 2, 'evidence': ['D1:1']}]}])
        self.fixture = self.root / 'fixture.json'
        convert(self.source, self.fixture)
        self.binding = {'source_sample': 'conversation-a', 'container': 'gateway-a', 'docker_user': '10001:10001',
                        'config_path': '/home/node/.openclaw/openclaw.json', 'agent_id': 'main', 'account_id': 'eval',
                        'user_id': 'private-a', 'client_spiffe_id': 'spiffe://argus.local/client/a',
                        'server_spiffe_id': 'spiffe://argus.local/service/memory'}
        self.config = self.root / 'config.json'
        atomic(self.config, {'schema': 'argus.locomo-run.v1', 'fixture': str(self.fixture), 'bindings': [self.binding],
                             'poll_attempts': 1, 'poll_seconds': 0})
        self.output = self.root / 'run'

    def test_stratification_is_seeded_and_covers_conversations_categories(self):
        rows = read(self.source)
        for i in range(1, 4):
            row = copy.deepcopy(rows[0]); row['sample_id'] = 'conversation-' + str(i)
            row['qa'] *= 4
            rows.append(row)
        atomic(self.source, rows)
        first = convert(self.source, self.fixture, limit=8, seed=11)
        second = convert(self.source, self.root / 'other.json', limit=8, seed=11)
        self.assertEqual(first, second)
        self.assertEqual(len({(t['source_sample'], t['category']) for t in first['tasks']}), 8)

    def test_pipeline_imports_once_has_fresh_sessions_and_no_reference_in_questions(self):
        adapter = FakeGateway()
        result = execute(self.config, self.output, gateway=adapter)
        self.assertEqual(result['result'], 'COMPLETE')
        commits = [p for action, p in adapter.calls if p.get('route', '').endswith('/commit')]
        self.assertEqual(len(commits), 1)
        questions = [p for action, p in adapter.calls if action == 'qa']
        self.assertEqual(len({p['session_key'] for p in questions}), 2)
        self.assertTrue(all('museum' not in p['question'] for p in questions))
        self.assertTrue(all('reference_answer' not in p for p in questions))
        state = read(self.output / 'state.json')
        self.assertEqual(state['conversations']['conversation-a']['sessions']['session_1']['extraction']['total'], 0)
        self.assertEqual(result['overall']['token_f1']['eligible'], 2)
        self.assertEqual(result['delivery_compliance'], 'NOT_ASSESSED_BY_QA')
        before = len([p for a, p in adapter.calls if p.get('method') == 'POST' or a == 'qa'])
        execute(self.config, self.output, resume=True, gateway=adapter)
        self.assertEqual(before, len([p for a, p in adapter.calls if p.get('method') == 'POST' or a == 'qa']))

    def test_known_task_timeout_resumes_without_recommit(self):
        adapter = FakeGateway(pending=True)
        result = execute(self.config, self.output, gateway=adapter)
        self.assertEqual(result['result'], 'INCOMPLETE')
        self.assertFalse(any(a == 'qa' for a, _ in adapter.calls))
        adapter.pending = False
        result = execute(self.config, self.output, resume=True, gateway=adapter)
        self.assertEqual(result['result'], 'COMPLETE')
        self.assertEqual(sum(p.get('route', '').endswith('/commit') for _, p in adapter.calls), 1)

    def test_unknown_commit_is_not_replayed(self):
        adapter = FakeGateway(fail='/commit')
        execute(self.config, self.output, gateway=adapter)
        adapter.fail = None
        result = execute(self.config, self.output, resume=True, gateway=adapter)
        self.assertEqual(result['result'], 'INCOMPLETE')
        self.assertEqual(sum(p.get('route', '').endswith('/commit') for _, p in adapter.calls), 1)
        self.assertEqual(result['overall']['token_f1']['all_tasks_zero_for_uncompleted'], 0)

    def test_unknown_message_is_not_replayed(self):
        adapter = FakeGateway(fail='/messages')
        execute(self.config, self.output, gateway=adapter)
        adapter.fail = None
        execute(self.config, self.output, resume=True, gateway=adapter)
        self.assertEqual(sum(p.get('route', '').endswith('/messages') for _, p in adapter.calls), 1)

    def test_unknown_qa_is_not_replayed_or_scored_as_answer(self):
        adapter = FakeGateway()
        original = adapter.call
        def call(binding, action, **payload):
            observation = original(binding, action, **payload)
            return {'result': 'UNKNOWN', 'code': 'LOST_RESULT'} if action == 'qa' else observation
        adapter.call = call
        execute(self.config, self.output, gateway=adapter)
        result = execute(self.config, self.output, resume=True, gateway=adapter)
        self.assertEqual(sum(a == 'qa' for a, _ in adapter.calls), 2)
        self.assertEqual(result['overall']['token_f1']['measured'], 0)
        self.assertIsNone(result['overall']['token_f1']['answered_mean'])

    def test_task_read_failure_has_bounded_queries_and_known_task_resume(self):
        adapter = FakeGateway(fail='/tasks/')
        execute(self.config, self.output, gateway=adapter)
        self.assertEqual(sum('/tasks/' in p.get('route', '') for _, p in adapter.calls), 2)
        adapter.fail = None
        result = execute(self.config, self.output, resume=True, gateway=adapter)
        self.assertEqual(result['result'], 'COMPLETE')
        self.assertEqual(sum(p.get('route', '').endswith('/commit') for _, p in adapter.calls), 1)

    def test_failed_preflight_performs_no_writes(self):
        adapter = FakeGateway()
        adapter.call = lambda *a, **k: {'result': 'UNKNOWN', 'code': 'PROTOCOL_MISMATCH'}
        with self.assertRaisesRegex(ValueError, 'preflight failed'):
            execute(self.config, self.output, gateway=adapter)
        self.assertEqual(read(self.output / 'state.json')['operations'], {})

    def test_category_five_without_reference_is_an_abstention_task(self):
        rows = read(self.source)
        rows[0]['qa'].append({'question': 'What is the unknown favorite color?', 'category': 5})
        atomic(self.source, rows)
        fixture = convert(self.source, self.fixture, categories=(5,))
        self.assertEqual(fixture['tasks'][0]['reference_answer'], 'UNKNOWN')

    def test_duplicate_business_scope_is_rejected(self):
        fixture = read(self.fixture)
        second = copy.deepcopy(fixture['tasks'][0]); second.update(task_id='second-question', source_sample='second')
        fixture['tasks'].append(second); atomic(self.fixture, fixture)
        config = read(self.config)
        duplicate = self.binding | {'source_sample': 'second', 'container': 'second-gateway'}
        config['bindings'].append(duplicate); atomic(self.config, config)
        with self.assertRaisesRegex(ValueError, 'own user'):
            configuration(self.config)

    def test_changed_fixture_rejected_on_resume(self):
        execute(self.config, self.output, gateway=FakeGateway())
        value = read(self.fixture); value['tasks'][0]['question'] += '?'; atomic(self.fixture, value)
        with self.assertRaisesRegex(ValueError, 'configuration or fixture changed'):
            execute(self.config, self.output, resume=True, gateway=FakeGateway())

    def test_runner_run_and_operation_binding_survives_resume_and_rejects_reassignment(self):
        with patch.dict(os.environ, ARGUS_RUN_ID='runner-run', ARGUS_OPERATION_ID='runner-operation'):
            result = execute(self.config, self.output, gateway=FakeGateway())
            state = read(self.output / 'state.json')
            self.assertEqual((result['run_id'], result['operation_id']), ('runner-run', 'runner-operation'))
            self.assertEqual((state['run_id'], state['operation_id']), ('runner-run', 'runner-operation'))
            execute(self.config, self.output, resume=True, gateway=FakeGateway())
        with patch.dict(os.environ, ARGUS_RUN_ID='runner-run', ARGUS_OPERATION_ID='different-operation'):
            with self.assertRaisesRegex(ValueError, 'runner association changed'):
                execute(self.config, self.output, resume=True, gateway=FakeGateway())

    def test_empty_extraction_and_failed_task_are_distinct(self):
        adapter = FakeGateway()
        original = adapter.call
        def call(binding, action, **payload):
            result = original(binding, action, **payload)
            if '/tasks/' in payload.get('route', ''):
                result['value'] = {'status': 'failed'}
            return result
        adapter.call = call
        result = execute(self.config, self.output, gateway=adapter)
        self.assertEqual(result['result'], 'INCOMPLETE')
        self.assertEqual(read(self.output / 'state.json')['conversations']['conversation-a']['reason'], 'EXTRACTION_FAILED')

    def test_audit_requires_real_identity_and_block_inclusion(self):
        span = {'component': 'argus-openclaw-recall', 'event': 'completed', 'session_key': 'qa',
                'request_ids': ['request'], 'recall_block_chars': 8, 'recall_block_sha256': 'digest', 'recall_block_in_output': True}
        receipt = {'request_id': 'request', 'http_status': 200, 'client_spiffe_id': self.binding['client_spiffe_id'],
                   'server_spiffe_id': self.binding['server_spiffe_id']}
        self.assertEqual(injection_evidence([span, receipt], 'qa', self.binding)['code'], 'INJECTION_OBSERVED')
        span['recall_block_in_output'] = False
        self.assertEqual(injection_evidence([span, receipt], 'qa', self.binding)['code'], 'INJECTION_UNOBSERVED')
        receipt['server_spiffe_id'] = 'wrong'
        self.assertEqual(injection_evidence([span, receipt], 'qa', self.binding)['code'], 'RECALL_IDENTITY_UNOBSERVED')

    def test_abstention_and_token_scoring_are_explicit(self):
        self.assertEqual(score('UNKNOWN', {'category': 5})['abstention'], 1)
        self.assertEqual(score('It was Paris', {'category': 5})['abstention'], 0)
        self.assertEqual(score('the museum', {'category': 4, 'reference_answer': 'museum'})['token_f1'], 1)
        self.assertEqual(score('Italy, France', {'category': 1, 'reference_answer': 'Italy, France'})['exact_match'], 1)

    def test_actual_adapter_passes_payload_via_stdin_not_argv(self):
        response = type('Result', (), {'stdout': '{"result":"OBSERVED","answer":"museum"}\n'})()
        with patch('locomo_run.subprocess.run', return_value=response) as process:
            Gateway().call(self.binding, 'qa', question='private question', session_key='fresh')
        command = process.call_args.args[0]
        self.assertNotIn('private question', command)
        self.assertIn('private question', process.call_args.kwargs['input'])
        self.assertIn('createSpiffeTransport', command[-1])

    def second_client(self):
        fixture = read(self.fixture)
        for task in list(fixture['tasks']):
            other = copy.deepcopy(task)
            other.update(task_id='second-' + task['task_id'], source_sample='conversation-b')
            fixture['tasks'].append(other)
        atomic(self.fixture, fixture)
        config = read(self.config)
        config['bindings'].append(self.binding | {'source_sample': 'conversation-b', 'container': 'gateway-b',
                                 'user_id': 'private-b', 'client_spiffe_id': 'spiffe://argus.local/client/b'})
        config['concurrent_clients'] = True
        atomic(self.config, config)

    def test_all_histories_finish_before_real_parallel_gateway_invocations(self):
        self.second_client()
        adapter = FakeGateway()
        original = adapter.call
        barrier = threading.Barrier(2, timeout=2)
        guard = threading.Lock()
        initialized, active, first, overlap = set(), {}, set(), []

        def call(binding, action, **payload):
            client = binding['user_id']
            if action != 'qa':
                result = original(binding, action, **payload)
                if payload.get('route', '').endswith('/context'):
                    initialized.add(client)
                return result
            with guard:
                self.assertEqual(initialized, {'private-a', 'private-b'})
                self.assertEqual(active.get(client, 0), 0)
                active[client] = 1
                initial = client not in first
                first.add(client)
            if initial:
                barrier.wait()
            with guard:
                overlap.append(sum(active.values()))
            time.sleep(.025)
            result = original(binding, action, **payload)
            with guard:
                active[client] = 0
            return result

        adapter.call = call
        result = execute(self.config, self.output, gateway=adapter)
        self.assertEqual(result['application']['planned'], 4)
        self.assertEqual(result['application']['completed'], 4)
        self.assertEqual(max(overlap), 2)
        self.assertEqual(result['application']['concurrency']['max_in_flight'], 2)
        self.assertEqual(set(result['application']['by_client']), {'conversation-a', 'conversation-b'})

    def test_fault_and_recovery_clock_does_not_wait_for_qa(self):
        config = read(self.config)
        config.update(schedule={'release_interval_s': .12, 'deadline_s': .5}, condition='fault',
                      controls={'fault': {'at_s': .03, 'argv': ['fake-fault']},
                                'recovery': {'at_s': .06, 'argv': ['fake-recovery']}})
        atomic(self.config, config)
        adapter = FakeGateway()
        original = adapter.call
        control_times, ends = {}, []

        def run(argv, **kwargs):
            control_times[argv[0]] = time.monotonic()
            return type('Result', (), {'returncode': 0})()

        def call(binding, action, **payload):
            if action == 'qa':
                time.sleep(.15)
                ends.append(time.monotonic())
            return original(binding, action, **payload)

        adapter.call = call
        with patch('locomo_execution.subprocess.run', side_effect=run):
            result = execute(self.config, self.output, gateway=adapter)
        self.assertLess(control_times['fake-fault'], ends[0])
        self.assertLess(control_times['fake-recovery'], ends[0])
        self.assertEqual(result['execution']['controls']['recovery']['status'], 'completed')
        self.assertEqual(result['application']['planned'], 2)
        self.assertIsNotNone(result['application']['recovery']['first_task_ms'])

    def test_deadline_keeps_unstarted_questions_in_denominator(self):
        config = read(self.config)
        config['schedule'] = {'release_interval_s': .01, 'deadline_s': .03}
        atomic(self.config, config)
        adapter = FakeGateway()
        original = adapter.call

        def call(binding, action, **payload):
            if action == 'qa':
                time.sleep(.07)
            return original(binding, action, **payload)

        adapter.call = call
        result = execute(self.config, self.output, gateway=adapter)
        self.assertEqual(sum(a == 'qa' for a, _ in adapter.calls), 1)
        self.assertEqual(result['application']['planned'], 2)
        self.assertEqual(result['application']['attempted'], 1)
        self.assertEqual(result['application']['outcome_counts']['deadline_missed'], 1)
        self.assertEqual(result['application']['deadline_misses'], 2)
        self.assertEqual(result['application']['latency']['n'], 0)
        self.assertEqual(result['overall']['token_f1']['eligible'], 2)
        self.assertEqual(result['result'], 'INCOMPLETE')
        self.assertTrue(result['measurement_complete'])

    def test_resume_after_window_only_reconciles_and_preserves_origin(self):
        config = read(self.config)
        config.update(schedule={'release_interval_s': .01, 'deadline_s': 1}, condition='no_fault',
                      controls={'fault': {'at_s': 0, 'argv': []}, 'recovery': {'at_s': .01, 'argv': []}})
        atomic(self.config, config)
        adapter = FakeGateway()
        execute(self.config, self.output, gateway=adapter)
        state = read(self.output / 'state.json')
        origin = state['qa_started_at_ms']
        ids = list(state['questions'])
        state['questions'][ids[0]].update(status='submission_unknown', outcome='unknown')
        del state['questions'][ids[1]]
        state['qa_phase'] = 'interrupted'
        state.pop('qa_completed_at_ms')
        atomic(self.output / 'state.json', state)
        before = len(adapter.calls)
        result = execute(self.config, self.output, resume=True, gateway=adapter)
        self.assertEqual(len(adapter.calls), before)
        self.assertEqual(result['execution']['qa_started_at_ms'], origin)
        self.assertEqual(result['application']['planned'], 2)
        self.assertEqual(result['application']['outcome_counts']['unknown'], 1)
        self.assertEqual(result['application']['outcome_counts']['not_run'], 1)
        self.assertEqual(result['execution']['qa_phase'], 'interrupted')
        self.assertFalse(result['measurement_complete'])

    def test_pending_initialization_does_not_start_fault_clock(self):
        config = read(self.config)
        config.update(schedule={'release_interval_s': .01, 'deadline_s': 1}, condition='fault',
                      controls={'fault': {'at_s': 0, 'argv': ['fault']},
                                'recovery': {'at_s': .01, 'argv': ['recover']}})
        atomic(self.config, config)
        with patch('locomo_execution.subprocess.run') as command:
            result = execute(self.config, self.output, gateway=FakeGateway(pending=True))
        command.assert_not_called()
        self.assertIsNone(result['execution']['qa_started_at_ms'])
        self.assertEqual(result['application']['outcome_counts']['not_run'], 2)

    def test_slow_audit_collection_does_not_block_next_planned_question(self):
        config = read(self.config)
        config['schedule'] = {'release_interval_s': .03, 'deadline_s': .1}
        atomic(self.config, config)
        adapter = FakeGateway()
        original = adapter.audit

        def audit(*args):
            time.sleep(.15)
            return original(*args)

        adapter.audit = audit
        result = execute(self.config, self.output, gateway=adapter)
        self.assertEqual(result['application']['attempted'], 2)
        self.assertEqual(result['application']['deadline_misses'], 0)
        self.assertEqual(result['application']['valid_completed'], 2)

    def test_terminal_body_failure_is_not_complete_success_or_injection(self):
        span = {'component': 'argus-openclaw-recall', 'event': 'completed', 'session_key': 'qa',
                'context_span_id': 'span', 'request_ids': ['request'], 'recall_block_chars': 8,
                'recall_block_in_output': True}
        common = {'request_id': 'request', 'http_status': 200, 'context_span_id': 'span',
                  'client_spiffe_id': self.binding['client_spiffe_id'], 'server_spiffe_id': self.binding['server_spiffe_id']}
        headers = common | {'event': 'response_headers'}
        incomplete = injection_evidence([span, headers], 'qa', self.binding)
        self.assertFalse(incomplete['request_audit_complete'])
        self.assertEqual(incomplete['requests'][0]['outcome'], 'unknown')
        failure = common | {'event': 'request_failed', 'error_code': 'ETIMEDOUT', 'phase': 'response_body', 'duration_ms': 10}
        observed = injection_evidence([span, headers, failure], 'qa', self.binding)
        self.assertTrue(observed['request_audit_complete'])
        self.assertEqual(observed['requests'][0]['outcome'], 'timeout')
        self.assertEqual(observed['associated_requests'], 0)
        self.assertEqual(observed['code'], 'RECALL_IDENTITY_UNOBSERVED')

    def test_request_failures_are_correlated_even_without_a_completed_recall(self):
        records = [{'session_key': 'qa', 'request_id': 'deny', 'event': 'response_completed', 'http_status': 403,
                    'duration_ms': 8, 'checked_at': '2026-09-29T00:00:00Z'},
                   {'session_key': 'another', 'request_id': 'ignore', 'event': 'response_completed', 'http_status': 200}]
        observed = injection_evidence(records, 'qa', self.binding)
        self.assertEqual(observed['code'], 'RECALL_AUDIT_MISSING')
        self.assertEqual(observed['request_ids'], ['deny'])
        self.assertEqual(observed['requests'][0]['outcome'], 'rejected')
        self.assertIsInstance(observed['requests'][0]['at_ms'], int)

    def test_qa_failure_categories_are_retained_and_not_success_latency(self):
        adapter = FakeGateway()
        original = adapter.call
        answers = iter([{'result': 'UNKNOWN', 'code': 'GATEWAY_TIMEOUT', 'outcome': 'timeout'},
                        {'result': 'UNKNOWN', 'code': 'APPLICATION_REQUEST_FAILED', 'outcome': 'rejected'}])

        def call(binding, action, **payload):
            observation = original(binding, action, **payload)
            return next(answers) if action == 'qa' else observation

        adapter.call = call
        result = execute(self.config, self.output, gateway=adapter)
        self.assertEqual(result['application']['outcome_counts']['timeout'], 1)
        self.assertEqual(result['application']['outcome_counts']['rejected'], 1)
        self.assertEqual(result['application']['latency']['n'], 0)
        self.assertEqual(result['application']['completion_rate'], 0)

    def test_fault_controls_require_fixed_schedule_and_explicit_commands(self):
        config = read(self.config)
        config['condition'] = 'fault'
        atomic(self.config, config)
        with self.assertRaisesRegex(ValueError, 'requires controls'):
            configuration(self.config)
        config['controls'] = {'fault': {'at_s': 1, 'argv': ['fault']}, 'recovery': {'at_s': 2, 'argv': ['recover']}}
        atomic(self.config, config)
        with self.assertRaisesRegex(ValueError, 'require a schedule'):
            configuration(self.config)

    def test_recovery_does_not_count_old_inflight_answer_or_response(self):
        fixture = read(self.fixture)
        tasks = fixture['tasks']
        old = {'status': 'completed', 'outcome': 'completed', 'started_at_ms': 10, 'finished_at_ms': 150,
               'injection': {'code': 'INJECTION_OBSERVED', 'requests': [{'request_id': 'old', 'outcome': 'completed',
                             'started_at_ms': 20, 'at_ms': 140}]}}
        new = {'status': 'completed', 'outcome': 'completed', 'started_at_ms': 160, 'finished_at_ms': 200,
               'injection': {'code': 'INJECTION_OBSERVED', 'requests': [{'request_id': 'new', 'outcome': 'completed',
                             'started_at_ms': 170, 'at_ms': 190}]}}
        state = {'controls': {'recovery': {'status': 'completed', 'started_at_ms': 100}},
                 'questions': {tasks[0]['task_id']: old, tasks[1]['task_id']: new}}
        result = application_summary(fixture, state)['recovery']
        self.assertEqual(result['first_successful_access_at_ms'], 190)
        self.assertEqual(result['first_completed_task_at_ms'], 200)

    def test_cli_complete_measurement_with_failed_answers_exits_successfully(self):
        adapter = FakeGateway()
        original = adapter.call

        def call(binding, action, **payload):
            response = original(binding, action, **payload)
            return {'result': 'UNKNOWN', 'code': 'GATEWAY_TIMEOUT', 'outcome': 'timeout'} if action == 'qa' else response

        adapter.call = call
        with patch('locomo_run.Gateway', return_value=adapter), patch('builtins.print'), \
                patch.object(sys, 'argv', ['locomo_run.py', 'run', '--config', str(self.config), '--output', str(self.output)]):
            self.assertEqual(main(), 0)
        result = read(self.output / 'result.json')
        self.assertEqual(result['result'], 'INCOMPLETE')
        self.assertTrue(result['measurement_complete'])
        self.assertEqual(result['application']['outcome_counts']['timeout'], 2)

    def test_cli_incomplete_initialization_retains_failure_exit(self):
        with patch('locomo_run.Gateway', return_value=FakeGateway(pending=True)), patch('builtins.print'), \
                patch.object(sys, 'argv', ['locomo_run.py', 'run', '--config', str(self.config), '--output', str(self.output)]):
            self.assertEqual(main(), 1)


if __name__ == '__main__':
    unittest.main()
