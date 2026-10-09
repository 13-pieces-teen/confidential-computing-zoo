#!/usr/bin/env python3
"""Create a hash-bound E4 pilot gate from runner and server evidence."""
import argparse
import json
from pathlib import Path

from common import atomic, read, require, sha


def gate_receipt(result_path, server_path):
    result_path, server_path = Path(result_path), Path(server_path)
    result, server = read(result_path), read(server_path)
    require(result.get('schema') == 'argus.continuous-result.v1'
            and result.get('scenario') == 'work-item-v1', 'work-item continuous result required')
    require(server.get('schema') == 'argus.e4-server-receipt.v1', 'server receipt required')
    require((server.get('run_id'),server.get('operation_id')) ==
            (result.get('run_id'),result.get('operation_id')), 'server receipt belongs to another operation')
    require(type(server.get('request_count')) is int and server['request_count'] >= 0
            and isinstance(server.get('receiver_sha256'),str)
            and isinstance(server.get('correlation_sha256'),str)
            and isinstance(server.get('matched_request_ids'),list)
            and len(server['matched_request_ids']) == len(set(server['matched_request_ids']))
            and all(isinstance(value,str) and value for value in server['matched_request_ids']),
            'server evidence binding missing')
    condition = result.get('condition')
    require(condition in ('no_fault','fault'), 'unsupported pilot condition')
    items = result.get('work_items') or []
    require(result.get('planned_tasks') == 6 and len(result.get('steps') or []) == 6
            and len(items) == 1, 'pilot must contain one six-step work item')
    item, gates = items[0], result.get('gates') or {}
    request_ids = sorted({request_id for step in result['steps']
                          for request_id in step.get('request_ids') or []})
    transport_linked = (bool(request_ids)
                        and all(step.get('task_result') == 'PASS'
                                and step.get('request_ids')
                                and step.get('transport_evidence')
                                and step.get('tool_events') for step in result['steps']))
    receiver_linked = (transport_linked and set(request_ids) <= set(server['matched_request_ids']))
    receipt = {
        'schema':('argus.healthy-pilot-gate.v1' if condition == 'no_fault'
                  else 'argus.fault-pilot-gate.v1'),
        'condition':condition,
        'run_id':result['run_id'],
        'operation_id':result['operation_id'],
        'planned_steps':6,
        'window_completion':gates.get('window_completion','UNKNOWN'),
        'service_recovery':('NOT_APPLICABLE' if condition == 'no_fault'
                            else 'PASS' if gates.get('service_recovery') == 'PASS'
                            and server.get('legal_recovery_result') == 'PASS' else 'UNKNOWN'),
        'task_correctness':('PASS' if gates.get('task_correctness') == 'PASS'
                            and item.get('complete_task_result') == 'PASS'
                            and transport_linked else
                            'FAIL' if 'FAIL' in (gates.get('task_correctness'),
                                               item.get('complete_task_result')) else 'UNKNOWN'),
        'receiver_coverage':('PASS' if server.get('receiver_coverage') == 'PASS'
                             and server['request_count'] > 0 and receiver_linked else 'UNKNOWN'),
        'runner_result_sha256':sha(result_path),
        'server_receipt_sha256':sha(server_path),
        'receiver_sha256':server['receiver_sha256'],
        'correlation_sha256':server['correlation_sha256']
    }
    receipt['request_ids'] = request_ids
    if condition == 'fault':
        controls = result.get('controls') or {}
        fault, recovery = controls.get('fault') or {}, controls.get('recovery') or {}
        steps = result.get('steps') or []
        pre_fault = [step for step in steps if step.get('committed') is True
                     and type(step.get('completed_at_ms')) is int
                     and type(fault.get('started_at_ms')) is int
                     and step['completed_at_ms'] <= fault['started_at_ms']]
        post_fault = [step for step in steps if step.get('attempted') is True and step.get('request_ids')
                      and type(step.get('dispatch_started_at_ms')) is int
                      and type(fault.get('completed_at_ms')) is int
                      and type(recovery.get('started_at_ms')) is int
                      and fault['completed_at_ms'] <= step['dispatch_started_at_ms'] < recovery['started_at_ms']]
        unresolved = item.get('unresolved_proposals') or []
        receipt.update(
            pre_fault_confirmed_state='PASS' if pre_fault else 'UNKNOWN',
            post_fault_attempt='PASS' if post_fault else 'UNKNOWN',
            legal_recovery='PASS' if server.get('legal_recovery_result') == 'PASS' else 'UNKNOWN',
            correct_continuation='PASS' if item.get('continuation_result') == 'PASS'
                                 and not unresolved else 'UNKNOWN',
            unresolved_proposals=unresolved)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result',required=True)
    parser.add_argument('--server-receipt',required=True)
    parser.add_argument('--output',required=True)
    args = parser.parse_args()
    output = Path(args.output)
    require(not output.exists(), 'fresh gate output required')
    receipt = gate_receipt(args.result,args.server_receipt)
    atomic(output,receipt)
    required = ['window_completion','task_correctness','receiver_coverage']
    if receipt['condition'] == 'fault':
        required += ['service_recovery','pre_fault_confirmed_state','post_fault_attempt',
                     'legal_recovery','correct_continuation']
    return 0 if all(receipt.get(name) == 'PASS' for name in required) else 1


if __name__ == '__main__':
    raise SystemExit(main())
