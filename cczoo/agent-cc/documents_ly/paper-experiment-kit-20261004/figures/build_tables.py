"""Build editable tables and a six-page vector PDF from the same result file."""
from pathlib import Path
from statistics import median
from xml.sax.saxutils import escape
import argparse, collections, hashlib, json, shutil
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, Table, TableStyle, Spacer
from pypdf import PdfReader, PdfWriter
from validate_results import validate

W=7.05*72
CW=W-16
INK=colors.HexColor('#182431');GRAY=colors.HexColor('#75808A')
PSTYLE=ParagraphStyle('cell',fontName='Times-Roman',fontSize=8,leading=9.5,textColor=INK)
HSTYLE=ParagraphStyle('head',parent=PSTYLE,fontName='Times-Bold')
NOTE=ParagraphStyle('note',parent=PSTYLE,fontSize=7,leading=9,textColor=colors.HexColor('#4D5B66'))
TITLE=ParagraphStyle('title',parent=PSTYLE,fontName='Times-Bold',fontSize=10,leading=12)
CAPTIONS={
 'admission':'Admission across record states. Online TDX observations and offline policy diagnostics are reported separately. Counts retain unknown and unexecuted cases; the first rejecting layer identifies the responsible mechanism.',
 'task':'Cross-session task continuation with Full Argus. Each row is a six-step task. Verified readback covers all confirmed original proposals; correct continuation and complete task success are separate outcomes. Both recovery intervals start at re-admission.',
 'reuse':'Identity reuse during healthy service windows. Quote generation, subscriptions, distinct SVIDs, and new admissions are counted separately within the reported observation window.',
 'cost':'Supporting cost measurements. Request outcomes accompany latency, resources use a declared component scope, and stage timings retain their original operation identifiers. Deliberate holds and offline verification are reported separately.'
}
OUTCOME={'CORRECT':'C','INCORRECT':'X','REJECTED':'R','TIMEOUT':'T','UNKNOWN':'U','NOT_DISPATCHED':'N'}

def observed(r):return r['status'] in ('recorded','unknown')
def missing(r):return 'TBD' if r['status']=='pending' else ('NR' if r['status']=='not_run' else 'U')
def rv(r,key):return val(r.get(key)) if r.get(key) is not None else missing(r)
def val(x,unit=''):
    if x is None:return 'TBD'
    if isinstance(x,float):return f'{x:.2f}'+unit
    return str(x)+unit
def summary(xs):
    xs=[x for x in xs if x is not None]
    if not xs:return 'TBD'
    return f'{median(xs):.2f} [{min(xs):.2f}, {max(xs):.2f}]'
def join_actual(rs,key):
    xs=sorted({str(r[key]) for r in rs if observed(r) and r.get(key) is not None})
    return ' / '.join(xs) if xs else ('U' if any(observed(r) for r in rs) else ('NR' if any(r['status']=='not_run' for r in rs) else 'TBD'))
def counts(rs):
    obs=[r for r in rs if observed(r)]
    if not obs:return 'NR' if any(r['status']=='not_run' for r in rs) else 'TBD'
    c=collections.Counter(r.get('decision') or 'UNKNOWN' for r in obs)
    return f"{c['ALLOW']}/{c['DENY']}/{c['UNKNOWN']} (n={len(obs)}/{len(rs)})"

