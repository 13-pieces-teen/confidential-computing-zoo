#!/usr/bin/env python3
"""Offline verifier for real Gateway E2E artifacts, not an evidence generator."""
import json
from pathlib import Path
import sys
from verify_audit import find_recall


def answer(path):
    value = json.loads(path.read_text())
    if value.get('status') != 'ok' or not value.get('runId') or value.get('error'):
        raise ValueError(f'{path.name}: Gateway result is not successful')
    payloads = value.get('result', {}).get('payloads')
    if not isinstance(payloads, list):
        raise ValueError(f'{path.name}: expected result.payloads from Gateway; inspect this version explicitly')
    return '\n'.join(v['text'] for v in payloads if isinstance(v.get('text'), str)).strip()


def verify(directory, session_key):
    fact = (directory / 'fact.txt').read_text().strip()
    query = (directory / 'recall-prompt.txt').read_text()
    if fact in query:
        raise ValueError('expected answer leaked into recall query')
    if fact not in answer(directory / 'recall-response.json'):
        raise ValueError('fresh-session answer did not recover the saved random fact')
    if answer(directory / 'negative-response.json') != 'UNKNOWN':
        raise ValueError('negative control did not return exactly UNKNOWN')
    receipt = find_recall((directory / 'gateway.log').read_text().splitlines(), session_key, fact)
    return {'result': 'PASS', 'recall_answer': True, 'negative_control': True, **receipt}


if __name__ == '__main__':
    print(json.dumps(verify(Path(sys.argv[1]), sys.argv[2]), indent=2))
