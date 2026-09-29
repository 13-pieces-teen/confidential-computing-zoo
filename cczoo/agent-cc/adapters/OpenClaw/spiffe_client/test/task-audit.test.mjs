import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {auditToolFactory,currentTask,taskHeaders,taskEvent,frameReceipts,sessionScope} from '../lib/task-audit.mjs';

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
