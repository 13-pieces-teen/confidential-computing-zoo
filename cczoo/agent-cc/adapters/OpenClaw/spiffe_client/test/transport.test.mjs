import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync, mkdirSync, rmSync, renameSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { createServer } from 'node:https';
import { once } from 'node:events';
import { X509Certificate } from 'node:crypto';
import { createSpiffeTransport, requestIdentity, requestFailure } from '../lib/transport.mjs';
import { validateSVID } from '../lib/svid.mjs';

const root = mkdtempSync(join(tmpdir(), 'argus-spiffe-test-'));
execFileSync('go', ['run', fileURLToPath(new URL('certificates.go', import.meta.url)), root], { env: { ...process.env, GO111MODULE: 'off' }, stdio: 'pipe' });
const cert = name => readFileSync(join(root, name + '.pem'));
const clientID = 'spiffe://argus.local/agent/openclaw';
const serverID = 'spiffe://argus.local/service/openviking-cmem';
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
test.after(() => rmSync(root, { recursive: true, force: true }));

async function fixture(t, serverName = 'server', handler) {
  const received = [];
  const server = createServer({ cert: cert(serverName), key: cert(serverName + '-key'), ca: cert('bundle'),
    requestCert: true, rejectUnauthorized: true }, (request, response) => {
    const peer = request.socket.getPeerCertificate();
    received.push({ method: request.method, path: request.url, headers: request.headers, serial: peer.serialNumber, port: request.socket.remotePort });
    try { validateSVID(peer.raw, clientID); } catch { response.writeHead(403).end(); return; }
    if (handler) { handler(request, response); return; }
    const chunks = [];
    request.on('data', chunk => chunks.push(chunk));
    request.on('end', () => response.end(JSON.stringify({ status: 'ok', result: { body: Buffer.concat(chunks).toString(), method: request.method } })));
  });
  server.on('tlsClientError', () => {});
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
  const directory = mkdtempSync(join(root, 'credentials-'));
  const config = { origin: `https://127.0.0.1:${server.address().port}`, clientSpiffeId: clientID, serverSpiffeId: serverID, credentialsDir: directory, requestTimeoutMs: 10000 };
  let current;
  let publishing = true;
  function renew() {
    if (!publishing) return;
    const next = { ...current, lease_until: Math.min(Date.now() + 2500, current.expires_at) };
    const file = join(directory, '.ready');
    writeFileSync(file, JSON.stringify(next), { mode: 0o640 });
    renameSync(file, join(directory, 'ready.json'));
  }
  function publish(name = 'client', keyName = name + '-key') {
    const generation = 'generation-' + Math.random().toString(36).slice(2);
    const path = join(directory, generation);
    mkdirSync(path, { mode: 0o750 });
    for (const [file, source] of [['svid.pem', name], ['key.pem', keyName], ['bundle.pem', 'bundle']]) writeFileSync(join(path, file), cert(source), { mode: 0o640 });
    const x509 = new X509Certificate(cert(name));
    current = { version: 1, generation, serial: x509.serialNumber.toLowerCase().replace(/^0+/, ''), expires_at: Date.parse(x509.validTo) };
    renew();
    return current;
  }
  publish();
  const timer = setInterval(renew, 500);
  t.after(() => clearInterval(timer));
  const transport = createSpiffeTransport(config);
  t.after(() => transport.close());
  return { config, transport, received, server, publish, directory, stopLease: () => { publishing = false; },
    remove: () => { publishing = false; rmSync(join(directory, 'ready.json')); } };
}

test('an established response is interrupted at the peer certificate expiry', async t => {
  const f = await fixture(t, 'short-server', (_request, response) => { response.writeHead(200); response.write('waiting'); });
  const response = await f.transport(f.config.origin + '/stream');
  await assert.rejects(response.text());
});

test('real URI-only mTLS, request body, API key, and connection reuse', async t => {
  const f = await fixture(t);
  const first = await f.transport(f.config.origin + '/api/v1/sessions', { method: 'POST', headers: { 'X-API-Key': 'test-business-key', 'Content-Type': 'application/json' }, body: '{"session_id":"test"}' });
  assert.equal((await first.json()).result.body, '{"session_id":"test"}');
  const second = await f.transport(f.config.origin + '/health');
  await second.text();
  assert.equal(f.received[0].headers['x-api-key'], 'test-business-key');
  assert.equal(f.received[0].port, f.received[1].port);
  assert.equal(requestIdentity(second).server_spiffe_id, serverID);
  assert.equal(requestIdentity(second).client_spiffe_id, clientID);
});

test('closed listener yields an attributed unavailable request, while missing credentials do not', async t => {
  const f = await fixture(t);
  await new Promise(resolve => f.server.close(resolve));
  await assert.rejects(f.transport(f.config.origin + '/health'), error => {
    const failure = requestFailure(error);
    assert.equal(failure.network_error, 'ECONNREFUSED');
    assert.equal(failure.phase, 'https_request');
    assert.ok(failure.request_id);
    return true;
  });
  f.remove();
  await assert.rejects(f.transport(f.config.origin + '/health'), error => {
    assert.equal(requestFailure(error), undefined);
    return true;
  });
});

