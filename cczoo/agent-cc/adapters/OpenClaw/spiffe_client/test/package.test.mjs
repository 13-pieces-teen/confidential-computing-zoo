import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync, mkdirSync, rmSync, renameSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { createServer } from 'node:https';
import { once } from 'node:events';
import { X509Certificate } from 'node:crypto';

test('execute the actual patched upstream client and setup over real mTLS', {skip: !process.env.ARGUS_TEST_PLUGIN_DIR}, async t => {
  const root = mkdtempSync(join(tmpdir(), 'argus-plugin-test-'));
  t.after(() => rmSync(root, {recursive:true, force:true}));
  execFileSync('go', ['run', fileURLToPath(new URL('certificates.go', import.meta.url)), root], {env:{...process.env, GO111MODULE:'off'}});
  const cert = name => readFileSync(join(root, name + '.pem'));
  const calls = [];
  const server = createServer({cert:cert('server'), key:cert('server-key'), ca:cert('bundle'), requestCert:true, rejectUnauthorized:true}, (request,response) => {
    calls.push({path:request.url, method:request.method, cert:request.socket.getPeerCertificate().subjectaltname,
      key:request.headers['x-api-key'], account:request.headers['x-openviking-account'],
      user:request.headers['x-openviking-user'], actor:request.headers['x-openviking-actor-peer']});
    if (request.url === '/health') response.end(JSON.stringify({status:'ok', healthy:true, version:'v0.4.8'}));
    else if (request.method === 'GET' && request.url.startsWith('/api/v1/sessions')) response.end(JSON.stringify({status:'ok', result:[]}));
    else { request.resume(); request.on('end', () => response.end(JSON.stringify({status:'ok', result:{session_id:'test-session'}}))); }
  });
  server.on('tlsClientError', () => {});
  server.listen(0,'127.0.0.1'); await once(server,'listening');
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
  const credentials = join(root,'credentials'); const generation = 'generation-release';
  mkdirSync(join(credentials,generation), {recursive:true, mode:0o750});
  for (const [name,source] of [['svid.pem','client'],['key.pem','client-key'],['bundle.pem','bundle']]) writeFileSync(join(credentials,generation,name),cert(source),{mode:0o640});
  const leaf = new X509Certificate(cert('client'));
  const renew = () => { writeFileSync(join(credentials,'.next'),JSON.stringify({version:1,generation,serial:leaf.serialNumber.toLowerCase().replace(/^0+/,''),expires_at:Date.parse(leaf.validTo),lease_until:Date.now()+2500}),{mode:0o640}); renameSync(join(credentials,'.next'),join(credentials,'ready.json')); };
  renew(); const timer=setInterval(renew,500); t.after(()=>clearInterval(timer));
  const origin=`https://127.0.0.1:${server.address().port}`;
  const config=join(root,'client.json');
  writeFileSync(config,JSON.stringify({origin,clientSpiffeId:'spiffe://argus.local/agent/openclaw',serverSpiffeId:'spiffe://argus.local/service/openviking-cmem',credentialsDir:credentials}),{mode:0o600});
  process.env.OPENVIKING_SPIFFE_CONFIG=config;
  const directory=process.env.ARGUS_TEST_PLUGIN_DIR;
  const pkg=JSON.parse(readFileSync(join(directory,'package.json')));
  assert.equal(pkg.argusSpiffe.revision,'argus.3');
  const {OpenVikingClient}=await import(pathToFileURL(join(directory,'dist/client.js')));
  const {__test__:setup}=await import(pathToFileURL(join(directory,'dist/commands/setup.js')));
  const client=new OpenVikingClient(origin,'user-test-key','main',5000,'account-a','user-a');
  await client.healthCheck();
  await client.addSessionMessage('test-session','user',[{type:'text',text:'marker'}]);
  await client.getSessionContext('test-session',128000,'test_worker');
  assert.ok(calls.some(call=>call.path.includes('/context?') && call.account==='account-a' && call.user==='user-a' && call.actor==='test_worker'));
  const probe=await setup.probeApiKeyType(origin,'user-test-key');
  assert.ok(probe);
  assert.ok(calls.some(call=>call.path==='/api/v1/sessions/test-session/messages' && call.method==='POST' && call.key==='user-test-key'));
  assert.ok(calls.some(call=>call.path.startsWith('/api/v1/sessions') && call.method==='GET'));
  assert.ok(calls.every(call=>call.cert.includes('spiffe://argus.local/agent/openclaw')));
  const before=calls.length;
  server.setSecureContext({cert:cert('wrong-server'),key:cert('wrong-server-key'),ca:cert('bundle')});
  server.closeAllConnections();
  await assert.rejects(client.healthCheck());
  assert.equal(calls.length,before);
});
