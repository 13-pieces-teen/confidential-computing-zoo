"""Join E4 timing and legal continuation without inventing missing evidence."""
from datetime import datetime
import math


def timestamp_ms(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return round(parsed.timestamp() * 1000) if parsed.tzinfo is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


def timed_phase(stamp, condition, fault_at, recovery_at, uncertainty):
    if type(stamp) is not int:
        return 'NOT_OBSERVED'
    if condition == 'no_fault':
        return 'no_fault'
    if (type(uncertainty) not in (int, float) or not math.isfinite(uncertainty) or uncertainty < 0
            or type(fault_at) is not int):
        return 'UNKNOWN'
    margin = 2 * uncertainty
    if stamp + margin < fault_at:
        return 'before_fault'
    if stamp - margin <= fault_at:
        return 'boundary'
    if type(recovery_at) is int:
        if stamp - margin >= recovery_at:
            return 'after_recovery_command'
        if stamp + margin >= recovery_at:
            return 'boundary'
    return 'fault_period'


def annotate_phases(steps, value, condition_evidence, uncertainty):
    fault_at = condition_evidence.get('fault_started_at_ms')
    recovery_at = value.get('controls', {}).get('recovery', {}).get('started_at_ms')
    rows = []
    for step in steps:
        phase = lambda at: timed_phase(at, value.get('condition'), fault_at, recovery_at, uncertainty)
        events = []
        for event in step.get('tool_events', []):
            stamp = timestamp_ms(event.get('checked_at'))
            events.append({**event, 'at_ms':stamp, 'actual_phase':phase(stamp)})
        rows.append({**step, 'planned_phase':step.get('phase'),
                     'actual_dispatch_phase':phase(step.get('released_at_ms')), 'tool_events':events,
                     'actual_tool_phases':sorted({e['actual_phase'] for e in events if e.get('event') == 'tool_started'}),
                     'phase_clock_uncertainty_ms':uncertainty,
                     'phase_scope':'actual fault checkpoint / recovery command; not admission or service readiness'})
    return rows


def join_work_items(value, receipt, condition_evidence):
    """Read independently assessed admission/read evidence for this run.

    A correct recovered step does not turn missing required proposals into full
    task success. Per-item first continuation and final outcome stay separate.
    Treat the clock bound conservatively as a per-endpoint error: ordering
    across sources requires a 2u gap, as do the reported interval bounds.
    """
    recovery = receipt.get('recovery') or {}
    milestones = {m.get('stage'):m for m in recovery.get('milestones', [])}
    admission = milestones.get('admission_completed', {})
    anchor, uncertainty = recovery.get('anchor_at_ms'), recovery.get('clock_uncertainty_ms')
    facts = {f['fact_id']:f for f in receipt.get('facts', [])}
    results = []
    for work_item in value.get('work_items', []):
        joined = {**work_item, 'legal_recovery_result':'UNKNOWN',
                  'legal_recovery_reason':'independent admission/read/task association incomplete',
                  'recovery_intervals':{}, 'legal_recovery_evidence':{}}
        if value.get('condition') == 'no_fault':
            joined.update(legal_recovery_result='NOT_RUN',legal_recovery_reason='no-fault control')
            results.append(joined); continue
        if (not value.get('run_id') or receipt.get('run_id') != value['run_id']
                or condition_evidence.get('result') != 'OBSERVED' or admission.get('status') != 'OBSERVED'
                or not admission.get('source_sha256') or type(admission.get('at_ms')) is not int
                or type(anchor) is not int or type(uncertainty) not in (int, float)
                or not math.isfinite(uncertainty) or uncertainty < 0):
            results.append(joined); continue
        margin = 2 * uncertainty
        candidates, legal_reads = [], []
        for step in value.get('steps', []):
            if step.get('work_item_id') != work_item['work_item_id'] or step.get('client_id') != work_item['client_id']:
                continue
            fact = facts.get(step['fact_id'], {})
            if fact.get('client_id') != work_item['client_id'] or fact.get('step_id') != step['step_id']:
                continue
            reads = [r for r in fact.get('reads', []) if r.get('result') == 'PASS' and r.get('phase') == 'recovery'
                     and r.get('admission_source_sha256') == admission['source_sha256']
                     and r.get('instance_id') and r.get('request_id')
                     and r['request_id'] in (step.get('request_ids') or []) and type(r.get('at_ms')) is int
                     and r['at_ms'] - margin >= admission['at_ms'] and r['at_ms'] - margin >= anchor]
            legal_reads.extend(reads)
            confirmed_at = step.get('goal_confirmed_at_ms')
            answer_at = step.get('answer_at_ms')
            if (step.get('task_result') == 'PASS' and step.get('committed') is True
                    and type(confirmed_at) is int and type(answer_at) is int and confirmed_at >= answer_at and reads):
                # Late archive confirmation cannot turn a pre-recovery answer
                # into a new continuation. The answer itself must follow a
                # legal application read attributable to this exact step.
                eligible = [r for r in reads if answer_at - margin >= r['at_ms']]
                if eligible:
                    candidates.append((step, min(eligible,key=lambda r:r['at_ms'])))
        if candidates:
            step, observation = min(candidates,key=lambda pair:pair[0]['goal_confirmed_at_ms'])
            first_read = min(legal_reads,key=lambda r:r['at_ms'])
            def interval(start, end):
                return {'lower_ms':max(0,end-start-2*uncertainty),'upper_ms':end-start+2*uncertainty}
            joined.update(legal_recovery_result='PASS', legal_recovery_reason='correct continuation linked to recovered admission and application read',
                legal_recovery_evidence={'admission_source_sha256':admission['source_sha256'],
                    'instance_id':observation['instance_id'],'request_id':observation['request_id'],
                    'step_id':step['step_id'],'fact_id':step['fact_id'],
                    'answer_at_ms':step['answer_at_ms'],
                    'first_legal_read_at_ms':first_read['at_ms'],'correct_continuation_at_ms':step['goal_confirmed_at_ms'],
                    'timing_scope':'answer after request-linked legal read; conservative confirmation includes archive observation latency',
                    'clock_ordering_scope':'2u per-endpoint conservative ordering'},
                recovery_intervals={'admission_to_first_legal_read_ms':interval(admission['at_ms'],first_read['at_ms']),
                    'first_legal_read_to_correct_continuation_ms':interval(first_read['at_ms'],step['goal_confirmed_at_ms']),
                    'recovery_command_to_correct_continuation_ms':interval(anchor,step['goal_confirmed_at_ms'])})
        results.append(joined)
    return results
