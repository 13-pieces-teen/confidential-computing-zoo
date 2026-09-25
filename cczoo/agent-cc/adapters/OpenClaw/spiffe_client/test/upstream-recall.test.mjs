import test from 'node:test';
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
import {join} from 'node:path';

test('fixed upstream search and leaf read retain behavior while emitting empty and populated audit',
  {skip:!process.env.ARGUS_TEST_PLUGIN_DIR}, async () => {
    const load = name => import(pathToFileURL(join(process.env.ARGUS_TEST_PLUGIN_DIR, 'dist', name)));
    const [{buildAutoRecallContext}, {memoryOpenVikingConfigSchema}, audit, routing] = await Promise.all([
      load('auto-recall.js'), load('config.js'), load('argus-spiffe/recall-audit.mjs'), load('plugin/openviking-session-routing-runtime.js'),
    ]);
    const cfg=memoryOpenVikingConfigSchema.parse({peer_prefix:'test', recallTargetTypes:['user'], recallPreferAbstract:false});
    const actor=routing.createOpenVikingSessionRoutingRuntime({peerPrefix:cfg.peer_prefix,logFindRequests:false,logger:{info(){}}})
      .resolvePluginSessionRouting({agentId:'worker',sessionKey:'unique'}).agentId;
    assert.equal(actor,'test_worker');
    const lines=[]; const original=console.error; console.error=line=>lines.push(JSON.parse(line));
    try {
      for (const populated of [false,true]) {
        let reads=0;
        const fact='ARGUS_FACT_'+'C'.repeat(32);
        const client={healthCheck:async()=>({}),find:async (_query,opts)=> {
          assert.equal(opts.actorPeerId,actor);
          return {memories: populated ? [{uri:'viking://user/memories/fact',level:2,score:0.99,abstract:'project memory'}] : []};
        },read:async (_uri,peer)=>{assert.equal(peer,actor); reads++; return fact;}};
        const result=await audit.auditAssembly(async()=>{
          const recall=await buildAutoRecallContext({cfg,client,agentId:actor,queryText:'Retrieve project check code',logger:{info(){}},sessionKey:String(populated)});
          audit.auditRecallSource(recall.block,recall.memoryCount);
          return {messages:recall.block ? [{content:recall.block}] : []};
        },{messages:[],sessionKey:String(populated)});
        const done=lines.find(v=>v.event==='completed'&&v.session_key===String(populated));
        assert.equal(done.search.candidate_count,populated?1:0);
        assert.equal(done.memory_count,populated?1:0);
        assert.equal(reads,populated?1:0);
        assert.deepEqual(audit.factHashes(result.messages),populated?audit.factHashes(fact):[]);
      }
    } finally {console.error=original;}
  });
