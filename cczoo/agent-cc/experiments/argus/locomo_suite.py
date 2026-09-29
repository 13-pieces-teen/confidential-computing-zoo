"""Bind provisioned LoCoMo runs to the existing paired experiment runner.

Normal one-condition LoCoMo still uses suite.py's fleet generation. Paired
fault/control runs reference explicitly provisioned users and Gateway configs;
this helper neither creates deployments nor adds another scheduling service.
"""
from pathlib import Path

from common import atomic, digest, read, require, resolve
from locomo_run import configuration
from runner import plan, validate
from variants import inspect_variant, SHORT


def generate(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    c = read(source)
    require(not output.exists(), 'suite output must be new')
    groups = c.get('groups', ['full_argus', 'native_spire_guarded'])
    require(groups and len(set(groups)) == len(groups) and set(groups) <= {'full_argus', 'native_spire_guarded'},
            'paired LoCoMo uses Full/native')
    conditions = c.get('conditions', ['no_fault', 'fault'])
    require(conditions and len(set(conditions)) == len(conditions) and set(conditions) <= {'fault', 'no_fault'},
            'invalid LoCoMo conditions')
    seeds = c.get('seeds', [0])
    fault_kind = c.get('fault_kind', 'helper-freeze')
    require(fault_kind in ('helper-freeze', 'helper-crash', 'target-exit'), 'select one existing service fault')
    require(c.get('fault_scope', 'shared_service') == 'shared_service', 'LoCoMo controls target the shared service')
    arms, artifacts, inputs, counts, protocols, users = {}, [], {}, {}, {}, set()
    phase_counts = {}
    scale = None
    for group in groups:
        variant = resolve(source.parent, c['variant_outputs'][group])
        require(inspect_variant(variant)['variant'] == group, 'wrong server variant')
        server = read(variant / 'environment.json')
        suffix = '/experiment/' + c['experiment_id'] + '/' + SHORT[group]
        arms[group] = {'identity_namespace': suffix, 'registration_namespace': suffix,
                       'state_dir': server['paths']['records_dir'], 'variant_output': str(variant),
                       'artifacts': [str(variant / 'variant.json')]}
        artifacts.extend(arms[group]['artifacts'])
        for seed in seeds:
            for condition in conditions:
                path = resolve(source.parent, c['locomo_configs'][group][str(seed)][condition])
                lc, fixture, bindings, _ = configuration(path)
                require(lc.get('condition', 'no_fault') == condition, 'LoCoMo condition differs from suite')
                require(lc.get('schedule'), 'paired LoCoMo requires a fixed QA schedule')
                require(lc.get('concurrent_clients') is True, 'paired LoCoMo uses independent concurrent clients')
                require(set(lc.get('controls', {})) == {'fault', 'recovery'}, 'paired conditions need the same control times')
                scale = len(bindings) if scale is None else scale
                require(scale > 0 and len(bindings) == scale, 'paired runs need equal client counts')
                for binding in bindings.values():
                    require(binding['server_spiffe_id'] == server['identity']['target_id']
                            and binding['client_spiffe_id'] in server['identity']['allowed_client_ids'],
                            'LoCoMo identity differs from server allowlist')
                    scope = (binding['account_id'], binding['user_id'])
                    require(scope not in users, 'each run needs fresh private users, including no-fault controls')
                    users.add(scope)
                protocol = digest({'fixture': fixture, 'schedule': lc['schedule'],
                                   'poll_attempts': lc.get('poll_attempts', 120), 'poll_seconds': lc.get('poll_seconds', 2),
                                   'qa_timeout_seconds': lc.get('qa_timeout_seconds', 180),
                                   'control_times': {k: {'at_s': v['at_s'], 'timeout_s': v.get('timeout_s', 60)} for k, v in lc['controls'].items()}})
                require(seed not in protocols or protocols[seed] == protocol,
                        'paired runs must use identical fixture, budgets and event times')
                protocols[seed] = protocol
                key = f'{group}:{scale}:{seed}:{condition}'
                inputs[key], counts[key] = str(path), len(fixture['tasks'])
                phases = {'normal': 0, 'fault': 0, 'recovery': 0}
                for sample in bindings:
                    tasks = [t for t in fixture['tasks'] if str(t['source_sample']) == sample]
                    for index in range(len(tasks)):
                        at = index * lc['schedule']['release_interval_s']
                        phase = ('normal' if at < lc['controls']['fault']['at_s'] else
                                 'fault' if at < lc['controls']['recovery']['at_s'] else 'recovery')
                        phases[phase] += 1
                phase_counts[key] = phases
                artifacts += [str(path), str(resolve(path.parent, lc['fixture']))]
    budget = c.get('locomo_timeout_s', 21600)
    require(type(budget) is int and 60 < budget <= 86400, 'bounded LoCoMo run timeout required')
    tool = str(Path(__file__).with_name('step.py'))
    operation = {'id': 'locomo', 'role': 'client', 'kind': 'mutation', 'timeout_s': budget,
                 'argv': ['python3', tool, 'locomo', 'run', '--config', '{locomo_config}', '--output', '{run_dir}', '--timeout', str(budget - 30)],
                 'resume_argv': ['python3', tool, 'locomo', 'resume', '--config', '{locomo_config}', '--output', '{run_dir}', '--timeout', str(budget - 30)],
                 'operation_id_file': 'locomo/state.json', 'result': 'step-result.json', 'verdict_field': 'result'}
    m = {'schema': 'argus.experiment.v1', 'experiment_id': c['experiment_id'], 'groups': groups,
         'seeds': seeds, 'scales': [scale], 'scope': 'private_user_memories', 'arms': arms,
         'policy': {'can_reattest': False, 'source': 'unchanged approved server artifact'},
         'artifacts': sorted(set(artifacts)), 'secrets': {}, 'locomo_inputs': inputs, 'locomo_task_counts': counts,
         'locomo_protocols': {str(k): v for k, v in protocols.items()}, 'locomo_phase_counts': phase_counts,
         'cases': [{'name': 'locomo', 'experiment': 'E4', 'conditions': conditions, 'fault_kind': fault_kind,
                    'fault_scope': 'shared_service', 'operations': [operation]}]}
    validate(m)
    output.mkdir(parents=True)
    atomic(output / 'suite.json', m)
    atomic(output / 'run-order.json', {'runs': plan(m), 'remote_acceptance': 'NOT_RUN',
                                      'note': "Deploy each run's declared Gateway/user config before executing its run ID."})
    return m
