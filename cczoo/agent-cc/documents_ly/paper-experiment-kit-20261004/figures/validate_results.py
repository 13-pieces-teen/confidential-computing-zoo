"""Validate observation integrity without inventing missing measurements."""
import json, math, sys
from pathlib import Path

STATUSES={'pending','recorded','unknown','not_run'}
OUTCOMES={'CORRECT','INCORRECT','REJECTED','TIMEOUT','UNKNOWN','NOT_DISPATCHED'}

def validate(d):
    errors=[]
    def need(ok,msg):
        if not ok: errors.append(msg)
    need(d.get('schema')=='argus.paper-figures.v1','unsupported schema')
    expected={
        'admission':{(p,a,s) for p in range(1,4) for a in ('full','native') for s in ('A','B','C')},
        'receiver':{(p,f,a) for p in range(1,4) for f,aa in (('config',('full','no_close')),('freeze',('full','no_watchdog'))) for a in aa},
        'tasks':{(p,c) for p in range(1,4) for c in ('healthy','recovery')},
        'cost':{(p,a,c) for p in range(1,4) for a in ('full','native') for c in ('new','reuse')},
    }
    keys={'admission':('pair','arm','stage'),'receiver':('pair','fault','arm'),'tasks':('pair','condition'),'cost':('pair','arm','connection')}
    for kind,want in expected.items():
        slots=[tuple(r.get(k) for k in keys[kind]) for r in d.get(kind,[])]
        need(len(slots)==len(want) and set(slots)==want,kind+': retain every planned slot exactly once')
    for kind in ('receiver','tasks','cost'):
        ids=[r.get('run_id') for r in d.get(kind,[]) if r.get('status') in ('recorded','unknown') and r.get('run_id')]
        need(len(ids)==len(set(ids)),kind+': one actual run cannot fill several independent slots')
    for kind in ('admission','history','receiver','tasks','cost','reuse','stage_costs'):
        rows=d.get(kind,[])
        for i,r in enumerate(rows):
            label=f'{kind}[{i}]'
            status=r.get('status')
            need(status in STATUSES,label+': invalid status')
            if status!='pending':
                need(bool(r.get('run_id')) and bool(r.get('evidence_ref')),label+': run_id and evidence_ref required')
            for k,v in r.items():
                if isinstance(v,(int,float)) and k not in ('pair',):
                    need(math.isfinite(v),label+': nonfinite '+k)
                    if k not in ('detected_s','closed_s'): need(v>=0,label+': negative '+k)
            if status=='pending':
                meta={'pair','arm','stage','case','fault','condition','connection','status','run_id','evidence_ref','steps','trace'}
                need(all(v is None for k,v in r.items() if k not in meta),label+': pending record contains observations')
                need(not r.get('trace'),label+': pending record has trace')
                need(all(s.get('outcome') is None and s.get('phase') is None and s.get('proposal_state') is None for s in r.get('steps',[])),label+': pending record has step observations')
            if status=='not_run':
                need(not r.get('trace'),label+': not_run has a trace')
                need(all(not isinstance(v,(int,float)) for k,v in r.items() if k!='pair'),label+': not_run contains measurements')
    for i,r in enumerate(d['admission']):
        if r['status']=='recorded': need(r.get('decision') in ('ALLOW','DENY','UNKNOWN'),f'admission[{i}]: decision required')
        need(r['arm'] in ('full','native') and r['stage'] in ('A','B','C'),f'admission[{i}]: invalid comparison')
    for i,r in enumerate(d['history']):
        if r['status']=='recorded':
            need(r.get('source_kind') in ('archive','signed_fixture'),f'history[{i}]: identify evidence source')
            need(r.get('verdict') in ('ALLOW','DENY','UNKNOWN'),f'history[{i}]: verdict required')
    for i,r in enumerate(d['receiver']):
        label=f'receiver[{i}]'
        if r.get('new_facts_read')==0:
            need(r.get('coverage')=='COMPLETE',label+': zero receipt requires COMPLETE coverage')
        if r.get('closure_observation')=='CLOSED':
            need(r.get('closed_s') is not None,label+': CLOSED needs measured time')
        if r.get('closure_observation')=='RIGHT_CENSORED':
            need(r.get('window_end_s') is not None and r.get('closed_s') is None,label+': censored closure needs end, no fabricated time')
        need(r['arm'] in (('full','no_close') if r['fault']=='config' else ('full','no_watchdog')),label+': invalid targeted ablation')
        last_t,last_y=None,None
        for t in r.get('trace',[]):
            need(t.get('coverage') in ('COMPLETE','GAP'),label+': trace coverage required')
            need(isinstance(t.get('t_s'),(int,float)),label+': trace time required')
            if last_t is not None: need(t['t_s']>=last_t,label+': trace time is not ordered')
            y=t.get('new_facts')
            if t['coverage']=='COMPLETE':
                need(isinstance(y,int) and y>=0,label+': covered count required')
                if y is not None and last_y is not None: need(y>=last_y,label+': cumulative count decreased')
                if y is not None:last_y=y
            else: need(y is None,label+': GAP must not be filled with zero')
            last_t=t['t_s']
    for i,r in enumerate(d['tasks']):
        label=f'tasks[{i}]'
        need([s['step'] for s in r['steps']]==list(range(1,7)),label+': six ordered steps required')
        for s in r['steps']:need(s.get('outcome') is None or s['outcome'] in OUTCOMES,label+': invalid step outcome')
        for k in ('continuation','complete_task'):
            need(r.get(k) in (None,'PASS','FAIL','UNKNOWN','N/A'),label+': invalid '+k)
        if r.get('complete_task')=='PASS':
            need(all(s.get('outcome')=='CORRECT' for s in r['steps']),label+': PASS cannot hide missing or unsuccessful steps')
            need(all(s.get('proposal_state')=='CONFIRMED' for s in r['steps']),label+': complete task needs confirmed proposals')
        if r.get('continuation')=='PASS':
            need(r.get('confirmed_proposals') is not None and r.get('confirmed_readback')==r['confirmed_proposals'] and r.get('readback_status')=='VERIFIED',label+': continuation requires full verified readback')
    for i,r in enumerate(d['cost']):
        label=f'cost[{i}]'
        if r.get('p50_ms') is not None and r.get('p95_ms') is not None:need(r['p95_ms']>=r['p50_ms'],label+': p95 < p50')
        if r['status']=='recorded':
            need(all(r.get(k) is not None for k in ('attempted','valid','rejected','failed','timed_out','invalid','duration_s')),label+': all request outcomes required')
            if all(r.get(k) is not None for k in ('attempted','valid','rejected','failed','timed_out','invalid')):
                need(sum(r[k] for k in ('valid','rejected','failed','timed_out','invalid'))==r['attempted'],label+': outcome categories must partition attempted requests')
            if r.get('p95_ms') is not None:need(r.get('valid',0)>0,label+': percentiles need valid requests')
            if r.get('duration_s') is not None:need(r['duration_s']>0,label+': measurement window must be positive')
            if r.get('cpu_one_core_pct') is not None or r.get('rss_mib') is not None:need(bool(r.get('component_scope')),label+': resource component scope required')
    return errors

if __name__=='__main__':
    data=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    errors=validate(data)
    print(json.dumps({'valid':not errors,'errors':errors,'observed_records':sum(r['status']!='pending' for k in ('admission','history','receiver','tasks','cost','reuse','stage_costs') for r in data[k])},indent=2))
    sys.exit(bool(errors))
