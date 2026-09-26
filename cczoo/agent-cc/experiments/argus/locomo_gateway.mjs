// Real, pinned Gateway adapter. JSON spec arrives over stdin, never shell argv.
// Reference answers are never passed to this process. Evaluation configuration
// is installed before admission; this adapter never changes a running Gateway.
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';

const hash = text => createHash('sha256').update(text).digest('hex');
const spec = JSON.parse(fs.readFileSync(0, 'utf8'));
let transport;
let identityOf;
let receipts = [];
try {
  const get = key => JSON.parse(execFileSync('openclaw', ['config', 'get', key, '--json'], {encoding:'utf8', stdio:['ignore','pipe','pipe']}));
  const installed = Object.values(get('plugins.installs')).map(v => v.installPath).filter(directory => {
    try {
      const p = JSON.parse(fs.readFileSync(path.join(directory, 'package.json')));
      return p.name === '@openviking/openclaw-plugin' && p.version === '2026.6.18' && p.argusSpiffe?.revision === 'argus.3';
    } catch { return false; }
  });
  if (installed.length !== 1) throw new Error('PINNED_PLUGIN_AMBIGUOUS');
  const base = installed[0];
  const {memoryOpenVikingConfigSchema} = await import(pathToFileURL(path.join(base, 'dist/config.js')));
  const raw = get('plugins.entries.openviking.config');
  const cfg = memoryOpenVikingConfigSchema.parse(raw);
  const tools = get('tools');
  const agents = get('agents');
  const {createSpiffeTransport, requestIdentity} = await import(pathToFileURL(path.join(base, 'dist/argus-spiffe/transport.mjs')));
  const {createOpenVikingSessionRoutingRuntime} = await import(pathToFileURL(path.join(base, 'dist/plugin/openviking-session-routing-runtime.js')));
  transport = createSpiffeTransport(); identityOf = requestIdentity;
  if (get('plugins.slots.contextEngine') !== 'openviking' || cfg.mode !== 'remote' || !cfg.apiKey
      || cfg.autoCapture !== false || cfg.autoRecall !== true || !tools.deny?.includes('*')
      || cfg.recallResources || cfg.recallTargetTypes.length !== 1 || cfg.recallTargetTypes[0] !== 'user'
      || cfg.accountId !== spec.account_id || cfg.userId !== spec.user_id
      || cfg.baseUrl.replace(/\/+$/, '') !== transport.config.origin
      || transport.config.clientSpiffeId !== spec.client_spiffe_id
      || transport.config.serverSpiffeId !== spec.server_spiffe_id) throw new Error('LOCOMO_SCOPE_OR_PROTOCOL_MISMATCH');
  const routing = createOpenVikingSessionRoutingRuntime({peerPrefix:cfg.peer_prefix, logFindRequests:false, logger:{info(){}}});
  const actor = routing.resolvePluginSessionRouting({agentId:spec.agent_id, sessionKey:spec.session_key}).agentId;
  const scope = {account_id:cfg.accountId, user_id:cfg.userId, actor_peer_id:actor,
    client_spiffe_id:transport.config.clientSpiffeId, server_spiffe_id:transport.config.serverSpiffeId};
  const headers = {'X-API-Key':cfg.apiKey, 'X-OpenViking-Account':cfg.accountId,
    'X-OpenViking-User':cfg.userId, 'X-OpenViking-Actor-Peer':actor, 'Content-Type':'application/json'};
  async function request(route, method='GET', body) {
    const response = await transport(new URL(route, transport.config.origin), {method, headers,
      ...(body === undefined ? {} : {body:JSON.stringify(body)}), signal:AbortSignal.timeout(30000)});
    const value = await response.json();
    receipts.push({...identityOf(response), http_status:response.status});
    if (!response.ok || value.status === 'error') throw new Error('APPLICATION_REQUEST_FAILED');
    return value;
  }
  if (spec.action === 'preflight') {
    const health = await request('/health');
    const status = await request('/api/v1/system/status');
    if (health.auth_mode !== 'api_key' || health.role !== 'user' || health.account_id !== cfg.accountId
        || health.user_id !== cfg.userId || status.result?.user !== cfg.userId) throw new Error('BUSINESS_IDENTITY_MISMATCH');
    console.log(JSON.stringify({result:'OBSERVED', scope, read_only_qa:true, receipts,
      config_sha256:hash(JSON.stringify({...raw, apiKey:'[REDACTED]', tools, agents}))}));
  } else if (spec.action === 'api') {
    if (!['GET','POST'].includes(spec.method) || !/^\/api\/v1\/(sessions(?:\/[^/?]+(?:\/(?:messages|commit|context))?)?|tasks\/[^/?]+)$/.test(spec.route)) throw new Error('INVALID_ROUTE');
    const value = await request(spec.route, spec.method, spec.body);
    console.log(JSON.stringify({result:'OBSERVED', scope, receipts, value:value.result ?? value}));
  } else if (spec.action === 'qa') {
    const started = performance.now();
    const out = execFileSync('openclaw', ['agent','--agent',spec.agent_id,'--session-key',spec.session_key,
      '--message',spec.question,'--timeout',String(spec.timeout_seconds ?? 180),'--json'],
      {encoding:'utf8', timeout:((spec.timeout_seconds ?? 180)+20)*1000, maxBuffer:16*1024*1024, stdio:['ignore','pipe','pipe']});
    const response = JSON.parse(out);
    if (response.status !== 'ok' || !response.runId || response.error || !Array.isArray(response.result?.payloads)) throw new Error('GATEWAY_RESULT_INVALID');
    const answer = response.result.payloads.filter(v => typeof v.text === 'string').map(v => v.text).join('\n').trim();
    console.log(JSON.stringify({result:'OBSERVED', scope, answer, run_id:response.runId,
      session_key:spec.session_key, duration_ms:performance.now()-started,
      model:response.result.meta?.agentMeta?.model ?? null,
      provider:response.result.meta?.agentMeta?.provider ?? null,
      usage:response.result.meta?.agentMeta?.usage ?? null}));
  } else throw new Error('INVALID_ACTION');
} catch (error) {
  const known = ['PINNED_PLUGIN_AMBIGUOUS','LOCOMO_SCOPE_OR_PROTOCOL_MISMATCH','BUSINESS_IDENTITY_MISMATCH','INVALID_ROUTE','INVALID_ACTION','GATEWAY_RESULT_INVALID','APPLICATION_REQUEST_FAILED'];
  console.log(JSON.stringify({result:'UNKNOWN', code:known.includes(error.message) ? error.message : 'GATEWAY_IO_FAILED', receipts}));
  process.exitCode = 1;
} finally { transport?.close(); }
