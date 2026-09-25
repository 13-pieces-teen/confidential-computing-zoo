import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import business_evidence
import verify_business
import verify_audit


class BusinessTests(unittest.TestCase):
    def test_exit_zero_requires_complete_matching_processing_write_and_recall_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            self.assertEqual(business_evidence.finish(p, 0)['result'], 'FAIL')
            identity = {'client_spiffe_id': 'spiffe://example/agent/b', 'server_spiffe_id': 'spiffe://example/memory'}
            state = {'schema_version': 1, 'scope': identity, 'stage': 'processing', 'code': 'PASSED',
                     'archive': True, 'plugin_readback': True, 'extraction': {'total': 1}, 'session_id': 's'}
            fact = 'ARGUS_FACT_' + 'D' * 32
            digest = hashlib.sha256(fact.encode()).hexdigest()
            write = {'component': 'argus-openclaw-spiffe', 'method': 'POST', 'path': '/api/v1/sessions/s/messages',
                     'http_status': 200, 'request_id': 'write', 'client_serial': 'a', 'server_serial': 'b',
                     'generation': 'g', **identity}
            request = {**write, 'method': 'POST', 'path': '/api/v1/search/find', 'request_id': 'search', 'context_span_id': 'span'}
            recall = {'component': 'argus-openclaw-recall', 'event': 'completed', 'session_key': 'fresh',
                      'context_span_id': 'span', 'request_ids': ['search'], 'input_fact_hashes': [],
                      'recall_fact_hashes': [digest], 'output_fact_hashes': [digest]}
            def put(name, value):
                (p / name).write_text(json.dumps(value), encoding='utf-8')
            put('processing-events.jsonl', state)
            put('identity.json', identity)
            put('write-mtls.json', write)
            (p / 'fact.txt').write_text(fact, encoding='utf-8')
            (p / 'recall-session-key.txt').write_text('fresh', encoding='utf-8')
            (p / 'recall-prompt.txt').write_text('lookup marker only', encoding='utf-8')
            (p / 'gateway.log').write_text('\n'.join(json.dumps(v, separators=(',', ':')) for v in (write, request, recall)), encoding='utf-8')
            for name, answer in [('recall-response.json', fact), ('negative-response.json', 'UNKNOWN')]:
                put(name, {'status': 'ok', 'runId': name, 'result': {'payloads': [{'text': answer}]}})
            put('recall-verification.json', verify_business.verify(p, 'fresh', **{'client_id': identity['client_spiffe_id'], 'server_id': identity['server_spiffe_id']}))
            self.assertEqual(business_evidence.finish(p, 0)['result'], 'PASS')
            put('write-mtls.json', {**write, 'request_id': 'wrong'})
            result = business_evidence.finish(p, 0)
            self.assertEqual(result['code'], 'ACCEPTANCE_EVIDENCE_INCOMPLETE')
            self.assertNotEqual(result['exit_code'], 0)

    def test_truncated_snapshot_keeps_known_task_and_failure_is_not_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            state = {'schema_version': 1, 'scope': {}, 'task_id': 'task-1', 'stage': 'extraction', 'code': 'EXTRACTION_TIMEOUT'}
            (p / 'processing-events.jsonl').write_text(json.dumps(state) + '\n{"schema', encoding='utf-8')
            self.assertEqual(business_evidence.latest(p), state)
            result = business_evidence.finish(p, 1)
            self.assertEqual(result['code'], 'EXTRACTION_TIMEOUT')
            self.assertTrue(result['resume_available'])
            self.assertEqual(result['result'], 'FAIL')

    def test_recall_failure_distinguishes_search_selection_and_injection(self):
        fact = 'ARGUS_FACT_' + 'A' * 32
        digest = hashlib.sha256(fact.encode()).hexdigest()
        base = {'component': 'argus-openclaw-recall', 'event': 'completed', 'session_key': 'fresh',
                'input_fact_hashes': [], 'recall_fact_hashes': [], 'output_fact_hashes': [], 'source_observed': True}
        cases = [
            ({'search': {'search_count': 1, 'candidate_count': 0}}, 'RETRIEVAL_EMPTY'),
            ({'search': {'search_count': 1, 'search_failed_count': 1}}, 'RETRIEVAL_FAILED'),
            ({'search': {'candidate_fact_hashes': [digest]}}, 'RECALL_SELECTION_FAILED'),
            ({'recall_fact_hashes': [digest]}, 'INJECTION_FAILED'),
            ({'input_fact_hashes': [digest]}, 'FACT_LEAKED'),
            ({}, 'FACT_NOT_RETRIEVED'),
            ({'source_observed': False}, 'RECALL_NOT_OBSERVED'),
            ({'event': 'failed'}, 'ASSEMBLY_FAILED'),
        ]
        for changes, code in cases:
            with self.subTest(code=code):
                line = json.dumps({**base, **changes}, separators=(',', ':'))
                self.assertEqual(verify_business.recall_failure([line], 'fresh', fact), code)
        self.assertEqual(verify_business.recall_failure([], 'fresh', fact), 'RECALL_AUDIT_MISSING')

    def test_configured_identity_is_required_and_default_identity_does_not_match(self):
        value = {'component': 'argus-openclaw-spiffe', 'method': 'POST', 'path': '/api/v1/sessions/s/messages',
                 'client_spiffe_id': 'spiffe://example/agent/b', 'server_spiffe_id': 'spiffe://example/memory',
                 'http_status': 200, 'request_id': 'r', 'client_serial': 'a', 'server_serial': 'b', 'generation': 'g'}
        lines = [json.dumps(value, separators=(',', ':'))]
        with self.assertRaises(ValueError):
            verify_audit.find_write(lines, 's')
        self.assertEqual(verify_audit.find_write(lines, 's', 'spiffe://example/agent/b', 'spiffe://example/memory'), value)


if __name__ == '__main__':
    unittest.main()
