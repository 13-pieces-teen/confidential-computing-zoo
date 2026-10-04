"""Render manuscript figures from recorded observations, or empty axes."""
from pathlib import Path
import argparse, json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.lines import Line2D
from matplotlib.ticker import MaxNLocator
from validate_results import validate

COLORS={'full':'#315C88','native':'#727B84','no_close':'#B47B30','no_watchdog':'#8A5F89'}
LABELS={'full':'Full Argus','native':'Native SPIRE','no_close':'No active closure','no_watchdog':'No external supervision'}
plt.rcParams.update({'font.family':'DejaVu Serif','font.size':8,'axes.titlesize':8.6,
                     'axes.labelsize':8,'xtick.labelsize':7.4,'ytick.labelsize':7.4,
                     'axes.linewidth':0.55,'grid.linewidth':0.45,
                     'pdf.fonttype':42,'ps.fonttype':42,'svg.fonttype':'none',
                     'savefig.dpi':220,'text.color':'#172331','axes.labelcolor':'#172331'})

def style(ax):
    ax.spines[['top','right']].set_visible(False)
    ax.tick_params(width=.5,length=3)
    ax.grid(axis='y',color='#DCE2E7',zorder=0)

def pending(ax, xmode='time', rows=None):
    label='DATA PENDING' if not rows or all(r['status']=='pending' for r in rows) else 'NO NUMERIC ESTIMATE'
    ax.text(.5,.57,label,transform=ax.transAxes,ha='center',va='center',
            color='#687682',fontsize=10)
    ax.text(.5,.42,'Axes scale to recorded observations',transform=ax.transAxes,
            ha='center',va='center',fontsize=7,color='#8C969E')
    ax.set_yticks([])
    if xmode=='time':
        ax.set_xlim(-1,1);ax.set_xticks([0]);ax.axvline(0,color='#7E8791',ls=':',lw=.7)

def is_observed(r): return r['status'] in ('recorded','unknown')

def receipt_figure(d):
    fig,axs=plt.subplots(2,2,figsize=(7.05,3.65))
    fig.subplots_adjust(left=.09,right=.985,bottom=.18,top=.90,wspace=.30,hspace=.65)
    for ax in axs.flat:style(ax)
    for ax,fault,tag,title in zip(axs[0],('config','freeze'),('a','b'),('Binding configuration change','Helper freeze')):
        arms=('full','no_close') if fault=='config' else ('full','no_watchdog')
        found=False
        for r in d['receiver']:
            if r['fault']!=fault or not is_observed(r):continue
            tr=r.get('trace',[])
            if not tr:continue
            x=[p['t_s'] for p in tr]
            y=[p['new_facts'] if p['coverage']=='COMPLETE' else np.nan for p in tr]
            ax.step(x,y,where='post',color=COLORS[r['arm']],lw=.85,alpha=.85,
                    linestyle='-' if r['arm']=='full' else '--')
            found=True
        ax.set_title(f'({tag}) {title}',loc='left',pad=16,fontweight='bold')
        ax.set_xlabel('Time relative to fault (s)',labelpad=3)
        ax.set_ylabel('New facts received',labelpad=4)
        if not found:pending(ax,rows=[r for r in d['receiver'] if r['fault']==fault])
        else:ax.axvline(0,color='#7E8791',ls=':',lw=.7);ax.set_ylim(bottom=0);ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax.legend([Line2D([],[],color=COLORS[a],lw=1.3,ls='-' if a=='full' else '--') for a in arms],
                  [LABELS[a] for a in arms],frameon=False,loc='lower left',bbox_to_anchor=(0,1.00),
                  ncol=2,handlelength=1.7,columnspacing=.9,borderaxespad=0,fontsize=6.6)
    cats=[('config','full'),('config','no_close'),('freeze','full'),('freeze','no_watchdog')]
    xlabels=['Config\nFull','Config\nNo closure','Freeze\nFull','Freeze\nNo watchdog']
    for ax,title,ylabel,key in [(axs[1,0],'(c) Closure after fault','Ingress closure delay (s)','closed_s'),
                                (axs[1,1],'(d) Receipt at window end','New facts received','new_facts_read')]:
        found=False
        for i,(f,a) in enumerate(cats):
            rs=[r for r in d['receiver'] if r['fault']==f and r['arm']==a]
            for j,r in enumerate(rs):
                if not is_observed(r):continue
                value=r.get(key)
                marker='o';face=COLORS[a]
                if key=='closed_s' and r.get('closure_observation')=='RIGHT_CENSORED':
                    value=r.get('window_end_s');marker='^';face='white'
                if value is None:continue
                ax.scatter(i+(j-1)*.095,value,s=21,marker=marker,facecolor=face,
                           edgecolor=COLORS[a],linewidth=.9,zorder=3)
                found=True
        ax.set_title(title,loc='left',fontweight='bold',pad=7)
        ax.set_ylabel(ylabel,labelpad=4);ax.set_xticks(range(4),xlabels);ax.set_xlim(-.5,3.5)
        if not found:pending(ax,'category',d['receiver'])
        else:ax.set_ylim(bottom=0)
        if key=='new_facts_read':ax.yaxis.set_major_locator(MaxNLocator(integer=True)) if found else None
    fig.text(.09,.025,'Controlled prototype. Dots: runs; hollow triangles: right-censored closure. Gaps break traces.',
             fontsize=7,color='#48545F')
    return fig

