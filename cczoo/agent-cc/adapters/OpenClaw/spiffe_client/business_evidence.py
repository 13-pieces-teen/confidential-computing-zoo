#!/usr/bin/env python3
"""Read resumable stage snapshots; never repeat a business write."""
import hashlib
import json
from pathlib import Path
import sys
from verify_audit import find_write
from verify_business import verify


def latest(directory):
    path = directory / 'processing-events.jsonl'
    state = None
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue  # A process may die while writing its last JSONL line.
            if isinstance(value, dict) and value.get('schema_version') == 1 and 'scope' in value:
                state = value
    return state


def finish(directory, exit_code):
    state = latest(directory) or {}
    reason = state.get('code', 'GATEWAY_OR_SETUP_FAILED')
    events = directory / 'processing-events.jsonl'
    if events.exists():
        last = None
        for line in events.read_text(encoding='utf-8').splitlines():
            try:
                last = json.loads(line)
            except ValueError:
                pass
        if isinstance(last, dict) and last.get('component') == 'argus-business-error':
            reason = last.get('code', 'BUSINESS_SETUP_FAILED')
    recall = directory / 'recall-verification.json'
    if reason == 'PASSED' and recall.exists():
        try:
            verification = json.loads(recall.read_text(encoding='utf-8'))
            reason = verification.get('code', 'RECALL_OR_NEGATIVE_CONTROL_FAILED')
        except ValueError:
            reason = 'RECALL_OR_NEGATIVE_CONTROL_FAILED'
    if exit_code and reason in ('PASSED', 'PASS'):
        reason = 'GATEWAY_OR_VERIFICATION_FAILED'
    if exit_code == 0:
        try:
            identity = json.loads((directory / 'identity.json').read_text(encoding='utf-8'))
            scope = state['scope']
            if (state.get('code') != 'PASSED' or not state.get('archive') or not state.get('plugin_readback')
                    or state.get('extraction', {}).get('total', 0) <= 0
                    or any(scope[key] != identity[key] for key in ('client_spiffe_id', 'server_spiffe_id'))):
                raise ValueError('processing/identity evidence incomplete')
            lines = (directory / 'gateway.log').read_text(encoding='utf-8').splitlines()
            write = find_write(lines, state['session_id'], identity['client_spiffe_id'], identity['server_spiffe_id'])
            stored_write = json.loads((directory / 'write-mtls.json').read_text(encoding='utf-8'))
            if write != stored_write:
                raise ValueError('write receipt mismatch')
            session_key = (directory / 'recall-session-key.txt').read_text(encoding='utf-8').strip()
            check = verify(directory, session_key, identity['client_spiffe_id'], identity['server_spiffe_id'])
            saved = json.loads(recall.read_text(encoding='utf-8'))
            if saved != check or check['result'] != 'PASS':
                raise ValueError('recall verification mismatch')
        except Exception:
            exit_code, reason = 1, 'ACCEPTANCE_EVIDENCE_INCOMPLETE'
    value = {'result': 'PASS' if exit_code == 0 else 'FAIL', 'code': 'PASS' if exit_code == 0 else reason,
             'exit_code': exit_code, 'processing_stage': state.get('stage'),
             'session_id': state.get('session_id'), 'task_id': state.get('task_id'),
             'resume_available': bool(state.get('task_id')), 'openclaw_tdx_remote_attestation': 'NOT_RUN',
             'scope': 'real Gateway business + mTLS + fresh-session recall'}
    (directory / 'result.json').write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    (directory / 'SHA256SUMS').write_text(''.join(
        hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n'
        for path in sorted(directory.iterdir()) if path.is_file() and path.name != 'SHA256SUMS'), encoding='utf-8')
    return value


if __name__ == '__main__':
    command, directory = sys.argv[1], Path(sys.argv[2])
    if command == 'resume':
        print(json.dumps(latest(directory)))
    elif command == 'finish':
        result = finish(directory, int(sys.argv[3]))
        print(json.dumps(result))
        raise SystemExit(result['exit_code'])
    else:
        raise SystemExit('expected resume or finish')
