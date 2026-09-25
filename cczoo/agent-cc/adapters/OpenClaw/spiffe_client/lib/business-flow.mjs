// Acceptance orchestration only. It never creates memories or changes retrieval.
export class BusinessFailure extends Error {
  constructor(code) { super(code); this.code = code; }
}
const stop = code => { throw new BusinessFailure(code); };
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

export function transientReadError(error) {
  let found = false;
  let current = error;
  for (let depth = 0; current && depth < 5; current = current.cause, depth++) {
    const transient = ['ECONNRESET', 'EPIPE', 'UND_ERR_SOCKET'].includes(current.code);
    // A nested socket error cannot override an outer TLS/policy failure.
    if ((current.code && !transient) || /SPIFFE|SVID|certificate|credential|identity|policy|permission|TLS/i.test(current.message ?? '')) return false;
    if (transient || current.message === 'socket hang up') found = true;
  }
  return !current && found;
}
export async function readOnceRetry(read) {
  try { return await read(); }
  catch (error) { if (!transientReadError(error)) throw error; return read(); }
}
export function extractionCounts(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  if (Object.values(value).some(count => !Number.isSafeInteger(count) || count < 0)) return null;
  return {by_category: value, total: Object.values(value).reduce((a, b) => a + b, 0)};
}
function commitReceipt(value) {
  return {status: value.status, task_id: value.task_id ?? null, archived: value.archived ?? null,
    memories_extracted: value.memories_extracted ?? null};
}

export async function runBusinessFlow({client, listSessions, marker, scope, previous = null,
  resume = false, captureAttempts = 30, commitAttempts = 60, sleep = wait, emit = async () => {}}) {
  const state = previous ? structuredClone(previous) : {schema_version: 1, scope};
  const save = async (stage, code) => {
    Object.assign(state, {stage, code, checked_at: new Date().toISOString()});
    await emit(structuredClone(state));
  };
  try {
    for (const n of [captureAttempts, commitAttempts]) {
      if (!Number.isSafeInteger(n) || n < 1 || n > 3600) stop('INVALID_ATTEMPTS');
    }
    if (previous && JSON.stringify(previous.scope) !== JSON.stringify(scope)) stop('RESUME_SCOPE_MISMATCH');
    if (previous && !resume) stop('RESUME_REQUIRED');
    // A lost POST response is ambiguous. Resume must never submit another POST.
    if (resume && !state.task_id && state.commit?.status !== 'completed') stop('COMMIT_OUTCOME_UNKNOWN');
    if (!resume) {
      await save('capture', 'RUNNING');
      for (let attempt = 0; attempt < captureAttempts && !state.session_id; attempt++) {
        const sessions = await readOnceRetry(listSessions);
        if (!Array.isArray(sessions)) stop('SESSION_RESPONSE_INVALID');
        const recent = [...sessions].sort((a, b) => String(b.mod_time ?? '').localeCompare(String(a.mod_time ?? ''))).slice(0, 100);
        for (const session of recent) {
          const id = String(session.session_id ?? '');
          if (!id || id.startsWith('memory-store-')) continue;
          let context;
          try { context = await readOnceRetry(() => client.getSessionContext(id, 128000, scope.actor_peer_id)); }
          catch (error) {
            if (/\[NOT_FOUND\]|HTTP 404/.test(error.message ?? '')) continue;
            throw error;
          }
          if (JSON.stringify(context).includes(marker)) { state.session_id = id; break; }
        }
        if (!state.session_id && attempt + 1 < captureAttempts) await sleep(2000);
      }
      if (!state.session_id) stop('CAPTURE_FAILED');
      state.plugin_readback = true;
      await save('commit', 'COMMIT_SUBMITTING');
      // Exactly one submission; all subsequent work is GET polling.
      let commit;
      try { commit = await client.commitSession(state.session_id, {wait: false, keepRecentCount: 0, agentId: scope.actor_peer_id}); }
      catch { stop('COMMIT_OUTCOME_UNKNOWN'); }
      state.commit = commitReceipt(commit);
      state.task_id = commit.task_id ?? null;
      await save('extraction', 'RUNNING');
    }
    let final = state.commit;
    if (state.task_id) {
      final = null;
      for (let attempt = 0; attempt < commitAttempts; attempt++) {
        const task = await readOnceRetry(() => client.getTask(state.task_id, scope.actor_peer_id));
        state.task = {task_id: state.task_id, status: task.status,
          extraction: extractionCounts(task.result?.memories_extracted)};
        await save('extraction', 'RUNNING');
        if (task.status === 'failed') stop('EXTRACTION_FAILED');
        if (task.status === 'completed') {
          final = {status: task.status, memories_extracted: task.result?.memories_extracted}; break;
        }
        if (!['pending', 'running', 'queued', 'processing', 'accepted'].includes(task.status)) stop('TASK_STATUS_UNKNOWN');
        if (attempt + 1 < commitAttempts) await sleep(5000);
      }
      if (!final) stop('EXTRACTION_TIMEOUT');
    }
    if (final?.status === 'failed') stop('EXTRACTION_FAILED');
    if (final?.status !== 'completed') stop('EXTRACTION_STATUS_UNKNOWN');
    state.extraction = extractionCounts(final.memories_extracted);
    if (!state.extraction) stop('EXTRACTION_RESULT_UNKNOWN');
    // Even a completed task with {} may have successfully archived the session.
    const detail = await readOnceRetry(() => client.getSession(state.session_id, scope.actor_peer_id));
    const context = await readOnceRetry(() => client.getSessionContext(state.session_id, 128000, scope.actor_peer_id));
    state.commit_count = detail.commit_count ?? 0;
    state.archive = state.commit_count > 0 && !!String(context.latest_archive_overview ?? '').trim();
    if (!state.archive) stop('ARCHIVE_FAILED');
    if (state.extraction.total === 0) stop('EXTRACTION_EMPTY');
    await save('processing', 'PASSED');
    return state;
  } catch (error) {
    const code = error instanceof BusinessFailure ? error.code : 'BUSINESS_IO_FAILED';
    await save(state.stage ?? 'processing', code);
    throw new BusinessFailure(code);
  }
}
