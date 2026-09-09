import test from 'node:test';
import assert from 'node:assert/strict';
import {auditAssembly, auditRecallSource, factHashes, recordRecallRequest} from '../lib/recall-audit.mjs';

test('concurrent recall audit links each request to its own context without changing messages', async () => {
  const lines = [];
  const original = console.error;
  console.error = line => lines.push(JSON.parse(line));
  try {
    const facts = ['ARGUS_FACT_'+'A'.repeat(32), 'ARGUS_FACT_'+'B'.repeat(32)];
    await Promise.all(facts.map(async (fact, index) => {
      const output = {messages:[{role:'user', content:fact}]};
      const result = await auditAssembly(async () => {
        await new Promise(resolve => setTimeout(resolve, 5-index));
        const receipt = {request_id:'request-'+index,http_status:200};
        recordRecallRequest(receipt);
        assert.ok(receipt.context_span_id);
        auditRecallSource(fact);
        return output;
      }, {messages:[{role:'user',content:'lookup without answer'}], sessionKey:'session-'+index});
      assert.equal(result, output);
    }));
    assert.equal(lines.length, 2);
    for (const [index, fact] of facts.entries()) {
      const line = lines.find(l => l.session_key === 'session-'+index);
      assert.deepEqual(line.input_fact_hashes, []);
      assert.deepEqual(line.recall_fact_hashes, factHashes(fact));
      assert.deepEqual(line.output_fact_hashes, factHashes(fact));
      assert.deepEqual(line.request_ids, ['request-'+index]);
      assert.ok(!JSON.stringify(line).includes(fact));
    }
  } finally { console.error = original; }
});
