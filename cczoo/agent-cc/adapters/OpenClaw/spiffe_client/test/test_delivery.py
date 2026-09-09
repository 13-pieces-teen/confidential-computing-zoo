import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class DeliveryTests(unittest.TestCase):
    def test_bad_upstream_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'integrity'):
            module('build_plugin').build(b'wrong-release')

    @unittest.skipUnless(os.environ.get('ARGUS_TEST_UPSTREAM'), 'set ARGUS_TEST_UPSTREAM to integrity-pinned npm tarball')
    def test_reproducible_package_and_complete_transport_patch(self):
        builder = module('build_plugin')
        source = Path(os.environ['ARGUS_TEST_UPSTREAM']).read_bytes()
        first, receipt = builder.build(source)
        second, _ = builder.build(source)
        self.assertEqual(first, second)
        self.assertEqual(receipt['customization'], 'argus.2')
        with tarfile.open(fileobj=io.BytesIO(first), mode='r:gz') as archive:
            for path in ('adapters/http-transport.ts', 'dist/adapters/http-transport.js',
                         'commands/setup.ts', 'dist/commands/setup.js', 'services/setup/probe-service.ts'):
                text = archive.extractfile('package/' + path).read().decode()
                self.assertIn('spiffeFetch', text)
                self.assertNotIn('await fetch(', text)
                self.assertNotIn('=> fetch(', text)
            self.assertIsNotNone(archive.getmember('package/dist/argus-spiffe/transport.mjs'))

    def test_health_or_another_session_cannot_pass_gateway_write_acceptance(self):
        verifier = module('verify_audit')
        value = {'component': 'argus-openclaw-spiffe', 'method': 'POST', 'path': '/api/v1/sessions/specific/messages',
                 'client_spiffe_id': 'spiffe://argus.local/agent/openclaw',
                 'server_spiffe_id': 'spiffe://argus.local/service/openviking-cmem', 'http_status': 200,
                 'request_id': 'request1', 'client_serial': '01', 'server_serial': '02', 'generation': 'generation-a'}
        line = json.dumps(value, separators=(',', ':'))
        self.assertEqual(verifier.find_write(['prefix ' + line], 'specific')['request_id'], 'request1')
        with self.assertRaises(ValueError): verifier.find_write([line], 'other-session')
        for changes in ({'path': '/health'}, {'method': 'GET'}, {'http_status': 403}, {'client_serial': ''}):
            changed = json.dumps({**value, **changes}, separators=(',', ':'))
            with self.assertRaises(ValueError): verifier.find_write([changed], 'specific')

    def test_recall_requires_unleaked_fact_and_matching_transport_span(self):
        verifier = module('verify_audit')
        fact = 'ARGUS_FACT_' + 'A'*32
        digest = hashlib.sha256(fact.encode()).hexdigest()
        recall = {'component':'argus-openclaw-recall','session_key':'fresh','context_span_id':'span-a',
                  'request_ids':['request-a'],'input_fact_hashes':[],
                  'recall_fact_hashes':[digest],'output_fact_hashes':[digest]}
        request = {'component':'argus-openclaw-spiffe','context_span_id':'span-a','request_id':'request-a',
                   'path':'/api/v1/search/find','http_status':200,'client_serial':'a','server_serial':'b','generation':'g',
                   'client_spiffe_id':'spiffe://argus.local/agent/openclaw',
                   'server_spiffe_id':'spiffe://argus.local/service/openviking-cmem'}
        def lines(r, t): return [json.dumps(v,separators=(',',':')) for v in (r,t)]
        self.assertIn('recall',verifier.find_recall(lines(recall,request),'fresh',fact))
        for changes in ({'input_fact_hashes':[digest]},{'output_fact_hashes':[]},{'session_key':'old'}):
            with self.assertRaises(ValueError): verifier.find_recall(lines(dict(recall,**changes),request),'fresh',fact)
        for changes in ({'context_span_id':'other'},{'http_status':403},{'path':'/health'},{'client_serial':''}):
            with self.assertRaises(ValueError): verifier.find_recall(lines(recall,dict(request,**changes)),'fresh',fact)


if __name__ == '__main__':
    unittest.main()
