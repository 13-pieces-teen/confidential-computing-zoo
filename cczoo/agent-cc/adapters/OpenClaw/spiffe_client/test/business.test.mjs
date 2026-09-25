import test from 'node:test';
import assert from 'node:assert/strict';
import {runBusinessFlow, extractionCounts, readOnceRetry} from '../lib/business-flow.mjs';

function fixture(tasks = [{status:'completed',result:{memories_extracted:{facts:1}}}]) {
  const events = []; const calls = [];
  const scope = {origin:'https://service.example', actor_peer_id:'test_agent', account_id:'account', user_id:'user'};
  const client = {
    getSessionContext: async (id, budget, actor) => { calls.push(['context', id, actor]); return {messages:[{text:'MARKER'}], latest_archive_overview:'archive'}; },
    getSession: async (id, actor) => { calls.push(['session', id, actor]); return {commit_count:1}; },
    commitSession: async (id, opts) => { calls.push(['commit', id, opts.agentId]); return {status:'accepted',task_id:'task',archived:true}; },
    getTask: async (id, actor) => { calls.push(['task', id, actor]); return tasks.length > 1 ? tasks.shift() : tasks[0]; },
  };
  const options = {client, listSessions: async () => [{session_id:'session'}], marker:'MARKER', scope,
    captureAttempts:1, commitAttempts:2, sleep:async()=>{}, emit:async value=>events.push(value)};
  return {options, calls, events};
}
test('archive and extraction are separate; one commit and actor scope everywhere', async () => {
  const f = fixture([{status:'running'}, {status:'completed',result:{memories_extracted:{facts:1}}}]);
  const state = await runBusinessFlow(f.options);
  assert.equal(state.code,'PASSED'); assert.equal(state.extraction.total,1);
  assert.equal(f.calls.filter(c => c[0] === 'commit').length,1);
  assert.ok(f.calls.every(c => c[2] === 'test_agent'));
  assert.equal(f.events.find(v => v.code === 'COMMIT_SUBMITTING').task_id,undefined);
});
test('completed empty extraction fails explicitly, after recording successful archive', async () => {
  const f = fixture([{status:'completed',result:{memories_extracted:{}}}]);
  await assert.rejects(runBusinessFlow(f.options), {code:'EXTRACTION_EMPTY'});
  assert.equal(f.events.at(-1).archive,true);
  assert.equal(f.events.at(-1).extraction.total,0);
  assert.equal(extractionCounts({facts:-1}),null);
});
test('poll timeout resumes the same task and never submits another commit', async () => {
  const f = fixture([{status:'running'}]);
  await assert.rejects(runBusinessFlow(f.options), {code:'EXTRACTION_TIMEOUT'});
  const previous = f.events.at(-1);
  f.options.client.getTask = async id => { assert.equal(id,'task'); return {status:'completed',result:{memories_extracted:{facts:1}}}; };
  const result = await runBusinessFlow({...f.options, previous, resume:true});
  assert.equal(result.code,'PASSED');
  assert.equal(f.calls.filter(c => c[0] === 'commit').length,1);
});
test('lost commit response cannot cause a duplicate POST on resume', async () => {
  const f = fixture(); let posts = 0;
  f.options.client.commitSession = async()=> {posts++; throw Object.assign(new Error('socket hang up'), {code:'ECONNRESET'});};
  await assert.rejects(runBusinessFlow(f.options), {code:'COMMIT_OUTCOME_UNKNOWN'});
  await assert.rejects(runBusinessFlow({...f.options, previous:f.events.at(-1), resume:true}), {code:'COMMIT_OUTCOME_UNKNOWN'});
  await assert.rejects(runBusinessFlow({...f.options, previous:null, resume:true}), {code:'COMMIT_OUTCOME_UNKNOWN'});
  assert.equal(posts,1);
});
test('failed task, missing extraction and changed scope fail without false success', async () => {
  for (const [task, code] of [[{status:'failed'},'EXTRACTION_FAILED'],[{status:'completed',result:{}},'EXTRACTION_RESULT_UNKNOWN']]) {
    const f=fixture([task]); await assert.rejects(runBusinessFlow(f.options),{code});
  }
  const f=fixture(); await runBusinessFlow(f.options);
  await assert.rejects(runBusinessFlow({...f.options,previous:f.events.at(-1),resume:true,
    scope:{...f.options.scope,user_id:'other'}}),{code:'RESUME_SCOPE_MISMATCH'});
});
test('GET retry is bounded and excludes identity, permission and business errors', async () => {
  let calls=0;
  assert.equal(await readOnceRetry(async()=>{if (++calls===1) throw Object.assign(new Error(),{code:'ECONNRESET'});return 42;}),42);
  assert.equal(calls,2);
  for (const code of ['CERT_HAS_EXPIRED','ERR_TLS_CERT_ALTNAME_INVALID','EACCES','BUSINESS_ERROR']) {
    calls=0;
    await assert.rejects(readOnceRetry(async()=>{calls++;throw Object.assign(new Error(),{code});}));
    assert.equal(calls,1);
  }
  calls=0;
  await assert.rejects(readOnceRetry(async()=>{calls++;throw Object.assign(new Error(),{code:'EPIPE'});}));
  assert.equal(calls,2);
  calls=0;
  await assert.rejects(readOnceRetry(async()=>{calls++;throw Object.assign(new Error('identity denied',
    {cause:Object.assign(new Error(),{code:'ECONNRESET'})}),{code:'ERR_TLS_CERT_ALTNAME_INVALID'});}));
  assert.equal(calls,1);
});
