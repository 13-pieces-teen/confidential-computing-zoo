// Passive scope for explicit E4 tool calls. Never changes tool input or result.
import { AsyncLocalStorage } from 'node:async_hooks';
import { createHash } from 'node:crypto';

const scopes = new AsyncLocalStorage();
const digest = text => createHash('sha256').update(text).digest('hex');
const framePattern = /ARGUS_FACT_V1\|id=([a-f0-9]{32})\|project=[a-f0-9]{12}\|chain=[a-f0-9]{12}\|ref=[a-f0-9]{32}\|amount=[0-9]{8}\|sha256=([a-f0-9]{64})\|END_ARGUS_FACT/g;

export function frameReceipts(text) {
  if (typeof text !== 'string') return [];
  return [...text.matchAll(framePattern)].filter(match => digest(match[0].split('|sha256=')[0]) === match[2])
    .map(match => ({fact_id:match[1], full_fact_sha256:digest(match[0]), fact_bytes:match[0].length}));
}
export function proposalReceipts(value) {
  const units={budget_cents:'cent',max_walk_minutes:'minute',max_travel_minutes:'minute',min_indoor_stops:'count'};
  const keys=['constraint_key','fact_id','schema','step_index','unit','value','work_item_id'];
  const receipts=new Map();
  const visit=(item,depth=0)=>{
    if(depth>16) return;
    if(typeof item==='string') {
      for(const match of item.matchAll(/ARGUS_PROPOSAL_V1\s*(\{[^{}]{1,2048}\})\s*END_ARGUS_PROPOSAL/g)) {
        try {
          const p=JSON.parse(match[1]);
          // This bounded schema has no nested values or escaped field names.
          const names=[...match[1].matchAll(/"([a-z_]+)"\s*:/g)].map(m=>m[1]);
          if(names.length!==keys.length || new Set(names).size!==keys.length
             || Object.keys(p).sort().join(',')!==keys.join(',') || p.schema!=='argus.proposal.v1'
             || !/^trip-[a-f0-9]{24}$/.test(p.work_item_id) || !/^[a-f0-9]{32}$/.test(p.fact_id)
             || !Number.isInteger(p.step_index) || p.step_index<0 || p.step_index>=6
             || !Object.hasOwn(units,p.constraint_key) || units[p.constraint_key]!==p.unit
             || !Number.isInteger(p.value) || p.value<0 || p.value>=100000000) continue;
          const sha=digest(JSON.stringify(Object.fromEntries(keys.map(k=>[k,p[k]]))));
          receipts.set(sha,{fact_id:p.fact_id,work_item_id:p.work_item_id,step_index:p.step_index,proposal_sha256:sha});
        } catch { /* malformed memory is not a confirmed typed input */ }
      }
      // Tool content may be a JSON-encoded list of recalled records. Inspect
      // its decoded text as well, without logging or changing that result.
      if(item.length<=1024*1024 && /^[\s]*[\[{]/.test(item)) {
        try { visit(JSON.parse(item),depth+1); } catch { /* ordinary text */ }
      }
    } else if(Array.isArray(item)) item.forEach(v=>visit(v,depth+1));
    else if(item && typeof item==='object') Object.values(item).forEach(v=>visit(v,depth+1));
  };
  visit(value);
  return [...receipts.values()];
}
export function sessionScope(sessionKey) {
  const match = /^argus-e4:([A-Za-z0-9_.-]{1,100}):([A-Za-z0-9_.-]{1,100}):([A-Za-z0-9_.-]{1,100}):([1-9][0-9]*)$/.exec(sessionKey ?? '');
  return match ? {run_id:match[1], client_id:match[2], step_id:match[3], task_id:match[3], attempt:Number(match[4]), session_key:sessionKey} : null;
}
export function currentTask() { return scopes.getStore(); }
export function taskHeaders(scope = currentTask()) {
  return scope ? {'x-argus-run-id':scope.run_id, 'x-argus-client-id':scope.client_id,
    'x-argus-task-id':scope.step_id, 'x-argus-attempt':String(scope.attempt),
    'x-argus-tool-call-id':scope.tool_call_id ?? ''} : {};
}
export function taskEvent(event, fields = {}, scope = currentTask()) {
  if (!scope) return;
  console.error(JSON.stringify({component:'argus-openclaw-task', ...scope, event, ...fields, checked_at:new Date().toISOString()}));
}
export function auditCommit(result, sessionId) {
  taskEvent('commit_receipt', {ov_session_id:sessionId, task_id:currentTask()?.step_id,
    extraction_task_id:result?.task_id ?? null, commit_status:result?.status ?? null,
    archived:result?.archived ?? null,
    archive_id:typeof result?.archive_uri === 'string' ? result.archive_uri.replace(/\/+$/, '').split('/').pop() : null});
  return result;
}
function wrapTool(tool, ctx) {
  if (!tool || typeof tool.execute !== 'function') return tool;
  return {...tool, execute:async function(toolCallId, params, ...rest) {
    const scope = sessionScope(ctx?.sessionKey);
    if (!scope) return tool.execute.call(tool, toolCallId, params, ...rest);
    return scopes.run({...scope, tool_call_id:String(toolCallId), tool_name:tool.name}, async () => {
      taskEvent('tool_started', {input_facts:frameReceipts(JSON.stringify(params)), input_proposals:proposalReceipts(params), input_sha256:digest(JSON.stringify(params)),
        ov_session_id:typeof params?.sessionId === 'string' ? params.sessionId : null});
      try {
        const result = await tool.execute.call(tool, toolCallId, params, ...rest);
        const details = result?.details ?? {};
        taskEvent('tool_completed', {details:Object.fromEntries(['action','sessionId','status','taskId','memoriesCount','archived','count','total']
          .filter(k => details[k] !== undefined).map(k => [k, details[k]])),
          output_sha256:digest(JSON.stringify(result)), output_facts:frameReceipts(JSON.stringify(result)), output_proposals:proposalReceipts(result)});
        return result;
      } catch (error) { taskEvent('tool_failed', {error_code:error?.code ?? 'TOOL_FAILED'}); throw error; }
    });
  }};
}
export function auditToolFactory(factory) {
  if (typeof factory !== 'function') return factory;
  return ctx => {
    const value = factory(ctx);
    return Array.isArray(value) ? value.map(tool => wrapTool(tool, ctx)) : wrapTool(value, ctx);
  };
}
