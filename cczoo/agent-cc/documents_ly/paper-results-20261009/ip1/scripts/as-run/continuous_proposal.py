"""Typed, independently recoverable E4 input; separate from receiver fact frames.

The digest is an observation identifier, not an authentication credential. Values
remain in protected fixtures/archives; public tool receipts contain hashes only.
"""
import hashlib
import json
import re

from fact_protocol import encode_fact, facts_in

SCHEMA = 'argus.proposal.v1'
UNITS = {'budget_cents': 'cent', 'max_walk_minutes': 'minute',
         'max_travel_minutes': 'minute', 'min_indoor_stops': 'count'}
FIELDS = {'schema', 'work_item_id', 'fact_id', 'step_index', 'constraint_key', 'unit', 'value'}
PATTERN = re.compile(r'ARGUS_PROPOSAL_V1\s*(\{[^{}]{1,2048}\})\s*END_ARGUS_PROPOSAL')


def validate(value):
    if (not isinstance(value, dict) or set(value) != FIELDS or value.get('schema') != SCHEMA
            or not re.fullmatch(r'trip-[a-f0-9]{24}', str(value.get('work_item_id', '')))
            or not re.fullmatch(r'[a-f0-9]{32}', str(value.get('fact_id', '')))
            or type(value.get('step_index')) is not int or not 0 <= value['step_index'] < 6
            or value.get('constraint_key') not in UNITS or value.get('unit') != UNITS[value['constraint_key']]
            or type(value.get('value')) is not int or not 0 <= value['value'] < 100000000):
        raise ValueError('invalid typed business proposal')
    return value


def canonical(value):
    return json.dumps(validate(value), sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def proposal_hash(value):
    return hashlib.sha256(canonical(value).encode('ascii')).hexdigest()


def encode(value):
    return 'ARGUS_PROPOSAL_V1\n' + canonical(value) + '\nEND_ARGUS_PROPOSAL'


def proposals_in(text):
    values = []
    for match in PATTERN.finditer(text):
        try:
            # Duplicate keys cannot silently overwrite an original input.
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError('duplicate proposal field')
                    result[key] = value
                return result
            values.append(validate(json.loads(match[1], object_pairs_hook=unique)))
        except (ValueError, TypeError):
            continue
    return values


def record_updates(text):
    """Reconstruct business inputs from stored text, never grader-only metadata."""
    frames = {fact['fact_id']: fact for fact in facts_in(text)}
    result = []
    for proposal in proposals_in(text):
        frame = frames.get(proposal['fact_id'])
        if frame and frame['amount_cents'] == proposal['value']:
            result.append({**frame, 'frame': encode_fact(frame), 'proposal': proposal, 'work_item_id': proposal['work_item_id'],
                           'index': proposal['step_index'], 'constraint_key': proposal['constraint_key']})
    return result
