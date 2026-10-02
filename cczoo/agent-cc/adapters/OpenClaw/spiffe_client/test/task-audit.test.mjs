import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {auditToolFactory,currentTask,taskHeaders,taskEvent,frameReceipts,proposalReceipts,sessionScope} from '../lib/task-audit.mjs';

function frame() {
  const prefix='ARGUS_FACT_V1|id='+'a'.repeat(32)+'|project='+'b'.repeat(12)+'|chain='+'c'.repeat(12)+'|ref='+'d'.repeat(32)+'|amount=00004000';
  return prefix+'|sha256='+createHash('sha256').update(prefix).digest('hex')+'|END_ARGUS_FACT';
}

test('concurrent real tool wrappers keep task headers separate and preserve values',async()=>{
  const lines=[]; const original=console.error; console.error=line=>lines.push(JSON.parse(line));
  try {
    const factory=auditToolFactory(ctx=>({name:'memory_store',async execute(id,params){
      await new Promise(resolve=>setTimeout(resolve,ctx.delay));
      assert.equal(currentTask().client_id,ctx.client);
      assert.equal(taskHeaders()['x-argus-tool-call-id'],id);
      taskEvent('request_attempted',{request_id:'request-'+ctx.client});
      return params.result;
    }}));
    const values=await Promise.all(['alice','bob'].map((client,i)=>{
      const result={details:{status:'completed',memoriesCount:1}};
      return factory({sessionKey:`argus-e4:run:${client}:s00:1`,client,delay:5-i}).execute('call-'+client,{text:frame(),result})
        .then(value=>{assert.equal(value,result);return value;});
    }));
    assert.equal(values.length,2); assert.equal(currentTask(),undefined);
    for(const client of ['alice','bob']) {
      const request=lines.find(v=>v.request_id==='request-'+client);
      assert.equal(request.client_id,client);
      assert.equal(request.tool_call_id,'call-'+client);
    }
    assert.ok(!JSON.stringify(lines).includes(frame()));
    assert.ok(!JSON.stringify(lines).includes('d'.repeat(32)));
  } finally {console.error=original;}
});

test('ordinary sessions are unchanged and incomplete/tampered facts are not accepted',async()=>{
  assert.equal(sessionScope('ordinary-session'),null);
  const result={ok:true};
  const tool=auditToolFactory(()=>({execute:()=>result}))({sessionKey:'ordinary'});
  assert.equal(await tool.execute('id',{}),result);
  assert.equal(frameReceipts(frame()).length,1);
  assert.equal(frameReceipts(frame().slice(0,-1)).length,0);
  assert.equal(frameReceipts(frame().replace('00004000','00005000')).length,0);
});

test('typed proposal receipts validate schema and retain hashes without raw values',async()=>{
  const proposal={schema:'argus.proposal.v1',work_item_id:'trip-'+'a'.repeat(24),fact_id:'b'.repeat(32),
    step_index:2,constraint_key:'budget_cents',unit:'cent',value:12345};
  const text='ARGUS_PROPOSAL_V1\n'+JSON.stringify(proposal)+'\nEND_ARGUS_PROPOSAL';
  const canonical=JSON.stringify(Object.fromEntries(Object.keys(proposal).sort().map(k=>[k,proposal[k]])));
  const receipt=proposalReceipts({parts:[{text}]})[0];
  assert.deepEqual(proposalReceipts({content:[{type:'text',text:JSON.stringify({memories:[{content:text}]})}]}),[receipt]);
  assert.equal(receipt.proposal_sha256,createHash('sha256').update(canonical).digest('hex'));
  assert.equal(receipt.step_index,2);
  assert.ok(!JSON.stringify(receipt).includes('12345'));
  for(const key of ['unit','step_index','constraint_key']) {
    const bad={...proposal}; delete bad[key];
    assert.deepEqual(proposalReceipts('ARGUS_PROPOSAL_V1 '+JSON.stringify(bad)+' END_ARGUS_PROPOSAL'),[]);
  }
  assert.deepEqual(proposalReceipts(text.replace('"cent"','"minute"')),[]);
  assert.deepEqual(proposalReceipts(text.replace('{','{"step_index":1,')),[]);
  const lines=[],original=console.error; console.error=line=>lines.push(JSON.parse(line));
  try {
    const result={content:[{type:'text',text}]};
    const tool=auditToolFactory(()=>({name:'memory_recall',execute:async()=>result}))({sessionKey:'argus-e4:r:a:s02:1'});
    assert.equal(await tool.execute('call',{text}),result);
    assert.deepEqual(lines[0].input_proposals,[receipt]);
    assert.deepEqual(lines[1].output_proposals,[receipt]);
    assert.ok(!JSON.stringify(lines).includes('12345'));
  } finally {console.error=original;}
});
