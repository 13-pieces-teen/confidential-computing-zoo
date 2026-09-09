#!/usr/bin/env python3
"""Require actual gateway transport evidence for the E2E session's write."""
import json
import hashlib
import sys
from urllib.parse import quote


def records(lines):
    for line in lines:
        start = line.find('{"component":"argus-openclaw-')
        if start >= 0:
            try:
                yield json.JSONDecoder().raw_decode(line[start:])[0]
            except ValueError:
                pass


def find_recall(lines, session_key, fact):
    values = list(records(lines))
    digest = hashlib.sha256(fact.encode()).hexdigest()
    for value in values:
        if (value.get('component') != 'argus-openclaw-recall' or value.get('session_key') != session_key
                or digest in value.get('input_fact_hashes', [])
                or digest not in value.get('recall_fact_hashes', [])
                or digest not in value.get('output_fact_hashes', [])):
            continue
        related = [r for r in values if r.get('component') == 'argus-openclaw-spiffe'
                   and r.get('context_span_id') == value.get('context_span_id')
                   and r.get('request_id') in value.get('request_ids', [])
                   and r.get('path', '').startswith('/api/v1/')
                   and r.get('path') not in ('/api/v1/sessions',)
                   and r.get('client_spiffe_id') == 'spiffe://argus.local/agent/openclaw'
                   and r.get('server_spiffe_id') == 'spiffe://argus.local/service/openviking-cmem'
                   and r.get('client_serial') and r.get('server_serial') and r.get('generation')
                   and 200 <= r.get('http_status', 0) < 300]
        if related:
            return {'recall': value, 'transport': related, 'model_input_boundary': 'ContextEngine assemble return'}
    raise ValueError('No fresh-session recall-to-model-input evidence linked to Gateway mTLS')


def find_write(lines, session):
    expected = '/api/v1/sessions/' + quote(session, safe='') + '/messages'
    for line in lines:
        start = line.find('{"component":"argus-openclaw-spiffe"')
        if start < 0:
            continue
        try:
            value, _ = json.JSONDecoder().raw_decode(line[start:])
        except ValueError:
            continue
        if (value.get('path') == expected and value.get('method') == 'POST'
                and value.get('client_spiffe_id') == 'spiffe://argus.local/agent/openclaw'
                and value.get('server_spiffe_id') == 'spiffe://argus.local/service/openviking-cmem'
                and isinstance(value.get('http_status'), int) and 200 <= value['http_status'] < 300
                and all(value.get(key) for key in ('request_id', 'client_serial', 'server_serial', 'generation'))):
            return value
    raise ValueError('No gateway native-mTLS write receipt for the accepted OpenViking session')


if __name__ == '__main__':
    # Consume all input before returning, so docker logs does not fail with
    # SIGPIPE under set -o pipefail when the matching receipt occurs early.
    print(json.dumps(find_write(sys.stdin.readlines(), sys.argv[1])))
