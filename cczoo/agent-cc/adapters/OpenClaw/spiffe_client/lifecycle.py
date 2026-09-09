#!/usr/bin/env python3
"""Check a continuous real-client trace. Faults are performed by the operator.

mark is a timestamp aid, not proof that an injected fault actually occurred.
Keep the operator command output and systemd journal alongside the trace.
"""
import argparse
import json
from pathlib import Path
import time


def verify(rows, mode, events):
    if len(rows) < 10:
        raise ValueError('at least ten continuous samples required')
    for before, after in zip(rows, rows[1:]):
        if not 0 < after['started_at_ms'] - before['started_at_ms'] <= 2000:
            raise ValueError('observation gap exceeds 2 seconds or timestamps moved backwards')
    if mode == 'rotation':
        serials = {(r.get('client_serial'), r.get('server_serial')) for r in rows}
        if any(not r['ok'] or not r['ready'] for r in rows) or len(serials) < 2:
            raise ValueError('rotation requires changed serial and uninterrupted readiness/business reads')
        return {'result': 'PASS', 'samples': len(rows), 'serial_pairs': sorted(serials)}
    if set(events) != {'fault_at_ms', 'recover_at_ms'} or events['recover_at_ms'] <= events['fault_at_ms']:
        raise ValueError('fault and recovery markers are required in order')
    bound = {'publisher': 3500, 'broker': 12000, 'network': 3500}[mode]
    first, last = events['fault_at_ms'], events['recover_at_ms']
    baseline = [r for r in rows if r['completed_at_ms'] < first]
    outage = [r for r in rows if first + bound <= r['started_at_ms'] and r['completed_at_ms'] < last]
    recovered = [r for r in rows if r['started_at_ms'] >= last]
    if len(baseline) < 3 or any(not r['ok'] or not r['ready'] for r in baseline):
        raise ValueError('missing healthy baseline')
    if len(outage) < 3 or any(r['ok'] or (mode != 'network' and r['ready']) for r in outage):
        raise ValueError('fault did not fail closed continuously within the observation bound')
    # Allow recovery up to 65s after operator intervention; require three final good reads.
    good = [r for r in recovered if r['ok'] and r['ready']]
    if not good or good[0]['completed_at_ms'] > last + 65000 or not all(r['ok'] and r['ready'] for r in recovered[-3:]) or len(recovered) < 3:
        raise ValueError('recovery was not demonstrated')
    failures = [r for r in rows if r['started_at_ms'] >= first and not r['ok']]
    return {'result': 'PASS', 'mode': mode, 'bound_ms': bound, 'samples': len(rows),
            'first_failure_observed_ms': failures[0]['completed_at_ms'] - first,
            'recovery_observed_ms': good[0]['completed_at_ms'] - last}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['mark', 'check'])
    p.add_argument('--events', type=Path)
    p.add_argument('--event', choices=['fault', 'recover'])
    p.add_argument('--trace', type=Path)
    p.add_argument('--mode', choices=['rotation', 'publisher', 'broker', 'network'], default='rotation')
    a = p.parse_args()
    if a.action == 'mark':
        if not a.events or not a.event:
            p.error('mark requires --events and --event')
        value = json.loads(a.events.read_text()) if a.events.exists() else {}
        key = a.event + '_at_ms'
        if key in value:
            raise ValueError('event already marked; use a new evidence file')
        value[key] = time.time_ns() // 1000000
        a.events.write_text(json.dumps(value, indent=2) + '\n')
    else:
        if not a.trace:
            p.error('check requires --trace')
        rows = [json.loads(line) for line in a.trace.read_text().splitlines() if line.strip()]
        value = verify(rows, a.mode, json.loads(a.events.read_text()) if a.events else {})
    print(json.dumps(value, indent=2))


if __name__ == '__main__':
    main()
