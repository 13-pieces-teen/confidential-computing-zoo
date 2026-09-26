import json
from pathlib import Path
import sys
import copy
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import suite
import variants
from common import atomic, read
from runner import prepare, safe_get_measurement


def test_generated_fleets_have_disjoint_ids_and_read_current_generations(tmp_path):
    client_root = suite.ROOT/'adapters/OpenClaw/spiffe_client'
    fleet = read(client_root/'config/fleet.example.json')
    fleet['node'].update(server_address='10.0.2.2',node_certificate_sha1='a'*40,helper_sha256='b'*64)
    for client in fleet['instances']:
        client.update(openclaw_image='registry.example/oc@sha256:'+'c'*64,image_config_digest='sha256:'+'d'*64)
    atomic(tmp_path/'fleet.json',fleet)
    server = read(variants.WORKLOAD/'config/environment.example.json')
    server['approved_policy_artifact'] = {'path':'/etc/approved.rego','sha256':'a'*64}
    server['identity'].pop('client_id',None)
    server['identity']['allowed_client_ids'] = [v['client_spiffe_id'] for v in fleet['instances']]
    groups = ['full_argus','native_spire_guarded']
    config = {'experiment_id':'paired','groups':groups,'cases':['private-memory','steady-api'],'fleet_config':'fleet.json','load':{'frozen':True,'rate':10},
              'variant_outputs':{},'business_configs':{}}
    for group in groups:
        variants.render_variant(server,group,tmp_path/group,experiment_id='paired')
        config['variant_outputs'][group] = str(tmp_path/group)
        business = read(client_root/'config/fleet-business.example.json')
        for item in business['instances']:
            item['user_id'] += '-' + group
            item['api_key_file'] = '/etc/experiment/'+group+'/'+item['name']+'/key'
        business_path = tmp_path/(group+'-business.json'); atomic(business_path,business)
        config['business_configs'][group] = str(business_path)
    atomic(tmp_path/'config.json',config)
    manifest = suite.generate(tmp_path/'config.json',tmp_path/'generated')
    assert manifest['cases'][0]['scales'] == [3]
    names, containers, identities, gids, uids = set(),set(),set(),set(),set()
    for group in groups:
        generated = read(tmp_path/'generated'/group/'fleet.json')
        assert generated['node']['server_spiffe_id'].endswith('/experiment/paired/'+variants.SHORT[group])
        for client in generated['instances']:
            assert client['name'] not in names; names.add(client['name'])
            assert client['container_name'] not in containers; containers.add(client['container_name'])
            assert client['client_spiffe_id'] not in identities; identities.add(client['client_spiffe_id'])
            assert client['gateway_uid'] not in uids; uids.add(client['gateway_uid'])
            for key in ('gateway_gid','reader_gid'):
                assert client[key] not in gids; gids.add(client[key])
        load = read(tmp_path/'generated'/group/'load.json')
        for item in load['instances']:
            assert item['credentials_dir'].endswith('/'+item['name']+'/credentials')
            assert not any(key in item for key in ('cert','key','bundle'))
            assert item['client_id'] in identities
    functional = dict(config)
    for key in ('groups', 'cases', 'load'):
        functional.pop(key)
    atomic(tmp_path/'functional.json', functional)
    smoke = suite.generate(tmp_path/'functional.json', tmp_path/'functional')
    assert smoke['groups'] == ['full_argus'] and smoke['seeds'] == [0]
    assert [case['name'] for case in smoke['cases']] == ['private-memory']
    assert 'load' not in smoke
    assert not (tmp_path/'functional/full_argus/load.json').exists()
    assert not (tmp_path/'functional/native_spire_guarded').exists()