def make_table(headers,rows,widths,font=8):
    st=ParagraphStyle('specific',parent=PSTYLE,fontSize=font,leading=font+1.8)
    hs=ParagraphStyle('specific-head',parent=st,fontName='Times-Bold')
    para=lambda x,s:Paragraph(escape(str(x)).replace('\n','<br/>'),s)
    data=[[para(x,hs) for x in headers]]+[[para(x,st) for x in row] for row in rows]
    table=Table(data,colWidths=widths,repeatRows=1,hAlign='LEFT')
    table.setStyle(TableStyle([
        ('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),3),
        ('TOPPADDING',(0,0),(-1,0),5),('BOTTOMPADDING',(0,0),(-1,0),5),
        ('TOPPADDING',(0,1),(-1,-1),5),('BOTTOMPADDING',(0,1),(-1,-1),5),
        ('LINEABOVE',(0,0),(-1,0),.8,INK),('LINEBELOW',(0,0),(-1,0),.5,INK),
        ('LINEBELOW',(0,-1),(-1,-1),.8,INK)]))
    for y,row in enumerate(rows,1):
        for x,text in enumerate(row):
            if text=='TBD':table.setStyle(TableStyle([('BACKGROUND',(x,y),(x,y),colors.HexColor('#F3F5F7'))]))
    return table

def latex_escape(s):
    chars={'&':r'\&','%':r'\%','$':r'\$','#':r'\#','_':r'\_','{':r'\{','}':r'\}','~':r'\textasciitilde{}','^':r'\textasciicircum{}'}
    return ''.join(chars.get(c,c) for c in str(s)).replace('\n',' ')

def latex_table(headers,rows,compact=False):
    n=len(headers)
    spec='@{}'+('l l '+('c '*6)+'Y Y Y Y' if compact else ' '.join('Y' for _ in headers))+'@{}'
    lines=[r'\begin{tabularx}{\linewidth}{'+spec+'}',r'\toprule',
           ' & '.join(r'\textbf{'+latex_escape(h)+'}' for h in headers)+r' \\',r'\midrule']
    for row in rows:
        lines.append(' & '.join(r'\textcolor{gray}{TBD}' if x=='TBD' else latex_escape(x) for x in row)+r' \\')
    lines.extend([r'\bottomrule',r'\end{tabularx}'])
    return '\n'.join(lines)

def flow_section(title,headers,rows,widths,font=8):
    return [Paragraph(escape(title),HSTYLE),Spacer(1,5),make_table(headers,rows,widths,font),Spacer(1,9)]

def tables(d):
    pages=[];tex=[]
    rows=[]
    for stage,desc in [('A','A. Legal admission'),('B','B. Record unconfirmed'),('C','C. Record confirmed')]:
        f=[r for r in d['admission'] if r['stage']==stage and r['arm']=='full']
        n=[r for r in d['admission'] if r['stage']==stage and r['arm']=='native']
        access=join_actual(f,'business_access')+' / '+join_actual(n,'business_access')
        if all(r['status']=='pending' for r in f+n):access='TBD'
        checks=join_actual(f,'current_checks')+' / '+join_actual(n,'current_checks')
        if all(r['status']=='pending' for r in f+n):checks='TBD'
        rejection=join_actual(f,'first_layer')+' / '+join_actual(n,'first_layer')
        if all(r['status']=='pending' for r in f+n):rejection='TBD'
        rows.append([desc,counts(f),counts(n),access,checks,rejection])
    heads=['Lifecycle state','Full\nA/D/U','Native\nA/D/U','Business access\nFull / native','Current checks\nFull / native','First rejection\nFull / native']
    blocks=flow_section('Online TDX comparison',heads,rows,[90,76,76,84,63,CW-389],7.8)
    text=[r'\textit{Online TDX comparison}\par\smallskip',latex_table(heads,rows)]
    history=[]
    names={'legal_history':'Eligible recorded history','unrelated_activity':'Permitted unrelated history','ineligible_launch':'Ineligible launch history'}
    for r in d['history']:
        history.append([names[r['case']],rv(r,'source_kind'),rv(r,'verdict')])
    hs=['Policy case','Evidence source','Verifier decision']
    blocks+=flow_section('Offline history diagnostics',hs,history,[CW*.48,CW*.30,CW*.22])
    note='Planned n=3 paired runs per mode. A/D/U = allow/deny/unknown. TBD = data pending. Offline cases retain their evidence type.'
    blocks+=[Paragraph(note,NOTE)]
    text += [r'\par\medskip\textit{Offline history diagnostics}\par\smallskip',latex_table(hs,history),r'\par\smallskip\footnotesize '+latex_escape(note)]
    pages.append(('Table A. Admission evidence',blocks));tex.append(('admission','\n'.join(text)))

    rows=[]
    for r in d['tasks']:
        steps=[OUTCOME.get(s.get('outcome'),'N' if r['status']=='not_run' else missing(r)) for s in r['steps']]
        readback=f"{r['confirmed_readback']}/{r['confirmed_proposals']}" if r['confirmed_readback'] is not None and r['confirmed_proposals'] is not None else missing(r)
        continuation='N/A' if r['condition']=='healthy' else rv(r,'continuation')
        timing='N/A' if r['condition']=='healthy' else (rv(r,'read_s')+' / '+rv(r,'continue_s') if r['read_s'] is not None or r['continue_s'] is not None else missing(r))
        rows.append([str(r['pair']),'Healthy' if r['condition']=='healthy' else 'Recovery',*steps,readback,continuation,rv(r,'complete_task'),timing])
    hs=['Pair','Condition',*[f'S{i}' for i in range(1,7)],'Readback\nk / K','Correct\ncontinuation','Complete\ntask','Access / continue\nfrom re-admission (s)']
    widths=[24,54]+[23]*6+[58,61,53,CW-388]
    t=make_table(hs,rows,widths,7.6)
    palette={'C':'#E8F0F8','X':'#F2DDDA','R':'#EEE7DA','T':'#F5EBCD','U':'#EEE7F1','N':'#EDF0F2'}
    for y,row in enumerate(rows,1):
        for x in range(2,8):
            if row[x] in palette:t.setStyle(TableStyle([('BACKGROUND',(x,y),(x,y),colors.HexColor(palette[row[x]]))]))
    note='C correct; X incorrect; R rejected; T timeout; U unknown; N not dispatched. k/K: verified readback / all confirmed proposals. TBD: data pending.'
    pages.append(('Table B. Agent task continuation',[t,Spacer(1,8),Paragraph(note,NOTE),Spacer(1,4),Paragraph('One row per complete task; all six planned steps remain visible. The two recovery intervals share a start and are not added.',NOTE)]))
    tex.append(('task',latex_table(hs,rows,True)+r'\par\smallskip\footnotesize '+latex_escape(note)))

    rows=[]
    for r in d['reuse']:
        rows.append(['Full Argus' if r['arm']=='full' else 'Native SPIRE',*[rv(r,k) for k in ('window_s','node_quotes','workload_quotes','subscriptions','node_svids','workload_svids','new_admissions')]])
    hs=['Mode','Window\n(s)','Node\nQuotes','Workload\nQuotes','Identity\nsubscriptions','Node\nSVIDs','Workload\nSVIDs','New\nadmissions']
    note='Counts refer to each actual healthy window. SVID counts use distinct certificate serials; Quote counts require attestation originals. TBD is not zero.'
    pages.append(('Table S1. Reuse and attestation',[make_table(hs,rows,[84]+[(CW-84)/7]*7),Spacer(1,8),Paragraph(note,NOTE)]))
    tex.append(('reuse',latex_table(hs,rows)+r'\par\smallskip\footnotesize '+latex_escape(note)))

    rows=[]
    for a in ('full','native'):
        for c in ('new','reuse'):
            planned=[r for r in d['cost'] if r['arm']==a and r['connection']==c]
            rs=[r for r in planned if observed(r)]
            fallback='U' if rs else ('NR' if any(r['status']=='not_run' for r in planned) else 'TBD')
            def stat(xs):
                nums=[x for x in xs if x is not None]
                return summary(nums)+f' (n={len(nums)})' if nums else fallback
            valid=[r for r in rs if all(r.get(k) is not None for k in ('valid','attempted','rejected','failed','timed_out','invalid'))]
            counts_text=f"{sum(r['valid'] for r in valid)}/{sum(r['attempted'] for r in valid)} (n={len(valid)}/{len(planned)})" if valid else fallback
            other='/'.join(str(sum(r[k] for r in valid)) for k in ('rejected','failed','timed_out','invalid')) if valid else fallback
            rate=stat([r['valid']/r['duration_s'] for r in valid if r.get('duration_s') and r['duration_s']>0])
            rows.append(['Full' if a=='full' else 'Native','New' if c=='new' else 'Reuse',counts_text,other,rate,
                         stat([r.get('cpu_one_core_pct') for r in rs]),stat([r.get('rss_mib') for r in rs])])
    hs=['Mode','TLS','Valid /\nattempted','R / F / T / I','Valid requests/s','CPU\n(% of one core)','RSS\n(MiB)']
    blocks=flow_section('Normal access: outcomes and resources',hs,rows,[40,35,65,65,100,95,CW-400],7.6)
    texts=[r'\textit{Normal access: outcomes and resources}\par\smallskip',latex_table(hs,rows)]
    srows=[]
    for stage,title in [('quote','Workload Quote'),('appraisal','Remote appraisal'),('identity','Identity delivery'),('ingress','Ingress readiness'),('first_access','First legal access'),('deliberate_hold','Deliberate barrier hold')]:
        cols=[]
        for phase in ('admission','recovery'):
            planned=[r for r in d['stage_costs'] if r.get('phase')==phase and r.get('stage')==stage and r.get('arm')=='full']
            rs=[r for r in planned if observed(r)]
            vals=[r['duration_ms'] for r in rs if r.get('duration_ms') is not None]
            fallback='U' if rs else ('NR' if any(r['status']=='not_run' for r in planned) else 'TBD')
            cols.append(summary(vals)+f' (n={len(vals)})' if vals else fallback)
        srows.append([title,*cols])
    sh=['Measured interval','Establishment (ms)','Recovery (ms)']
    blocks+=flow_section('Full Argus: directly measured intervals',sh,srows,[CW*.30,CW*.35,CW*.35],7.6)
    texts += [r'\par\medskip\textit{Full Argus: directly measured intervals}\par\smallskip',latex_table(sh,srows)]
    hrows=[[names[r['case']],rv(r,'source_kind'),rv(r,'record_count'),rv(r,'record_bytes'),rv(r,'verify_ms')] for r in d['history']]
    hh=['History case','Source','Records','Bytes','Offline verification (ms)']
    blocks+=flow_section('Offline history processing',hh,hrows,[CW*.33,CW*.19,CW*.13,CW*.13,CW*.22],7.6)
    texts += [r'\par\medskip\textit{Offline history processing}\par\smallskip',latex_table(hh,hrows)]
    note='Numeric summaries are median [min, max] of observed runs. R/F/T/I = rejected/failed/timed out/invalid. Deliberate holds and offline timings remain separate from online processing cost; intervals are not summed.'
    blocks+=[Paragraph(note,NOTE)]
    texts += [r'\par\smallskip\footnotesize '+latex_escape(note)]
    pages.append(('Table S2. Supporting cost evidence',blocks));tex.append(('cost','\n'.join(texts)))
    return pages,tex

def write_pages(pages,path):
    c=canvas.Canvas(str(path),pagesize=(W,200))
    c.setTitle('Argus experimental table templates')
    layout=[]
    for title,blocks in pages:
        flows=[Paragraph(escape(title),TITLE),Spacer(1,8),*blocks]
        dims=[f.wrap(CW,10000) for f in flows]
        height=sum(h for w,h in dims)+20
        c.setPageSize((W,height));y=height-10
        for flow,(fw,fh) in zip(flows,dims):
            y-=fh;flow.drawOn(c,8,y)
        assert y>=9.9
        c.showPage();layout.append({'title':title,'width_pt':W,'height_pt':height})
    c.save();return layout

def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);p.add_argument('--plots',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();d=json.loads(a.data.read_text(encoding='utf-8'));errors=validate(d)
    if errors:raise ValueError('\n'.join(errors))
    a.out.mkdir(parents=True,exist_ok=True);(a.out/'latex').mkdir(exist_ok=True)
    (a.out/'assets').mkdir(exist_ok=True)
    for name in ('figure-a-receipt','figure-b-cost'):
        for ext in ('svg','png'):shutil.copyfile(a.plots/f'{name}.{ext}',a.out/'assets'/f'{name}.{ext}')
    pages,tex=tables(d)
    layout=write_pages(pages,a.plots/'tables.pdf')
    reader=PdfReader(a.plots/'tables.pdf');plots=PdfReader(a.plots/'plots.pdf')
    writer=PdfWriter()
    for page in (reader.pages[0],plots.pages[0],reader.pages[1],plots.pages[1],reader.pages[2],reader.pages[3]):writer.add_page(page)
    writer.add_metadata({'/Title':'Argus - experimental figures and tables (unfilled templates)',
                         '/Subject':'Observed data only; templates contain no synthetic measurements'})
    final=a.out/'argus-experiment-templates.pdf'
    with final.open('wb') as f:writer.write(f)
    fragments=[]
    for name,body in tex:
        fragment='\n'.join([r'\begin{table*}[t]',r'\centering',r'\scriptsize',r'\setlength{\tabcolsep}{3pt}',
                             r'\caption{'+CAPTIONS[name]+'}',r'\label{tab:argus-'+name+'}',body,r'\end{table*}'])
        (a.out/'latex'/f'table-{name}.tex').write_text(fragment,encoding='utf-8');fragments.append(fragment)
    preamble='\n'.join([r'\documentclass[10pt]{article}',r'\usepackage[margin=18mm]{geometry}',
                        r'\usepackage{booktabs,tabularx,array,xcolor,graphicx}',r'\newcolumntype{Y}{>{\raggedright\arraybackslash}X}',
                        r'\begin{document}'])
    preview=preamble+'\n'+('\n\\clearpage\n'.join(fragments))+'\n\\end{document}\n'
    (a.out/'tables-preview.tex').write_text(preview,encoding='utf-8')
    report={'status':'GENERATED','pdf_pages':6,'table_layout':layout,'data_sha256':hashlib.sha256(a.data.read_bytes()).hexdigest(),
            'observed_records':sum(r['status']!='pending' for kind in ('admission','history','receiver','tasks','cost','reuse','stage_costs') for r in d[kind]),
            'validation_errors':errors,'input_missing_values_render_as':'TBD','synthetic_measurements_used':False,
            'pdf_sha256':hashlib.sha256(final.read_bytes()).hexdigest()}
    (a.out/'build-manifest.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
