import copy
from contextlib import nullcontext, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import deploy
import fleet
import fleet_business as business


def fixture():
    c = json.loads((ROOT / 'config/fleet.example.json').read_text())
    c['node'].update(server_address='10.0.2.2', node_certificate_sha1='a'*40, helper_sha256='b'*64)
    for item in c['instances']:
        item.update(openclaw_image='registry.example/oc@sha256:'+'c'*64, image_config_digest='sha256:'+'d'*64)
    return c


class FleetTests(unittest.TestCase):
    def test_full_fleet_validated_before_selection(self):
        c = fixture()
        self.assertEqual(fleet.select(c, 'alice')['_instance'], 'alice')
        for name in (None, 'missing'):
            with self.assertRaises(ValueError): fleet.select(c, name)
        for field in ('name', 'client_spiffe_id', 'container_name', 'gateway_uid', 'gateway_gid', 'reader_gid'):
            broken = copy.deepcopy(c)
            broken['instances'][2][field] = broken['instances'][1][field]
            with self.subTest(field=field), self.assertRaises(ValueError): fleet.select(broken, 'alice')
        c['instances'][1]['gateway_gid'] = c['instances'][0]['reader_gid']
        with self.assertRaises(ValueError): fleet.validate_fleet(c)

    def test_selectors_are_disjoint_and_helper_is_shared(self):
        a, b, _ = fleet.validate_fleet(fixture())
        first = deploy.selectors(a, deploy.client_id(a))
        second = deploy.selectors(b, deploy.client_id(b))
        self.assertFalse(first <= second)
        self.assertFalse(second <= first)
        self.assertIn('docker:label:org.argus.instance:alice', first)
        self.assertEqual(deploy.selectors(a, deploy.HELPER), deploy.selectors(b, deploy.HELPER))

    def test_render_mounts_and_units_are_instance_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            for c in fleet.validate_fleet(fixture()):
                out = Path(tmp) / c['_instance']
                deploy.render(c, out)
                self.assertFalse((out / 'agent.conf').exists())
                self.assertFalse((out / 'argus-openclaw-agent.service').exists())
                layout = deploy.paths(c)
                unit = (out / (layout['unit'] + '.service')).read_text()
                self.assertIn('Requires=argus-openclaw-agent.service', unit)
                self.assertNotIn('PartOf=', unit)
                self.assertIn(layout['run'].as_posix(), unit)
                compose = json.loads((out / 'compose.json').read_text())['services']['gateway']
                self.assertEqual(compose['labels']['org.argus.instance'], c['_instance'])
                self.assertTrue(all(c['_instance'] in m.split(':')[0] for m in compose['volumes']))
                self.assertFalse(any('sock' in m for m in compose['volumes']))
                config = json.loads((out / 'credentials.json').read_text())
                self.assertEqual(config['helper_spiffe_id'], deploy.HELPER)
                self.assertEqual(config['target_spiffe_id'], deploy.client_id(c))
                self.assertEqual(config['credentials_dir'], (layout['run'] / 'credentials').as_posix())
                private = json.loads((out / 'private-memory.fragment.json').read_text())
                self.assertEqual(private['plugins']['entries']['openviking']['config']['recallTargetTypes'], ['user'])
                self.assertEqual(private['plugins']['entries']['openviking']['config']['accountId'], '')
                self.assertEqual(private['plugins']['entries']['openviking']['config']['userId'], '')
            fleet.render_node(fixture(), Path(tmp) / 'node')
            agent = (Path(tmp) / 'node/agent.conf').read_text()
            self.assertEqual(agent.count('id = "' + deploy.HELPER + '"'), 1)
            self.assertNotIn('/instances/', agent)
            custom = fixture(); custom['node']['server_spiffe_id'] = 'spiffe://argus.local/service/experiment/other'
            fleet.render_node(custom, Path(tmp) / 'other-node')
            self.assertEqual((Path(tmp) / 'node/node.json').read_bytes(), (Path(tmp) / 'other-node/node.json').read_bytes())
            deploy.render(fleet.select(custom, 'alice'), Path(tmp) / 'other-client')
            self.assertEqual(json.loads((Path(tmp) / 'other-client/client.json').read_text())['serverSpiffeId'], custom['node']['server_spiffe_id'])

    def test_instance_stop_never_stops_node_or_sibling(self):
        c = fleet.select(fixture(), 'bob')
        with patch.object(deploy, 'run') as run, patch.object(Path, 'unlink') as unlink:
            deploy.guest_stop(c)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0], ['systemctl', 'stop', 'argus-openclaw-credentials-bob'])
        self.assertIn('bob', str(commands[1][2]))
        self.assertNotIn('argus-openclaw-agent', str(commands))
        self.assertNotIn('alice', str(commands))

    def test_runtime_mount_guard_rejects_sibling_socket_and_extra_groups(self):
        c = fleet.select(fixture(), 'alice')
        layout = deploy.paths(c)
        info = {'Config':{'User':f"{c['gateway_uid']}:{c['gateway_gid']}"},
                'HostConfig':{'GroupAdd':[str(c['reader_gid'])]}, 'Mounts':[
                    {'Type':'bind', 'Source':layout['home'].as_posix(), 'Destination':'/home/node/.openclaw','RW':True},
                    {'Type':'bind', 'Source':(layout['etc']/'client.json').as_posix(), 'Destination':'/etc/argus-openclaw/client.json','RW':False},
                    {'Type':'bind', 'Source':(layout['run']/'credentials').as_posix(), 'Destination':'/run/argus-openclaw/credentials','RW':False}]}
        fleet.check_mounts(info, c)
        for source in ('/run/argus-openclaw/instances/bob/credentials', '/var/run/docker.sock'):
            bad = copy.deepcopy(info); bad['Mounts'][2]['Source'] = source
            with self.assertRaises(ValueError): fleet.check_mounts(bad, c)
        bad = copy.deepcopy(info); bad['HostConfig']['GroupAdd'].append('22002')
        with self.assertRaises(ValueError): fleet.check_mounts(bad, c)
        bad = copy.deepcopy(info); bad['Mounts'][2]['RW'] = True
        with self.assertRaises(ValueError): fleet.check_mounts(bad, c)

    def test_denial_is_not_network_failure_or_health_200(self):
        self.assertEqual(business.denial({'result':'UNKNOWN'}), 'UNKNOWN')
        self.assertEqual(business.denial({'result':'UNAVAILABLE', 'code':'CONNECTION_UNAVAILABLE'}), 'UNKNOWN')
        self.assertEqual(business.denial({'result':'OBSERVED','http_status':200}), 'FAIL')
        self.assertEqual(business.denial({'result':'OBSERVED','http_status':403}), 'PASS')

    def test_probe_preserves_attributed_unavailability_but_docker_failure_stays_unknown(self):
        item = {'deployment':fleet.select(fixture(), 'alice'), 'agent_id':'main', 'account_id':'shared', 'user_id':'alice'}
        observed = {'result':'UNAVAILABLE', 'code':'CONNECTION_UNAVAILABLE', 'network_error':'ECONNREFUSED',
                    'phase':'https_request', 'request_id':'actual-request'}
        with patch.object(business.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, json.dumps(observed), '')):
            self.assertEqual(business.probe(item, 'key', route='/api/v1/system/status'), observed)
        with patch.object(business.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'No such container')):
            self.assertEqual(business.probe(item, 'key', route='/api/v1/system/status')['result'], 'UNKNOWN')

    def test_resolved_key_role_and_mandatory_context_are_checked(self):
        item = {'account_id':'shared', 'user_id':'alice'}
        good = {'result':'OBSERVED','http_status':200,'identity':{'account_id':'shared','user_id':'alice','role':'user','auth_mode':'api_key'}}
        context = {'result':'OBSERVED','http_status':200,'application_status':'ok','identity':{'context_user':'alice'}}
        with patch.object(business, 'probe', side_effect=[good, context]):
            self.assertEqual(business.scope_check(item, 'key')['result'], 'PASS')
        for identity in ({}, good['identity'] | {'role':'root'}, good['identity'] | {'user_id':'bob'}, good['identity'] | {'auth_mode':'trusted'}):
            with patch.object(business, 'probe', side_effect=[good | {'identity':identity}, context]):
                self.assertEqual(business.scope_check(item, 'key')['result'], 'FAIL')
        with patch.object(business, 'probe', side_effect=[good, context | {'identity':{'context_user':'bob'}}]):
            self.assertEqual(business.scope_check(item, 'key')['result'], 'FAIL')
        with patch.object(business, 'probe', side_effect=[good, {'result':'UNKNOWN'}]):
            self.assertEqual(business.scope_check(item, 'key')['result'], 'UNKNOWN')

    def test_failed_preflight_preserves_known_resume_and_unknown_write(self):
        item = {'name':'alice', 'deployment':fleet.select(fixture(), 'alice'), 'agent_id':'main'}
        for known in (True, False):
            with self.subTest(known_task=known), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp)
                evidence = output / 'alice'; evidence.mkdir()
                if known:
                    (evidence / 'run.json').write_text('{}')
                    (evidence / 'processing-events.jsonl').write_text('{}')
                initial = {'result':'UNKNOWN', 'code':'SUBMISSION_IN_PROGRESS', 'evidence_dir':str(evidence)}
                business.atomic(output / 'result.json', {
                    'config_sha256':'digest', 'pair_seed':None, 'run_id':'run', 'operation_id':None,
                    'instances':{'alice':initial}, 'isolation':[], 'completed':False})
                argv = ['fleet_business.py', 'resume', '--config', 'unused.json', '--output', str(output), '--instance', 'alice']
                with patch.object(sys, 'argv', argv), patch.object(business, 'config', return_value=({}, [item], 'digest')), \
                     patch.object(business, 'secret', return_value='key'), patch.object(business, 'lock', side_effect=lambda _: nullcontext()), \
                     patch.dict(os.environ, {}, clear=True), patch.object(business, 'execute_business') as execute, \
                     patch.object(business, 'scope_check', side_effect=[{'result':'UNKNOWN'}, {'result':'PASS'}]), \
                     redirect_stdout(io.StringIO()):
                    business.main()
                    saved = json.loads((output / 'result.json').read_text())['instances']['alice']
                    self.assertEqual(saved['code'], initial['code'])
                    self.assertEqual(saved['scope_check']['result'], 'UNKNOWN')
                    business.main()
                if known:
                    self.assertEqual(execute.call_count, 1)
                    self.assertEqual(execute.call_args.args[0][-2:], ['--resume', str(evidence.resolve())])
                else:
                    execute.assert_not_called()
                    saved = json.loads((output / 'result.json').read_text())['instances']['alice']
                    self.assertEqual(saved['code'], 'INITIAL_WRITE_UNKNOWN')

    def test_unknown_question_submission_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(business.subprocess, 'run') as execute:
            self.assertEqual(business.gateway_question({}, '', 'marker', Path(tmp))['result'], 'UNKNOWN')
            execute.assert_not_called()
            business.atomic(Path(tmp) / 'operation.json', {'result':'PASS'})
            self.assertEqual(business.gateway_question({}, '', 'marker', Path(tmp))['result'], 'PASS')
            execute.assert_not_called()

    def test_inherited_single_instance_overrides_are_cleared(self):
        item = {'deployment':fleet.select(fixture(), 'bob'), 'agent_id':'main'}
        with patch.dict('os.environ', {'DUAL_E2E_SESSION_KEY':'other', 'OPENCLAW_PLUGIN_DIR':'other'}):
            env = business.environment(item, 'secret')
        self.assertEqual(env['OPENCLAW_CONTAINER'], 'argus-openclaw-bob')
        self.assertNotIn('DUAL_E2E_SESSION_KEY', env)
        self.assertNotIn('OPENCLAW_PLUGIN_DIR', env)

    def test_paired_seed_uses_logical_position_not_arm_specific_name(self):
        item = {'deployment':fleet.select(fixture(), 'bob'), 'agent_id':'main', 'pair_index':1}
        with patch.dict('os.environ', {'ARGUS_SEED':'123'}):
            first = business.environment(item, 'secret', run_id='full-bob')
            second = business.environment(item, 'secret', run_id='weak-bob')
            third = business.environment(item | {'pair_index':2}, 'secret')
        self.assertEqual(first['DUAL_E2E_SEED'], second['DUAL_E2E_SEED'])
        self.assertNotEqual(first['DUAL_E2E_SEED'], third['DUAL_E2E_SEED'])
        self.assertNotEqual(first['DUAL_E2E_RUN_ID'], second['DUAL_E2E_RUN_ID'])

    @unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Linux root needed for real UID/GID access test')
    def test_linux_reader_group_cannot_read_sibling_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp); parent.chmod(0o755)
            for name, reader in (('alice', 62101), ('bob', 62102)):
                directory = parent / name; directory.mkdir(); directory.chmod(0o750); os.chown(directory, 0, reader)
                key = directory / 'key.pem'; key.write_text('synthetic-' + name)
                key.chmod(0o640); os.chown(key, 0, reader)
            def drop():
                os.setgroups([62101]); os.setgid(61101); os.setuid(61101)
            code = "from pathlib import Path; import sys; print(Path(sys.argv[1]).read_text())"
            allowed = subprocess.run([sys.executable, '-c', code, str(parent/'alice/key.pem')],
                                     preexec_fn=drop, capture_output=True, text=True)
            refused = subprocess.run([sys.executable, '-c', code, str(parent/'bob/key.pem')],
                                     preexec_fn=drop, capture_output=True, text=True)
            self.assertEqual(allowed.returncode, 0)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn('PermissionError', refused.stderr)

    def test_missing_edges_or_incomplete_isolation_cannot_be_overall_pass(self):
        state = {'instances':{'alice':{'result':'PASS'}}, 'isolation':[{'result':'NOT_RUN'}]}
        self.assertEqual(business.aggregate(state), 'NOT_RUN')
        state['isolation'] = [{'result':'UNKNOWN'}]
        self.assertEqual(business.aggregate(state), 'UNKNOWN')
        state['instances']['alice']['result'] = 'FAIL'
        self.assertEqual(business.aggregate(state), 'FAIL')


if __name__ == '__main__': unittest.main()
