// Observe the real ContextEngine boundary. Never inject facts or alter messages.
import { AsyncLocalStorage } from 'node:async_hooks';
import { createHash, randomUUID } from 'node:crypto';

const spans = new AsyncLocalStorage();
export function factHashes(value) {
  const text = typeof value === 'string' ? value : JSON.stringify(value);
  return [...new Set((text?.match(/ARGUS_FACT_[A-F0-9]{32}/g) ?? [])
    .map(fact => createHash('sha256').update(fact).digest('hex')))].sort();
}
export function recordRecallRequest(receipt) {
  const span = spans.getStore();
  if (!span) return;
  receipt.context_span_id = span.id;
  if (receipt.http_status >= 200 && receipt.http_status < 300 && span.requests.length < 128) span.requests.push(receipt.request_id);
}
export function auditRecallSearch(items, settled) {
  const span = spans.getStore();
  if (!span) return;
  span.search = {
    candidate_count: items.length,
    leaf_count: items.filter(item => item.level === 2).length,
    search_count: settled.length,
    search_failed_count: settled.filter(item => item.status === 'rejected').length,
    candidate_fact_hashes: factHashes(items.map(item => item.abstract ?? item.overview ?? '')),
  };
}
export function auditRecallSource(block, memoryCount) {
  const span = spans.getStore();
  if (span) {
    span.recall = factHashes(block);
    span.memoryCount = memoryCount ?? (block ? 1 : 0);
    span.sourceObserved = true;
  }
}
export async function auditAssembly(assemble, params) {
  const span = {id: randomUUID(), requests: [], recall: []};
  return spans.run(span, async () => {
    const before = factHashes(params.messages);
    const base = {
      component: 'argus-openclaw-recall', context_span_id: span.id,
      session_id: params.sessionId, session_key: params.sessionKey,
    };
    console.error(JSON.stringify({...base, event: 'started', checked_at: new Date().toISOString()}));
    try {
      const result = await assemble(params);
      // Counts and synthetic E2E hashes only; no ordinary text, URI or error body.
      console.error(JSON.stringify({...base, event: 'completed',
        input_fact_hashes: before, recall_fact_hashes: span.recall,
        output_fact_hashes: factHashes(result.messages), request_ids: span.requests,
        source_observed: !!span.sourceObserved, memory_count: span.memoryCount ?? null,
        search: span.search ?? null, checked_at: new Date().toISOString(),
      }));
      return result;
    } catch (error) {
      console.error(JSON.stringify({...base, event: 'failed', request_ids: span.requests,
        checked_at: new Date().toISOString()}));
      throw error;
    }
  });
}
