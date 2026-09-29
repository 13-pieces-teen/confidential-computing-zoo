import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner import fault_ready


def trace(path, **changes):
    rows = [dict(type='request', run_id='trial', lane=lane, request_id=f'{lane}-{i}',
                 ok=True, tls_connections=1) for lane in ('existing', 'new') for i in range(3)]
    rows[-1].update(changes)
    path.write_text('\n'.join(json.dumps(row) for row in rows))
    return rows


def test_transport_ready_needs_no_model_or_memory_milestone(tmp_path):
    path = tmp_path / 'trace.jsonl'; trace(path)
    fault_ready(path, None, 'trial', readiness='transport')
    with pytest.raises(ValueError, match='milestone'):
        fault_ready(path, None, 'trial')


@pytest.mark.parametrize('change', [{'ok': False}, {'request_id': 'new-0'}, {'run_id': 'other'}])
def test_transport_ready_keeps_actual_lane_requirements(tmp_path, change):
    path = tmp_path / 'trace.jsonl'; trace(path, **change)
    with pytest.raises(ValueError):
        fault_ready(path, None, 'trial', readiness='transport')


def test_reconnected_existing_lane_is_not_a_reuse_baseline(tmp_path):
    path = tmp_path / 'trace.jsonl'; rows = trace(path)
    rows[1]['tls_connections'] = 2
    path.write_text('\n'.join(json.dumps(row) for row in rows))
    with pytest.raises(ValueError, match='reconnected'):
        fault_ready(path, None, 'trial', readiness='transport')