def cost_figure(d):
    fig,axs=plt.subplots(1,2,figsize=(7.05,2.35),sharey=True)
    fig.subplots_adjust(left=.09,right=.985,bottom=.25,top=.80,wspace=.23)
    all_values=[]
    for ax,conn,title in zip(axs,('new','reuse'),('(a) New TLS connection','(b) Reused TLS connection')):
        style(ax);found=False
        for i,a in enumerate(('full','native')):
            rs=[r for r in d['cost'] if r['arm']==a and r['connection']==conn]
            for metric,offset,marker in [('p50_ms',-.13,'o'),('p95_ms',.13,'s')]:
                values=[]
                for j,r in enumerate(rs):
                    if not is_observed(r) or r.get(metric) is None:continue
                    y=r[metric];values.append(y);all_values.append(y)
                    ax.scatter(i+offset+(j-1)*.045,y,marker=marker,s=23,
                               facecolors='white' if metric=='p50_ms' else COLORS[a],
                               edgecolors=COLORS[a],linewidths=.85,zorder=3)
                    found=True
                if values:
                    med=float(np.median(values));ax.plot([i+offset-.08,i+offset+.08],[med,med],color=COLORS[a],lw=1)
        ax.set_title(title,loc='left',fontweight='bold',pad=8)
        ax.set_xticks([0,1],['Full Argus','Native SPIRE']);ax.set_xlim(-.5,1.5)
        if not found:pending(ax,'category',[r for r in d['cost'] if r['connection']==conn])
    axs[0].set_ylabel('Software request latency (ms)')
    if all_values:axs[0].set_ylim(bottom=0)
    fig.legend([Line2D([],[],marker='o',mfc='white',mec='#465361',lw=0),
                Line2D([],[],marker='s',mfc='#465361',mec='#465361',lw=0)],
               ['Per-run p50','Per-run p95'],frameon=False,ncol=2,loc='upper center',
               bbox_to_anchor=(.54,1),fontsize=7.5,columnspacing=2)
    fig.text(.09,.035,'Controlled software path. Dots: runs; lines: medians. Outcomes and resources: Table S2.',
             fontsize=7,color='#48545F')
    return fig

def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();d=json.loads(a.data.read_text(encoding='utf-8'));errors=validate(d)
    if errors:raise ValueError('\n'.join(errors))
    a.out.mkdir(parents=True,exist_ok=True)
    with PdfPages(a.out/'plots.pdf') as pdf:
        for name,fig in [('figure-a-receipt',receipt_figure(d)),('figure-b-cost',cost_figure(d))]:
            fig.savefig(a.out/f'{name}.svg');fig.savefig(a.out/f'{name}.png',dpi=220)
            pdf.savefig(fig);plt.close(fig)
    print('Rendered receipt and cost figures without synthetic measurements.')

if __name__=='__main__':main()
