"""Real SPIRE config + x509pop enrollment/Entry CLI; no Docker or TDX claim.

Run on Linux with ARGUS_TEST_SPIRE_BIN_DIR pointing to official SPIRE 1.15.3.
"""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('deploy', ROOT/'deploy.py')
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


@unittest.skipUnless(os.environ.get('ARGUS_TEST_SPIRE_BIN_DIR') and os.name == 'posix', 'requires official Linux SPIRE 1.15.3')
class OfficialSPIRETests(unittest.TestCase):
    def test_config_x509pop_and_entries(self):
        spire = Path(os.environ['ARGUS_TEST_SPIRE_BIN_DIR'])
        with tempfile.TemporaryDirectory(prefix='argus-oc-spire-') as temporary:
            root = Path(temporary)
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            ca, key, cert = root/'ca.pem', root/'ca.key', root/'node.pem'
            deploy.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',key,'-out',ca,'-subj','/CN=Local Test CA','-days','1'])
            deploy.run(['openssl','req','-new','-newkey','rsa:2048','-nodes','-keyout',root/'node.key','-out',root/'node.csr','-subj','/CN=openclaw-test'])
            (root/'extensions').write_text('basicConstraints=CA:FALSE\nkeyUsage=digitalSignature\n')
            deploy.run(['openssl','x509','-req','-in',root/'node.csr','-CA',ca,'-CAkey',key,'-CAcreateserial',
                        '-out',cert,'-days','1','-extfile',root/'extensions'])
            fingerprint = deploy.run(['openssl','x509','-in',cert,'-fingerprint','-sha1','-noout']).split('=')[-1].replace(':','').lower()
            c = json.loads((ROOT/'config/deployment.example.json').read_text())
            c.update(server_address='127.0.0.1',server_port=port,node_certificate_sha1=fingerprint,
                     openclaw_image='example/oc@sha256:'+'b'*64,image_config_digest='sha256:'+'c'*64,helper_sha256='d'*64)
            generated = root/'generated'
            deploy.render(c, generated)
            # Validate the FULL delivered config including Docker and separate Broker socket directories.
            deploy.run([spire/'spire-agent','validate','-config',generated/'agent.conf'])
            fragment = (generated/'server-x509pop.fragment.hcl').read_text().replace('/etc/argus-openclaw/x509pop-ca.pem',str(ca))
            server = '''server {
 bind_address="127.0.0.1" bind_port=%d trust_domain="argus.local"
 socket_path="%s" data_dir="%s"
 ca_subject { country=["CN"] organization=["Local test"] common_name="SPIRE test" }
}
plugins {
 DataStore "sql" { plugin_data { database_type="sqlite3" connection_string="%s" } }
 KeyManager "memory" {}
 %s
}
''' % (port, root/'server.sock',root/'server-data',root/'database.sqlite',fragment)
            (root/'server.conf').write_text(server)
            deploy.run([spire/'spire-server','validate','-config',root/'server.conf'])
            processes = []
            with (root/'server.log').open('w+') as slog, (root/'agent.log').open('w+') as alog:
                try:
                    processes.append(subprocess.Popen([str(spire/'spire-server'),'run','-config',str(root/'server.conf')],stdout=slog,stderr=slog))
                    deadline=time.monotonic()+20
                    while not (root/'server.sock').exists():
                        if processes[0].poll() is not None or time.monotonic()>deadline: raise ValueError('SPIRE Server startup failed')
                        time.sleep(.1)
                    bundle = deploy.run([spire/'spire-server','bundle','show','-socketPath',root/'server.sock','-format','pem'])
                    (root/'bundle.pem').write_text(bundle)
                    agent = (generated/'agent.conf').read_text()
                    for before, after in {'/var/lib/argus-openclaw/spire':str(root/'agent-data'),
                                          '/etc/argus-openclaw/bootstrap-bundle.pem':str(root/'bundle.pem'),
                                          '/etc/argus-openclaw/node/key.pem':str(root/'node.key'),
                                          '/etc/argus-openclaw/node/cert.pem':str(cert),
                                          '/run/spire/openclaw/agent.sock':str(root/'workload/agent.sock'),
                                          '/run/spire/openclaw-broker/broker.sock':str(root/'broker/broker.sock')}.items():
                        agent=agent.replace(before,after)
                    # Runtime test exercises x509pop, not Docker workload attestation.
                    agent=agent.replace('    WorkloadAttestor "docker" { plugin_data { docker_socket_path = "unix:///var/run/docker.sock" } }\n','')
                    (root/'agent.conf').write_text(agent)
                    processes.append(subprocess.Popen([str(spire/'spire-agent'),'run','-config',str(root/'agent.conf')],stdout=alog,stderr=alog))
                    agents=[]
                    deadline=time.monotonic()+20
                    while time.monotonic()<deadline:
                        agents=json.loads(deploy.run([spire/'spire-server','agent','list','-socketPath',root/'server.sock','-output','json'])).get('agents',[])
                        if agents: break
                        if processes[-1].poll() is not None: raise ValueError('SPIRE Agent startup failed')
                        time.sleep(.1)
                    self.assertEqual(len(agents),1)
                    self.assertEqual(deploy.id_string(agents[0]['id']),deploy.agent_id(c))
                    self.assertEqual(agents[0].get('attestation_type',agents[0].get('attestationType')),'x509pop')
                    for identity in (deploy.HELPER,deploy.CLIENT):
                        cmd=[spire/'spire-server','entry','create','-socketPath',root/'server.sock',
                             '-parentID',deploy.agent_id(c),'-spiffeID',identity,'-x509SVIDTTL','300']
                        if identity==deploy.CLIENT: cmd+=['-disableX509SVIDPrefetch']
                        for selector in sorted(deploy.selectors(c,identity)): cmd+=['-selector',selector]
                        deploy.run(cmd)
                        entries=json.loads(deploy.run([spire/'spire-server','entry','show','-socketPath',root/'server.sock',
                                                     '-spiffeID',identity,'-output','json']))['entries']
                        deploy.audit(entries,c,identity)
                except Exception:
                    for label,stream in [('Server',slog),('Agent',alog)]:
                        stream.flush(); stream.seek(0); print(label,stream.read())
                    raise
                finally:
                    for process in reversed(processes):
                        process.terminate()
                        try: process.wait(timeout=5)
                        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)


if __name__ == '__main__': unittest.main()
