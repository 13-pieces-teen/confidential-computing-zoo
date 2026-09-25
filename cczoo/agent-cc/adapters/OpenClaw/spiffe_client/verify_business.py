#!/usr/bin/env python3
"""Offline verifier for real Gateway E2E artifacts, not an evidence generator."""
import json
from pathlib import Path
import sys
import argparse
import hashlib
from verify_audit import find_recall, records


class BusinessFailure(ValueError):
    pass


def recall_failure(lines, session_key, fact):
    digest = hashlib.sha256(fact.encode()).hexdigest()
    attempts = [v for v in records(lines) if v.get('component') == 'argus-openclaw-recall'
                and v.get('session_key') == session_key and v.get('event') in ('completed', 'failed')]
    if not attempts:
        return 'RECALL_AUDIT_MISSING'
    value = attempts[-1]
    if value.get('event') == 'failed':
        return 'ASSEMBLY_FAILED'
    if digest in value.get('input_fact_hashes', []):
        return 'FACT_LEAKED'
    if digest in value.get('recall_fact_hashes', []) and digest not in value.get('output_fact_hashes', []):
        return 'INJECTION_FAILED'
    search = value.get('search') or {}
    if search.get('search_failed_count', 0):
        return 'RETRIEVAL_FAILED'
    if search.get('search_count', 0) and search.get('candidate_count') == 0:
        return 'RETRIEVAL_EMPTY'
    if digest in search.get('candidate_fact_hashes', []) and digest not in value.get('recall_fact_hashes', []):
        return 'RECALL_SELECTION_FAILED'
    if digest not in value.get('recall_fact_hashes', []):
        return 'FACT_NOT_RETRIEVED' if value.get('source_observed') else 'RECALL_NOT_OBSERVED'
    return 'RECALL_IDENTITY_EVIDENCE_MISSING'


def answer(path):
    value = json.loads(path.read_text())
    if value.get('status') != 'ok' or not value.get('runId') or value.get('error'):
        raise ValueError(f'{path.name}: Gateway result is not successful')
    payloads = value.get('result', {}).get('payloads')
    if not isinstance(payloads, list):
        raise ValueError(f'{path.name}: expected result.payloads from Gateway; inspect this version explicitly')
    return '\n'.join(v['text'] for v in payloads if isinstance(v.get('text'), str)).strip()


def verify(directory, session_key, client_id='spiffe://argus.local/agent/openclaw',
           server_id='spiffe://argus.local/service/openviking-cmem'):
    fact = (directory / 'fact.txt').read_text().strip()
    query = (directory / 'recall-prompt.txt').read_text()
    if fact in query:
        raise BusinessFailure('FACT_LEAKED')
    lines = (directory / 'gateway.log').read_text(encoding='utf-8').splitlines()
    try:
        receipt = find_recall(lines, session_key, fact, client_id, server_id)
    except ValueError:
        raise BusinessFailure(recall_failure(lines, session_key, fact)) from None
    if answer(directory / 'recall-response.json') != fact:
        raise BusinessFailure('ANSWER_FAILED')
    if answer(directory / 'negative-response.json') != 'UNKNOWN':
        raise BusinessFailure('NEGATIVE_CONTROL_FAILED')
    return {'result': 'PASS', 'code': 'PASS', 'recall_answer': True, 'negative_control': True, **receipt}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('session_key')
    parser.add_argument('--client-id', default='spiffe://argus.local/agent/openclaw')
    parser.add_argument('--server-id', default='spiffe://argus.local/service/openviking-cmem')
    args = parser.parse_args()
    try:
        result = verify(args.directory, args.session_key, args.client_id, args.server_id)
    except Exception as error:
        result = {'result': 'FAIL', 'code': str(error) if isinstance(error, BusinessFailure) else 'BUSINESS_EVIDENCE_INVALID'}
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['result'] == 'PASS' else 1)
