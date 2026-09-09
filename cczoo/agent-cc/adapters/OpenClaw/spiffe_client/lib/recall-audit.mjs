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
export function auditRecallSource(block) {
  const span = spans.getStore();
  if (span) span.recall = factHashes(block);
}
export async function auditAssembly(assemble, params) {
  const span = {id: randomUUID(), requests: [], recall: []};
  return spans.run(span, async () => {
    const before = factHashes(params.messages);
    const result = await assemble(params);
    // Only synthetic E2E fact hashes, never general conversation content.
    if (span.recall.length) console.error(JSON.stringify({
      component: 'argus-openclaw-recall', context_span_id: span.id,
      session_id: params.sessionId, session_key: params.sessionKey,
      input_fact_hashes: before, recall_fact_hashes: span.recall,
      output_fact_hashes: factHashes(result.messages), request_ids: span.requests,
      checked_at: new Date().toISOString(),
    }));
    return result;
  });
}
