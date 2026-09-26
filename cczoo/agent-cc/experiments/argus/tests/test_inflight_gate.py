"""Fault injection needs an actual read while the slow request is still active."""
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fault_trial


def test_actual_read_gate_rejects_wrong_scope_and_finished_request(tmp_path):
    config = {'run_id':'run', 'server_receiver_package':'/installed', 'collector_control':'/control'}
    trace = tmp_path / 'trace.jsonl'
    start = {'type':'stream_start', 'run_id':'run', 'request_id':'slow'}
    trace.write_text(json.dumps(start) + '\n')
    observed = {'request_id':'slow', 'phase':'body_read', 'received_body_bytes':4,
                'provenance':'kernel_process_and_deployment'}
    status = {'run_id':'run', 'first_read':observed}

    def status_response(*args, **kwargs):
        kwargs['output'].write_text(json.dumps(status))

    with patch.object(fault_trial, 'remote', side_effect=status_response) as query:
        assert fault_trial.inflight_first_read(config, tmp_path) == observed
        status['run_id'] = 'other'
        assert fault_trial.inflight_first_read(config, tmp_path) is None
        status['run_id'] = 'run'
        observed['received_body_bytes'] = 0
        assert fault_trial.inflight_first_read(config, tmp_path) is None
        observed['received_body_bytes'] = 4
        trace.write_text(json.dumps(start) + '\n' + json.dumps({'type':'request', 'request_id':'slow'}) + '\n')
        query.reset_mock()
        assert fault_trial.inflight_first_read(config, tmp_path) is None
        query.assert_not_called()
