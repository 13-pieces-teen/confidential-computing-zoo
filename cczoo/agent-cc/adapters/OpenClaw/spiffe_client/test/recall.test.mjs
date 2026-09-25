import test from 'node:test';
import assert from 'node:assert/strict';
import {auditAssembly, auditRecallSource, auditRecallSearch, factHashes, recordRecallRequest} from '../lib/recall-audit.mjs';

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
    assert.equal(lines.length, 4);
    for (const [index, fact] of facts.entries()) {
      const line = lines.find(l => l.session_key === 'session-'+index && l.event === 'completed');
      assert.deepEqual(line.input_fact_hashes, []);
      assert.deepEqual(line.recall_fact_hashes, factHashes(fact));
      assert.deepEqual(line.output_fact_hashes, factHashes(fact));
      assert.deepEqual(line.request_ids, ['request-'+index]);
      assert.ok(!JSON.stringify(line).includes(fact));
    }
  } finally { console.error = original; }
});

test('empty and failed assemblies are observable without leaking text or error details', async () => {
  const lines = []; const original = console.error;
  console.error = line => lines.push(JSON.parse(line));
  try {
    const privateText = 'ordinary private user data';
    await auditAssembly(async () => {
      auditRecallSearch([], [{status:'fulfilled'}]);
      auditRecallSource(undefined, 0);
      return {messages:[{content:privateText}]};
    }, {messages:[{content:privateText}], sessionKey:'empty'});
    await assert.rejects(auditAssembly(async () => { throw new Error(privateText); }, {messages:[], sessionKey:'failed'}));
    const empty = lines.find(v => v.session_key === 'empty' && v.event === 'completed');
    assert.equal(empty.search.candidate_count, 0);
    assert.equal(empty.source_observed, true);
    assert.equal(empty.memory_count, 0);
    assert.ok(lines.some(v => v.session_key === 'failed' && v.event === 'failed'));
    assert.ok(!JSON.stringify(lines).includes(privateText));
  } finally { console.error = original; }
});
