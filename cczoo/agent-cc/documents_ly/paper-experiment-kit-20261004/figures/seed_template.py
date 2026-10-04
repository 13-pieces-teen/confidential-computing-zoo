"""Create empty observation slots once; never overwrite results."""
from pathlib import Path
import json

ROOT=Path(__file__).resolve().parent
def slot(**kw):
    return dict(status='pending',run_id=None,evidence_ref=None,
                environment_id=None,runtime_revision=None,
                evidence_kind=kw.pop('evidence_kind','controlled_prototype'),**kw)

data={
 'schema':'argus.paper-figures.v2',
 'design':{'version':'2026-10-04-controlled-v2',
           'evidence_mode':'controlled_prototype','paper_sync':'NOT_APPLIED',
           'planned_pairs':3,'task_steps':6,'purpose':'unfilled manuscript templates'},
 'admission':[
   slot(pair=p,arm=a,stage=s,decision=None,first_layer=None,business_access=None,
        current_checks=None,record_state=None)
   for p in range(1,4) for a in ('full','native') for s in ('A','B','C')],
 'history':[
   slot(evidence_kind='offline',case=c,source_kind=None,verdict=None,record_count=None,record_bytes=None,verify_ms=None)
   for c in ('legal_history','unrelated_activity','ineligible_launch')],
 'receiver':[
   slot(pair=p,fault=f,arm=a,coverage=None,backend_alive=None,
        detected_s=None,closed_s=None,closure_observation=None,window_end_s=None,
        new_facts_read=None,late_pre_fault_facts=None,ambiguous_facts=None,trace=[])
   for f,arms in (('config',('full','no_close')),('freeze',('full','no_watchdog')))
   for p in range(1,4) for a in arms],
 'tasks':[
   slot(pair=p,condition=c,confirmed_proposals=None,confirmed_readback=None,
        continuation=None,complete_task=None,readback_status=None,
        read_s=None,continue_s=None,
        steps=[{'step':i,'outcome':None,'phase':None,'proposal_state':None} for i in range(1,7)])
   for p in range(1,4) for c in ('healthy','recovery')],
 'cost':[
   slot(pair=p,arm=a,connection=c,p50_ms=None,p95_ms=None,attempted=None,
        valid=None,rejected=None,failed=None,timed_out=None,invalid=None,
        duration_s=None,cpu_one_core_pct=None,rss_mib=None,component_scope=None)
   for p in range(1,4) for a in ('full','native') for c in ('new','reuse')],
 'reuse':[
   slot(arm=a,window_s=None,node_evidence_requests=None,workload_evidence_requests=None,subscriptions=None,
        node_svids=None,workload_svids=None,new_admissions=None)
   for a in ('full','native')],
 'stage_costs':[]
}
path=ROOT/'results.json'
with path.open('x',encoding='utf-8',newline='\n') as f:
    json.dump(data,f,ensure_ascii=False,indent=2)
    f.write('\n')
print('Created empty result slots: '+str(path))
