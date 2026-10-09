"""Six itinerary proposals in one persistent business work item.

Pure fixture/decision functions only. The existing continuous runner owns I/O,
submission journals, controls and independent receiver observations.
"""
import hashlib
import hmac
import json
import random
import uuid

from fact_protocol import encode_fact
from continuous_proposal import SCHEMA, UNITS, encode as encode_proposal, proposal_hash, validate

SCENARIOS = {'ledger-v1': 6, 'work-item-v1': 2}  # steps per phase


def decision(policy, updates):
    indexes = [validate(update['proposal'])['step_index'] for update in updates]
    if indexes != sorted(set(indexes)):
        raise ValueError('proposal order or uniqueness differs')
    constraints = dict(policy['initial_constraints'])
    for update in updates:
        proposal = validate(update['proposal'])
        if proposal['work_item_id'] != policy['work_item_id']:
            raise ValueError('proposal belongs to another work item')
        if proposal['fact_id'] != update['fact_id'] or proposal['value'] != update['amount_cents']:
            raise ValueError('proposal differs from its receipt frame')
        constraints[proposal['constraint_key']] = proposal['value']
    feasible = [route for route in policy['routes']
                if route['cost_cents'] <= constraints['budget_cents']
                and route['walk_minutes'] <= constraints['max_walk_minutes']
                and route['travel_minutes'] <= constraints['max_travel_minutes']
                and route['indoor_stops'] >= constraints['min_indoor_stops']]
    route = min(feasible, key=lambda r: (r['cost_cents'], r['route_code'])) if feasible else None
    current = updates[-1]
    return {'work_item_id': current['work_item_id'], 'fact_id': current['fact_id'],
            'event_ref': current['event_ref'],
            'previous_event_ref': updates[-2]['event_ref'] if len(updates) > 1 else None,
            'applied_fact_ids': [item['fact_id'] for item in updates], 'constraints': constraints,
            'status': 'ITINERARY_SELECTED' if route else 'NO_FEASIBLE_ITINERARY',
            'route_code': route['route_code'] if route else policy['unavailable_code'],
            'total_cents': route['cost_cents'] if route else 0,
            'itinerary': route['stops'] if route else []}


def resolve_step(step, confirmed):
    """All previously confirmed proposals, in original order, remain effective."""
    selected = sorted(confirmed, key=lambda s: s['index'])
    predecessor = selected[-1] if selected else None
    return {**step, 'predecessor': predecessor['step_id'] if predecessor else None,
            'predecessor_fact_id': predecessor['fact_id'] if predecessor else None,
            'predecessor_full_fact_sha256': hashlib.sha256(predecessor['frame'].encode()).hexdigest() if predecessor else None,
            'required_recall_fact_hashes': [hashlib.sha256(s['frame'].encode()).hexdigest() for s in selected],
            'required_recall_proposal_hashes': [proposal_hash(s['proposal']) for s in selected],
            'confirmed_predecessors': [s['step_id'] for s in selected],
            'confirmed_predecessor_fact_ids': [s['fact_id'] for s in selected],
            'expected': decision(step['policy'], [*selected, step])}


