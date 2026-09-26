// Invoked inside a selected Gateway with stdin; no key appears in argv/output.
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';

const spec = JSON.parse(process.argv[2]);
const digest = value => createHash('sha256').update(value).digest('hex');
let transport, requestFailure, response;
try {
  const get = key => JSON.parse(execFileSync('openclaw', ['config', 'get', key, '--json'], {encoding:'utf8'}));
  const installs = get('plugins.installs');
  const plugins = Object.values(installs).map(i => i.installPath).filter(directory => {
    try { const p = JSON.parse(fs.readFileSync(path.join(directory, 'package.json')));
      return p.name === '@openviking/openclaw-plugin' && p.version === '2026.6.18' && p.argusSpiffe?.revision === 'argus.3';
    } catch { return false; }
  });
  if (plugins.length !== 1) throw new Error('PINNED_PLUGIN_AMBIGUOUS');
  const base = plugins[0];
  const {memoryOpenVikingConfigSchema} = await import(pathToFileURL(path.join(base, 'dist/config.js')));
  const cfg = memoryOpenVikingConfigSchema.parse(get('plugins.entries.openviking.config'));
  const native = await import(pathToFileURL(path.join(base, 'dist/argus-spiffe/transport.mjs')));
  const {createSpiffeTransport, requestIdentity} = native;
  requestFailure = native.requestFailure;
  transport = createSpiffeTransport();
  if (!cfg.apiKey || digest(cfg.apiKey) !== spec.key_sha256 || cfg.accountId !== spec.account_id || cfg.userId !== spec.user_id
      || cfg.mode !== 'remote' || cfg.baseUrl.replace(/\/+$/, '') !== transport.config.origin
      || transport.config.clientSpiffeId !== spec.client_spiffe_id
      || transport.config.serverSpiffeId !== spec.server_spiffe_id
      || cfg.recallResources || cfg.recallTargetTypes.length !== 1 || cfg.recallTargetTypes[0] !== 'user') throw new Error('FLEET_SCOPE_MISMATCH');
  const headers = {'X-OpenViking-Account':spec.account_id, 'X-OpenViking-User':spec.user_id};
  if (spec.key_mode !== 'missing') headers['X-API-Key'] = spec.key_mode === 'invalid' ? 'argus-invalid-synthetic-key' : cfg.apiKey;
  if (spec.forged_user) headers['X-OpenViking-User'] = spec.forged_user;
  let method = 'GET', route = spec.route, body;
  if (spec.query !== undefined) {
    method = 'POST'; route = '/api/v1/search/find';
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify({query:spec.query, limit:50, ...(spec.target_uri ? {target_uri:spec.target_uri} : {})});
  }
  response = await transport(new URL(route, transport.config.origin), {method, headers, body, signal:AbortSignal.timeout(30000)});
  const text = await response.text();
  let value; try { value = JSON.parse(text); } catch { value = null; }
  const items = [];
  function walk(v) {
    if (Array.isArray(v)) return v.forEach(walk);
    if (!v || typeof v !== 'object') return;
    if (typeof v.uri === 'string') items.push({uri:v.uri, level:v.level});
    for (const child of Object.values(v)) if (child && typeof child === 'object') walk(child);
  }
  walk(value?.result);
  console.log(JSON.stringify({result:'OBSERVED', http_status:response.status,
    application_status:value?.status ?? null, body_sha256:digest(text), bytes:Buffer.byteLength(text),
    contains_fact:!!spec.fact_sha256 && (text.match(/ARGUS_FACT_[A-F0-9]{32}/g) ?? []).some(f => digest(f) === spec.fact_sha256),
    items, identity:spec.identity_probe ? {account_id:value?.account_id, user_id:value?.user_id,
      role:value?.role, auth_mode:value?.auth_mode, context_user:value?.result?.user} : undefined, ...requestIdentity(response)}));
} catch (error) {
  const unavailable = requestFailure?.(error, response);
  console.log(JSON.stringify(unavailable ? {result:'UNAVAILABLE', code:'CONNECTION_UNAVAILABLE', ...unavailable}
    : {result:'UNKNOWN', code:error.message === 'FLEET_SCOPE_MISMATCH' ? error.message : 'PROBE_FAILED'}));
  process.exitCode = 1;
} finally { transport?.close(); }
