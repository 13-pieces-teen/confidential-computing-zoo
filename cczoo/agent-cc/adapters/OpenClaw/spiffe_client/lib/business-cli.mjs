// Installed alongside the fixed upstream client; stdout is a resumable JSONL log.
import fs from 'node:fs';
import {execFileSync} from 'node:child_process';
import {createSpiffeTransport} from './transport.mjs';
import {runBusinessFlow} from './business-flow.mjs';

const [configPath, expectedBase, marker, agentId, sessionKey, captureAttempts, commitAttempts, mode] = process.argv.slice(2);
let transport;
try {
  const [{OpenVikingClient}, {memoryOpenVikingConfigSchema}, {createOpenVikingSessionRoutingRuntime}] = await Promise.all([
    import('../client.js'), import('../config.js'), import('../plugin/openviking-session-routing-runtime.js'),
  ]);
  const config = key => JSON.parse(execFileSync('openclaw', ['config', 'get', key, '--json'], {
    encoding: 'utf8', env: {...process.env, OPENCLAW_CONFIG_PATH: configPath},
  }));
  const cfg = memoryOpenVikingConfigSchema.parse(config('plugins.entries.openviking.config'));
  if (config('plugins.slots.contextEngine') !== 'openviking' || cfg.mode !== 'remote'
      || cfg.baseUrl.replace(/\/+$/, '') !== expectedBase || !cfg.autoCapture || !cfg.autoRecall) {
    throw new Error('BUSINESS_CONFIG_MISMATCH');
  }
  const routing = createOpenVikingSessionRoutingRuntime({peerPrefix: cfg.peer_prefix, logFindRequests: false, logger: {info() {}}});
  const actor = routing.resolvePluginSessionRouting({agentId, sessionKey}).agentId;
  transport = createSpiffeTransport();
  const scope = {origin: expectedBase, account_id: cfg.accountId, user_id: cfg.userId,
    actor_peer_id: actor, agent_id: agentId, session_key: sessionKey,
    client_spiffe_id: transport.config.clientSpiffeId, server_spiffe_id: transport.config.serverSpiffeId};
  const key = cfg.apiKey;
  if (!key || key !== process.env.OPENVIKING_API_KEY) throw new Error('API_KEY_SCOPE_MISMATCH');
  // Same parsed tenant configuration, actor and transport for every operation.
  const client = new OpenVikingClient(expectedBase, key, cfg.peer_prefix, 30000,
    cfg.accountId, cfg.userId, undefined, {transport});
  const listSessions = async () => {
    const response = await transport(expectedBase + '/api/v1/sessions', {
      headers: {'X-API-Key': key, 'X-OpenViking-Actor-Peer': actor,
        ...(cfg.accountId ? {'X-OpenViking-Account': cfg.accountId} : {}),
        ...(cfg.userId ? {'X-OpenViking-User': cfg.userId} : {})}, signal: AbortSignal.timeout(30000),
    });
    if (!response.ok) throw new Error('SESSION_LIST_FAILED');
    const value = await response.json();
    if (value.status !== 'ok') throw new Error('SESSION_LIST_FAILED');
    return value.result;
  };
  const previous = JSON.parse(fs.readFileSync(0, 'utf8').trim() || 'null');
  await runBusinessFlow({client, listSessions, marker, scope, previous, resume: mode === 'resume',
    captureAttempts: Number(captureAttempts), commitAttempts: Number(commitAttempts),
    emit: value => new Promise(resolve => process.stdout.write(JSON.stringify(value) + '\n', resolve)),
  });
} catch (error) {
  // Never print upstream error text, which can include responses or credentials.
  const setupCodes = ['BUSINESS_CONFIG_MISMATCH', 'API_KEY_SCOPE_MISMATCH'];
  const code = error.code ?? (setupCodes.includes(error.message) ? error.message : 'BUSINESS_SETUP_FAILED');
  const failure = JSON.stringify({component: 'argus-business-error', code});
  await new Promise(resolve => process.stdout.write(failure + '\n', resolve));
  console.error(failure);
  process.exitCode = 1;
} finally { transport?.close(); }