def make_fixture(c, secret_seed):
    key = str(secret_seed).encode()
    token = lambda name, length: hmac.new(key, name.encode(), hashlib.sha256).hexdigest()[:length]
    rng = random.Random(c['structure_seed'])
    rules, steps = [], []
    for binding in sorted(c['bindings'], key=lambda b: b['client_id']):
        client = binding['client_id']
        work_item = 'trip-' + token(client + '/trip', 24)
        project = token(client + '/project', 12)
        policy = {'work_item_id': work_item,
                  'initial_constraints': {'budget_cents': 30000, 'max_walk_minutes': 120,
                                          'max_travel_minutes': 240, 'min_indoor_stops': 0},
                  'unavailable_code': token(client + '/unavailable', 12),
                  'routes': [
                      {'route_code': token(client + '/coast', 12), 'cost_cents': 8000,
                       'walk_minutes': 55, 'travel_minutes': 110, 'indoor_stops': 0,
                       'stops': ['海滨步道', '公园']},
                      {'route_code': token(client + '/mixed', 12), 'cost_cents': 10000,
                       'walk_minutes': 35, 'travel_minutes': 90, 'indoor_stops': 1,
                       'stops': ['博物馆', '公园']},
                      {'route_code': token(client + '/indoor', 12), 'cost_cents': 12000,
                       'walk_minutes': 20, 'travel_minutes': 60, 'indoor_stops': 2,
                       'stops': ['博物馆', '美术馆']}]}
        generation = c.get('initialization_generation', 0)
        seed_name = client + '/trip/seed' + (f'/retry-{generation}' if generation else '')
        rule = {'client_id': client, 'project_id': project, 'work_item_id': work_item,
                'normal_code': policy['routes'][0]['route_code'],
                'review_code': policy['unavailable_code'], 'threshold_cents': 30000,
                'ov_session_id': str(uuid.UUID(token(seed_name, 32))),
                'memory_policy': {'self': {'enabled': True}, 'peer': {'enabled': True},
                                  'memory_types': ['events'], 'working_memory': {'enabled': True}},
                'initialization_query': ('Private project ' + project + ' work_item_id=' + work_item +
                                         ' 行程规则 routes initial_constraints')}
        rule['text'] = ('Private project ' + project + ': 此行程工作项按步骤顺序应用所有已确认提案；'
                        '未确认的写入不能当作已生效，也不能重发。以全部有效约束选择最低成本可行路线；'
                        '没有满足全部约束的候选时报告 NO_FEASIBLE_ITINERARY。完整保留每条 ARGUS_PROPOSAL_V1 '
                        '原始提案（包括类型、顺序、单位、值）及对应 ARGUS_FACT_V1 事实帧，Decision JSON 仅为派生判断。'
                        '后续 memory_recall 必须能回读全部原始提案，不能只保留摘要或 Decision。行程规则：' +
                        json.dumps(policy, ensure_ascii=False, sort_keys=True))
        rules.append(rule)
        constraints = [('budget_cents', rng.randint(12000, 20000)),
                       ('max_walk_minutes', rng.randint(45, 75)),
                       ('budget_cents', rng.randint(9000, 14000)),
                       ('max_walk_minutes', rng.randint(20, 45)),
                       ('max_travel_minutes', rng.randint(60, 120)),
                       ('min_indoor_stops', rng.randint(1, 2))]
        client_steps = []
        planned_constraints = dict(policy['initial_constraints'])
        for index, (constraint, value) in enumerate(constraints):
            step_id = f's{index:02d}'
            if value == planned_constraints[constraint]:
                raise ValueError(f'work-item input {client}/{step_id} leaves {constraint} unchanged; '
                                 'validate structure_seed before freezing the experiment')
            planned_constraints[constraint] = value
            fields = {'fact_id': token(f'{client}/{step_id}/id', 32), 'project_id': project,
                      'chain_id': token(client + '/trip/chain', 12),
                      'event_ref': token(f'{client}/{step_id}/ref', 32), 'amount_cents': value}
            proposal = {'schema': SCHEMA, 'work_item_id': work_item, 'fact_id': fields['fact_id'],
                        'step_index': index, 'constraint_key': constraint, 'unit': UNITS[constraint], 'value': value}
            step = {'client_id': client, 'step_id': step_id, 'task_id': step_id,
                    'work_item_id': work_item, 'phase': ('normal', 'pause', 'recovery')[index // 2],
                    'index': index, 'constraint_key': constraint, 'policy': policy,
                    'required_checkpoint': 's01' if index >= 2 else 's00' if index == 1 else None,
                    'release_offset_s': index * c['schedule']['release_interval_s'],
                    'ov_session_id': str(uuid.UUID(token(f'{client}/{step_id}/store', 32))),
                    **fields, 'frame': encode_fact(fields), 'proposal': proposal,
                    'proposal_text': encode_proposal(proposal), 'proposal_sha256': proposal_hash(proposal)}
            step = resolve_step(step, client_steps)
            client_steps.append(step)
            steps.append(step)
    return {'rules': rules, 'steps': steps}


def prompt_for(step):
    refs = ', '.join(step.get('confirmed_predecessor_fact_ids', [])) or '无，读取记忆中的初始约束'
    return ('继续同一行程工作项 work_item_id=' + step['work_item_id'] + '，按外部私有记忆中的路线规则处理一个新提案。'
            '使用真实 memory_recall 回读规则和所有已确认 ARGUS_PROPOSAL_V1 原始提案及对应完整事实帧；'
            'Decision 只能作为参考，必须按 Proposal 的 step_index 重算约束，不能沿用错误的旧 Decision。'
            '这些 fact_id 为：' + refs + '。按 constraint_key 与 unit 解释 value，核对提案与事实帧的 fact_id 和数值一致。'
            '不遗漏故障期已确认提案，不猜测未知写入；缺少类型、顺序或单位时报告 UNKNOWN。'
            'memory_recall 的查询必须包含以下当前完整事实帧；可以另行查询前驱记录。\n' + step['frame'] +
            '\n当前原始提案如下，必须逐字保存，不能改写成 Decision：\n' + step['proposal_text'] +
            '\n选择满足全部约束的最低成本行程，或报告无可行路线。'
            '使用 memory_store 恰好一次，sessionId="' + step['ov_session_id'] + '"，text 依次为当前完整事实帧、'
            '完整 ARGUS_PROPOSAL_V1 原始提案封套、Decision: 和结果 JSON，用换行分隔。'
            '返回同一 JSON，字段为 work_item_id, fact_id, event_ref, '
            'previous_event_ref, applied_fact_ids, constraints, status, route_code, total_cents, itinerary。'
            '缺少规则、任一已确认提案或写入不可用时报告 UNKNOWN，不重试 memory_store，不重放旧 POST。'
            '不要用本地文件、之前的推理会话或用户提供的答案代替外部持久记忆。')
