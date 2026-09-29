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
      taskEvent('tool_started', {input_facts:frameReceipts(JSON.stringify(params)), input_sha256:digest(JSON.stringify(params)),
        ov_session_id:typeof params?.sessionId === 'string' ? params.sessionId : null});
      try {
        const result = await tool.execute.call(tool, toolCallId, params, ...rest);
        const details = result?.details ?? {};
        taskEvent('tool_completed', {details:Object.fromEntries(['action','sessionId','status','taskId','memoriesCount','archived','count','total']
          .filter(k => details[k] !== undefined).map(k => [k, details[k]])),
          output_sha256:digest(JSON.stringify(result)), output_facts:frameReceipts(JSON.stringify(result))});
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