def locomo_sources(tmp_path, groups=('full_argus', 'native_spire_guarded')):
    client_root = suite.ROOT / 'adapters/OpenClaw/spiffe_client'
    f = read(client_root / 'config/fleet.example.json')
    f['node'].update(server_address='10.0.2.2', node_certificate_sha1='a'*40, helper_sha256='b'*64)
    for client in f['instances']:
        client.update(openclaw_image='registry.example/oc@sha256:'+'c'*64, image_config_digest='sha256:'+'d'*64)
    atomic(tmp_path / 'fleet.json', f)
    fixture = {'schema': 'argus.locomo-derived.v1', 'source_sha256': 'a'*64, 'tasks': []}
    for item in f['instances']:
        fixture['tasks'].append({'task_id': item['name']+'-q1', 'source_sample': item['name'], 'category': 4,
            'sessions': [{'source_session': 'session_1', 'messages': [{'speaker': 'A', 'text': 'A visited a museum.'}]}],
            'question': 'Where did A visit?', 'reference_answer': 'museum'})
    atomic(tmp_path / 'fixture.json', fixture)
    server = read(variants.WORKLOAD / 'config/environment.example.json')
    server['approved_policy_artifact'] = {'path': '/etc/approved.rego', 'sha256': 'a'*64}
    server['identity'].pop('client_id', None)
    server['identity']['allowed_client_ids'] = [v['client_spiffe_id'] for v in f['instances']]
    result = {'experiment_id': 'locomo01', 'groups': list(groups), 'cases': ['locomo'], 'fleet_config': 'fleet.json',
              'variant_outputs': {}, 'business_configs': {}, 'locomo_configs': {}, 'locomo_timeout_s': 3600}
    ids = [item[k] for item in f['instances'] for k in ('gateway_uid', 'gateway_gid', 'reader_gid')]
    stride = max(ids) - min(ids) + 1
    for index, group in enumerate(groups):
        variants.render_variant(server, group, tmp_path / group, experiment_id='locomo01')
        result['variant_outputs'][group] = str(tmp_path / group)
        deployed = read(tmp_path / group / 'environment.json')
        business = read(client_root / 'config/fleet-business.example.json')
        for item in business['instances']:
            item['user_id'] += '-' + group
            item['api_key_file'] = '/etc/experiment/' + group + '/' + item['name'] + '/key'
        business_file = tmp_path / (group + '-business.json'); atomic(business_file, business)
        result['business_configs'][group] = str(business_file)
        bindings = []
        for item, user in zip(f['instances'], business['instances']):
            bindings.append({'source_sample': item['name'], 'container': 'locomo01-' + variants.SHORT[group] + '-' + item['name'],
                'docker_user': str(item['gateway_uid']+index*stride) + ':' + str(item['gateway_gid']+index*stride),
                'config_path': '/home/node/.openclaw/openclaw.json', 'agent_id': user.get('agent_id', 'main'),
                'account_id': user['account_id'], 'user_id': user['user_id'],
                'client_spiffe_id': item['client_spiffe_id'] + '/experiment/locomo01/' + variants.SHORT[group],
                'server_spiffe_id': deployed['identity']['target_id']})
        locomo_file = tmp_path / (group + '-locomo.json')
        atomic(locomo_file, {'schema': 'argus.locomo-run.v1', 'fixture': 'fixture.json', 'bindings': bindings})
        result['locomo_configs'][group] = str(locomo_file)
    atomic(tmp_path / 'suite-config.json', result)
    return result


def test_locomo_frozen_suite_prepares_fixture_and_has_resumable_operation(tmp_path):
    locomo_sources(tmp_path)
    m = suite.generate(tmp_path / 'suite-config.json', tmp_path / 'generated')
    assert [case['name'] for case in m['cases']] == ['locomo']
    op = m['cases'][0]['operations'][0]
    assert op['operation_id_file'] == 'locomo/state.json'
    assert op['timeout_s'] > float(op['argv'][-1])
    prepared = prepare(tmp_path / 'generated/suite.json', tmp_path / 'prepared')
    assert len(prepared['runs']) == 2
    assert str(tmp_path / 'fixture.json') in prepared['artifacts']
    for arm in m['arms'].values():
        assert 'load_config' not in arm
        assert Path(read(arm['locomo_config'])['fixture']).is_absolute()


@pytest.mark.parametrize('change', ['agent', 'config_path', 'fixture', 'budget', 'multiple_seeds', 'business_mixed'])
def test_locomo_rejects_confounded_protocol_or_wrong_gateway(tmp_path, change):
    cfg = locomo_sources(tmp_path)
    native_file = Path(cfg['locomo_configs']['native_spire_guarded'])
    lc = read(native_file)
    if change == 'agent': lc['bindings'][0]['agent_id'] = 'other'
    if change == 'config_path': lc['bindings'][0]['config_path'] = '/tmp/not-running.json'
    if change == 'fixture':
        fixture = read(tmp_path / 'fixture.json'); fixture['tasks'][0]['question'] += ' changed'
        atomic(tmp_path / 'changed.json', fixture); lc['fixture'] = 'changed.json'
    if change == 'budget': lc['qa_timeout_seconds'] = 360
    if change == 'multiple_seeds': cfg['seeds'] = [0, 1]
    if change == 'business_mixed': cfg['cases'] = ['locomo', 'private-memory']
    atomic(native_file, lc); atomic(tmp_path / 'suite-config.json', cfg)
    with pytest.raises(ValueError):
        suite.generate(tmp_path / 'suite-config.json', tmp_path / 'generated')


def test_memory_post_body_is_resolved_hashed_and_not_safe_to_replay(tmp_path):
    cfg = locomo_sources(tmp_path, ('full_argus',))
    cfg.update(cases=['steady-api'], load={'frozen': True, 'rate': 1, 'path': '/api/v1/search/find',
                                         'body_file': 'query.json', 'connection_mode': 'reuse'})
    atomic(tmp_path / 'query.json', {'query': 'a fixed recall question', 'limit': 5})
    atomic(tmp_path / 'suite-config.json', cfg)
    m = suite.generate(tmp_path / 'suite-config.json', tmp_path / 'generated')
    op = m['cases'][0]['operations'][0]
    assert 'safe_retry' not in op
    arm = m['arms']['full_argus']
    assert not safe_get_measurement(op, arm, tmp_path)
    assert all(item['body_file'] == str(tmp_path / 'query.json') for item in read(arm['load_config'])['instances'])
    prepared = prepare(tmp_path / 'generated/suite.json', tmp_path / 'prepared')
    assert str(tmp_path / 'query.json') in prepared['artifacts']