test('a reset after response headers is observable without counting it as zero receipt', async t => {
  const f = await fixture(t, 'server', (_request, response) => {
    response.writeHead(200); response.write('partial');
    setTimeout(() => response.destroy(), 20);
  });
  const response = await f.transport(f.config.origin + '/stream');
  await assert.rejects(response.text(), error => {
    const failure = requestFailure(error, response);
    assert.equal(failure.network_error, 'ECONNRESET');
    assert.equal(failure.phase, 'response_body');
    assert.equal(failure.request_id, requestIdentity(response).request_id);
    return true;
  });
  assert.equal(f.received.length, 1);
});

test('request timeout remains inconclusive even after the service received it', async t => {
  const f = await fixture(t, 'server', () => {});
  await assert.rejects(f.transport(f.config.origin + '/slow', {signal:AbortSignal.timeout(200)}), error => {
    assert.equal(requestFailure(error), undefined);
    return true;
  });
  assert.equal(f.received.length, 1);
});

for (const invalid of ['wrong-server', 'multiple-uri', 'bad-ku', 'bad-eku', 'expired', 'spoofed-san']) {
  test(`reject ${invalid} before sending business data`, async t => {
    const f = await fixture(t, invalid);
    await assert.rejects(f.transport(f.config.origin + '/secret', { headers: { 'X-API-Key': 'never-send' } }), error => {
      assert.equal(requestFailure(error), undefined);
      return true;
    });
    assert.equal(f.received.length, 0);
  });
}

test('wrong own ID and mismatched key cannot make requests', async t => {
  const f = await fixture(t);
  f.publish('wrong-client');
  await assert.rejects(f.transport(f.config.origin + '/health'));
  f.publish('client', 'client-rotated-key');
  await assert.rejects(f.transport(f.config.origin + '/health'));
  assert.equal(f.received.length, 0);
});

test('untrusted server chain is rejected despite a matching SPIFFE URI', async t => {
  const f = await fixture(t);
  const other = mkdtempSync(join(root, 'other-ca-'));
  execFileSync('go', ['run', fileURLToPath(new URL('certificates.go', import.meta.url)), other], { env: { ...process.env, GO111MODULE: 'off' } });
  f.server.setSecureContext({ cert: readFileSync(join(other, 'server.pem')), key: readFileSync(join(other, 'server-key.pem')), ca: cert('bundle') });
  await assert.rejects(f.transport(f.config.origin + '/health'));
  assert.equal(f.received.length, 0);
});

test('rotation switches client credentials and server certificate without restarting the client', async t => {
  const f = await fixture(t);
  const before = await f.transport(f.config.origin + '/health'); await before.text();
  f.publish('client-rotated');
  f.server.setSecureContext({ cert: cert('server-rotated'), key: cert('server-rotated-key'), ca: cert('bundle') });
  const after = await f.transport(f.config.origin + '/health'); await after.text();
  assert.notEqual(requestIdentity(before).client_serial, requestIdentity(after).client_serial);
  assert.notEqual(requestIdentity(before).server_serial, requestIdentity(after).server_serial);
});

test('identity removal closes an active response and prevents new requests', async t => {
  const f = await fixture(t, 'server', (_request, response) => { response.writeHead(200); response.write('waiting'); });
  const response = await f.transport(f.config.origin + '/stream');
  const failed = assert.rejects(response.text());
  f.remove();
  await failed;
  await assert.rejects(f.transport(f.config.origin + '/health'));
});

test('publisher crash or freeze expires its lease and blocks requests', async t => {
  const f = await fixture(t);
  const response = await f.transport(f.config.origin + '/health'); await response.text();
  f.stopLease();
  await sleep(2800);
  await assert.rejects(f.transport(f.config.origin + '/health'), /lease/);
});

test('partial publication fails closed and a new complete generation recovers', async t => {
  const f = await fixture(t);
  const bad = f.publish('client-rotated');
  rmSync(join(f.directory, bad.generation, 'key.pem'));
  await assert.rejects(f.transport(f.config.origin + '/health'));
  f.publish();
  const response = await f.transport(f.config.origin + '/health');
  assert.equal(response.status, 200); await response.text();
});

test('HTTP and cross-origin requests never use the client identity; redirects are not followed', async t => {
  const f = await fixture(t, 'server', (_request, response) => response.writeHead(302, { Location: 'http://127.0.0.1:1/stolen' }).end());
  await assert.rejects(f.transport('http://127.0.0.1/secret'), /origin/);
  await assert.rejects(f.transport('https://example.org/secret'), /origin/);
  await assert.rejects(f.transport(f.config.origin + '/redirect'), /redirect/);
  assert.equal(f.received.length, 1);
});

test('multipart bodies and abort signals preserve fetch behavior', async t => {
  const f = await fixture(t);
  const body = new FormData(); body.append('file', new Blob(['test-data']), 'test.txt');
  const response = await f.transport(f.config.origin + '/upload', { method: 'POST', body });
  assert.match((await response.json()).result.body, /test-data/);
  await assert.rejects(f.transport(f.config.origin + '/abort', { signal: AbortSignal.abort() }), error => {
    assert.equal(requestFailure(error), undefined);
    return true;
  });
});

test('a different authenticated client gets the receiving service HTTP denial', async t => {
  const f = await fixture(t);
  f.publish('wrong-client');
  const other = createSpiffeTransport({...f.config, clientSpiffeId:'spiffe://argus.local/service/wrong'});
  t.after(() => other.close());
  const response = await other(f.config.origin + '/health');
  assert.equal(response.status,403);
  assert.equal(response.ok,false);
  await response.text();
});
