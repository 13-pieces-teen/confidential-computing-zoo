// Run inside the actual admitted Gateway. Inputs are stdin; secrets stay in its config.
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';

const spec = JSON.parse(fs.readFileSync(0, 'utf8'));
const hash = text => createHash('sha256').update(text).digest('hex');
let transport;
let inputRelease;
try {
  const get = key => JSON.parse(execFileSync('openclaw', ['config','get',key,'--json'], {encoding:'utf8', stdio:['ignore','pipe','pipe']}));
  const installed = Object.values(get('plugins.installs')).map(p => p.installPath).filter(directory => {
    try { const p = JSON.parse(fs.readFileSync(path.join(directory,'package.json')));
      return p.name === '@openviking/openclaw-plugin' && p.version === '2026.6.18' && p.argusSpiffe?.revision === 'argus.3';
    } catch { return false; }
  });
  if (installed.length !== 1) throw new Error('PINNED_PLUGIN_AMBIGUOUS');
  const base = installed[0];
  if (!fs.existsSync(path.join(base,'dist/argus-spiffe/task-audit.mjs'))
      || !fs.readFileSync(path.join(base,'dist/plugin/tool-registration.js'),'utf8').includes('auditToolFactory(toolOrFactory)')
      || !fs.readFileSync(path.join(base,'dist/client.js'),'utf8').includes('auditCommit(result, sessionId)')) throw new Error('TASK_AUDIT_MISSING');
  if ((spec.proposal_schema || spec.work_item_id)
      && !fs.readFileSync(path.join(base,'dist/argus-spiffe/task-audit.mjs'),'utf8').includes('output_proposals:proposalReceipts(result)')) {
    throw new Error('TYPED_PROPOSAL_AUDIT_MISSING');
  }
  const {memoryOpenVikingConfigSchema} = await import(pathToFileURL(path.join(base,'dist/config.js')));
  const raw = get('plugins.entries.openviking.config'), toolConfig = get('tools');
  // openclaw >= 2026.7.1 masks secret fields (apiKey) in `config get` output.
  // The live key lives in the Gateway config file this adapter is pointed at;
  // keep every non-secret field exactly as the CLI resolved it.
  const fileEntry = JSON.parse(fs.readFileSync(process.env.OPENCLAW_CONFIG_PATH || '/home/node/.openclaw/openclaw.json', 'utf8'))
      .plugins?.entries?.openviking?.config;
  if (fileEntry?.apiKey && fileEntry.apiKey !== raw.apiKey) raw.apiKey = fileEntry.apiKey;
  const cfg = memoryOpenVikingConfigSchema.parse(raw);
  const {createSpiffeTransport} = await import(pathToFileURL(path.join(base,'dist/argus-spiffe/transport.mjs')));
  const {OpenVikingClient} = await import(pathToFileURL(path.join(base,'dist/client.js')));
  const {createOpenVikingSessionRoutingRuntime} = await import(pathToFileURL(path.join(base,'dist/plugin/openviking-session-routing-runtime.js')));
  transport = createSpiffeTransport();
  const onlyTools = values => Array.isArray(values) && [...values].sort().join(',') === 'memory_recall,memory_store';
  if (get('plugins.slots.contextEngine') !== 'openviking' || cfg.mode !== 'remote' || !cfg.apiKey
      || cfg.autoCapture !== false || cfg.autoRecall !== false || !onlyTools(cfg.enabledTools)
      || !onlyTools(toolConfig.allow) || toolConfig.deny?.some(t => ['*','memory_recall','memory_store'].includes(t))
      || cfg.recallResources || cfg.recallTargetTypes.length !== 1 || cfg.recallTargetTypes[0] !== 'user'
      || cfg.accountId !== spec.account_id || cfg.userId !== spec.user_id
      || cfg.baseUrl.replace(/\/+$/, '') !== transport.config.origin
      || transport.config.clientSpiffeId !== spec.client_spiffe_id || transport.config.serverSpiffeId !== spec.server_spiffe_id) {
    throw new Error('CONTINUOUS_PROTOCOL_MISMATCH');
  }
  const routing = createOpenVikingSessionRoutingRuntime({peerPrefix:cfg.peer_prefix, logFindRequests:false, logger:{info(){}}});
  const actor = routing.resolvePluginSessionRouting({agentId:spec.agent_id, sessionKey:spec.session_key}).agentId;
  // The upstream constructor takes a fetch-like adapter in options; its patched default
  // uses the same fixed-origin SPIFFE transport when no adapter override is provided.
  const client = new OpenVikingClient(cfg.baseUrl,cfg.apiKey,actor,30000,cfg.accountId,cfg.userId);
  const scope = {account_id:cfg.accountId,user_id:cfg.userId,actor_peer_id:actor,
    client_spiffe_id:transport.config.clientSpiffeId,server_spiffe_id:transport.config.serverSpiffeId};
  let result;
  if (spec.action === 'preflight') {
    const agentsConfig = get('agents');
    const agentConfig = (agentsConfig.list ?? []).find(v=>v.id === spec.agent_id);
    const selectedModel = agentConfig?.model ?? agentsConfig.defaults?.model;
    const configuredModel = typeof selectedModel === 'string' ? selectedModel : selectedModel?.primary;
    if (spec.expected_model && configuredModel !== spec.expected_model) throw new Error('MODEL_CONFIGURATION_MISMATCH');
    const headers = {'X-API-Key':cfg.apiKey,'X-OpenViking-Account':cfg.accountId,'X-OpenViking-User':cfg.userId};
    const response = await transport(new URL('/health',cfg.baseUrl),{headers});
    const health = await response.json();
    if (!response.ok || health.auth_mode !== 'api_key' || health.role !== 'user'
        || health.account_id !== cfg.accountId || health.user_id !== cfg.userId) throw new Error('BUSINESS_IDENTITY_MISMATCH');
    // Config observations do not establish the provider's effective sampling.
    // Export only bounded scalar sampling fields, never arbitrary model params.
    const samplingKeys=['temperature','topP','top_p','topK','top_k','seed'];
    const samplingSources=[['agents.defaults.models[selected].params',agentsConfig.defaults?.models?.[configuredModel]?.params],
      ['agents.list[selected].params',agentConfig?.params]];
    const configuredSampling=samplingSources.map(([source,values])=>({source,values:Object.fromEntries(samplingKeys
      .filter(k=>typeof values?.[k]==='number' && Number.isFinite(values[k])).map(k=>[k,values[k]]))}))
      .filter(v=>Object.keys(v.values).length);
    const samplingObservation={status:configuredSampling.length ? 'CONFIGURED_NOT_EFFECTIVE_VERIFIED' : 'PROVIDER_DEFAULT_UNVERIFIED',
      configured_sources:configuredSampling,effective_parameters:'UNKNOWN',
      scope:'read-only configured values; provider application and defaults are not attested'};
    result = {explicit_tools:true,config_sha256:hash(JSON.stringify({...raw,apiKey:'[REDACTED]',tools:toolConfig})),
      configured_model:configuredModel ?? null, model_config_sha256:hash(JSON.stringify(agentsConfig)),
      sampling_observation:samplingObservation,
      adapter_sha256:hash(fs.readFileSync(path.join(base,'dist/argus-spiffe/task-audit.mjs')))};
  } else if (spec.action === 'seed') {
    if (spec.memory_policy) {
      const headers = {'X-API-Key':cfg.apiKey,'X-OpenViking-Account':cfg.accountId,
        'X-OpenViking-User':cfg.userId,'X-OpenViking-Actor-Peer':actor,'Content-Type':'application/json'};
      const response = await transport(new URL('/api/v1/sessions',cfg.baseUrl),{
        method:'POST',headers,body:JSON.stringify({session_id:spec.ov_session_id,memory_policy:spec.memory_policy})
      });
      const created = await response.json();
      if (!response.ok || created.status !== 'ok' || created.result?.session_id !== spec.ov_session_id) {
        throw new Error('SESSION_CREATE_FAILED');
      }
    }
    await client.addSessionMessage(spec.ov_session_id,'user',[{type:'text',text:spec.text}],actor);
    const commit = await client.commitSession(spec.ov_session_id,{wait:true,agentId:actor,keepRecentCount:0});
    result = {commit,ov_session_id:spec.ov_session_id};
  } else if (spec.action === 'inspect') {
    const session = await client.getSession(spec.ov_session_id,actor);
    const context = await client.getSessionContext(spec.ov_session_id,128000,actor);
    const task = spec.extraction_task_id ? await client.getTask(spec.extraction_task_id,actor) : null;
    const archiveIds = new Set((context.pre_archive_abstracts ?? []).map(a => a.archive_id));
    if (spec.archive_id) archiveIds.add(spec.archive_id);
    const archives = [];
    for (const id of archiveIds) archives.push(await client.getSessionArchive(spec.ov_session_id,id,actor));
    result = {session,task,archives,context};
  } else if (spec.action === 'find') {
    const found = await client.find(spec.query,{targetUri:'viking://user/memories',limit:20},actor);
    const memories = [];
    for (const item of found.memories ?? []) if (item.level === 2) memories.push({uri:item.uri,content:await client.read(item.uri,actor)});
    result = {memories};
  } else if (spec.action === 'agent') {
    // Actual CLI submission boundary, separate from controller queue/intent and
    // from transport, receiver, persistence or model-provider consumption.
    inputRelease = {source:'gateway_agent_dispatch', boundary:'openclaw_cli_input',
      task_id:spec.task_id, fact_id:spec.fact_id, work_item_id:spec.work_item_id ?? null,
      session_key:spec.session_key, prompt_sha256:hash(spec.prompt), at_ms:Date.now(),
      clock_domain:'gateway_host_realtime'};
    const rawResult = execFileSync('openclaw',['agent','--agent',spec.agent_id,'--session-key',spec.session_key,
      '--message',spec.prompt,'--timeout',String(spec.timeout_seconds),'--json'],
      {encoding:'utf8',timeout:(spec.timeout_seconds+20)*1000,maxBuffer:16*1024*1024,stdio:['ignore','pipe','pipe']});
    const response = JSON.parse(rawResult);
    if (response.status !== 'ok' || !response.runId || !Array.isArray(response.result?.payloads)) throw new Error('GATEWAY_RESULT_INVALID');
    result = {answer:response.result.payloads.filter(v=>typeof v.text==='string').map(v=>v.text).join('\n').trim(),
      gateway_run_id:response.runId,model:response.result.meta?.agentMeta?.model ?? null,
      provider:response.result.meta?.agentMeta?.provider ?? null,usage:response.result.meta?.agentMeta?.usage ?? null};
  } else throw new Error('INVALID_ACTION');
  console.log(JSON.stringify({result:'OBSERVED',scope,...result,...(inputRelease ? {input_release:inputRelease} : {})}));
} catch (error) {
  // These spawn errors mean the CLI never received its input. A running CLI's
  // timeout/failure retains submission evidence; an outer Docker loss does not.
  if (['ENOENT','EACCES'].includes(error.code)) inputRelease = undefined;
  const known = ['PINNED_PLUGIN_AMBIGUOUS','TASK_AUDIT_MISSING','TYPED_PROPOSAL_AUDIT_MISSING','CONTINUOUS_PROTOCOL_MISMATCH','BUSINESS_IDENTITY_MISMATCH','MODEL_CONFIGURATION_MISMATCH','SESSION_CREATE_FAILED','GATEWAY_RESULT_INVALID','INVALID_ACTION'];
  console.log(JSON.stringify({result:'UNKNOWN',code:known.includes(error.message) ? error.message : 'GATEWAY_IO_FAILED',
    ...(inputRelease ? {input_release:inputRelease} : {})}));
  process.exitCode = 1;
} finally { transport?.close(); }
