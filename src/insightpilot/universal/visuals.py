"""Purposeful visual recommendations and a shared accessible presentation."""
import math

import plotly.express as px
import plotly.graph_objects as go
import pandas as pd

from .engine import frame_of

TEAL='#67E0CD'
COLORS=['#61D5C5','#8EBAFF','#F1BC73','#DCA8EF','#EF8E9A','#A4C897']
INK='#E3EAF5';MUTED='#94A5BD';GRID='#223340';BACKGROUND='#0f1b2b';NEGATIVE='#EF8E9A';AMBER='#F1BC73';BLUE='#8EBAFF'


def category_label(value):
    """Display label for a grouping value; NULL (None or pandas NaN) stays an explicit category."""
    try:
        if value is None or pd.isna(value):return 'Missing'
    except (TypeError,ValueError):pass
    return str(value)


def compact(value):
    """Short human label for an annotation; the exact value stays in hover and exports."""
    if value is None:return 'n/a'
    try:number=float(value)
    except (TypeError,ValueError):return str(value)
    if not math.isfinite(number):return 'n/a'
    size=abs(number)
    for limit,divisor,suffix in ((1e12,1e12,'T'),(1e9,1e9,'B'),(1e6,1e6,'M'),(1e4,1e3,'k')):
        if size>=limit:return f'{number/divisor:,.3g}{suffix}'
    if size>=100 or number==int(number):return f'{number:,.0f}'
    return f'{number:,.3g}'


def subtitle(fig,text):
    """A muted explanatory line under the title; keeps the title text itself plain for saved names."""
    fig.add_annotation(text=text,xref='paper',yref='paper',x=0,y=1.0,xanchor='left',yanchor='bottom',yshift=6,
                       showarrow=False,align='left',font={'size':12,'color':MUTED})
    return fig


def style(fig,title,x=None,y=None):
    fig.update_layout(template='plotly_dark',paper_bgcolor=BACKGROUND,plot_bgcolor=BACKGROUND,
        font={'family':'Inter, ui-sans-serif, sans-serif','size':13,'color':INK},
        title={'text':title,'font':{'size':18},'x':0.012,'xanchor':'left','y':0.97,'yanchor':'top'},
        margin={'l':24,'r':24,'t':78,'b':50},height=430,separators='.,',
        colorway=COLORS,legend={'orientation':'h','yanchor':'bottom','y':1.02,'xanchor':'right','x':1,'font':{'size':12,'color':MUTED}},
        hoverlabel={'namelength':-1,'bgcolor':'#16263a','bordercolor':'#2c4256','font':{'color':INK}})
    fig.update_xaxes(title=x,gridcolor=GRID,zeroline=False,linecolor='#2c4256',tickfont={'color':MUTED},automargin=True)
    fig.update_yaxes(title=y,gridcolor=GRID,zeroline=False,linecolor='#2c4256',tickfont={'color':MUTED},automargin=True)
    return fig


def ranked(result,title,unit,scope=''):
    data=frame_of(result)
    if data.empty: return style(go.Figure(),title)
    columns=result['columns']
    categories=[r[columns.index('category')] for r in result['rows']]
    values=[r[columns.index('value')] for r in result['rows']]
    records=[r[columns.index('records')] for r in result['rows']] if 'records' in columns else [None]*len(values)
    display=[category_label(v) for v in categories]
    colors=[NEGATIVE if isinstance(v,(int,float)) and v<0 else TEAL for v in values]
    fig=go.Figure(go.Bar(x=values,y=display,orientation='h',marker={'color':colors},
        customdata=[[value,scope] for value in categories],
        text=[compact(v) for v in values],textposition='outside',cliponaxis=False,textfont={'color':MUTED,'size':12},
        hovertext=[('' if n is None else f'{n:,} source rows') for n in records],
        hovertemplate='<b>%{y}</b><br>%{x:,.4~g} '+unit+'<br>%{hovertext}<extra></extra>'))
    fig.update_yaxes(categoryorder='total ascending',tickfont={'size':12,'color':INK})
    fig=style(fig,title,unit,None)
    fig.update_xaxes(rangemode='tozero')
    fig.update_layout(height=min(850,max(430,len(categories)*26+120)),bargap=.28,margin={'r':56})
    if scope: fig.update_layout(dragmode='select',selectionrevision=scope)
    return fig


def _edge_coverage(row,grain):
    """True when observed dates do not span the whole calendar period (edge periods may be incomplete)."""
    try:
        start=pd.Timestamp(row.period);end=start+pd.tseries.frequencies.to_offset({'day':'D','week':'W','month':'MS','quarter':'QS','year':'YS'}[grain])
        if grain=='week':end=start+pd.Timedelta(days=7)
        first=pd.Timestamp(row.first_observed);last=pd.Timestamp(row.last_observed)
        return first.normalize()>start or last.normalize()<(end-pd.Timedelta(days=1))
    except (AttributeError,TypeError,ValueError,KeyError):return False


def trend(result,title,unit):
    data=frame_of(result)
    grain=result.get('method',{}).get('time_grain','month') if isinstance(result.get('method'),dict) else 'month'
    partial=set()
    if len(data):
        data['period']=pd.to_datetime(data.period)
        if grain!='day' and {'first_observed','last_observed'}<=set(data.columns):
            for position in (0,len(data)-1):
                if _edge_coverage(data.iloc[position],grain):partial.add(data.period.iloc[position])
        frequency={'day':'D','week':'W-MON','month':'MS','quarter':'QS','year':'YS'}[grain]
        index=pd.date_range(data.period.min(),data.period.max(),freq=frequency)
        data=data.set_index('period').reindex(index).rename_axis('period').reset_index()
    periods=data.get('period',pd.Series(dtype='datetime64[ns]'))
    values=data.get('value',pd.Series(dtype=float))
    records=data.get('records',pd.Series([None]*len(data)))
    symbols=['circle-open' if p in partial else 'circle' for p in periods]
    fig=go.Figure(go.Scatter(x=periods,y=values,mode='lines+markers',name='Observed',connectgaps=False,
        line={'color':TEAL,'width':2.4},marker={'color':TEAL,'size':7,'symbol':symbols,'line':{'width':2,'color':TEAL}},
        hovertext=['Missing period · not zero-filled' if pd.isna(v) else ('' if pd.isna(n) else f'{int(n):,} source rows')+(' · edge period may be incomplete' if p in partial else '') for p,v,n in zip(periods,values,records)],
        hovertemplate='<b>%{x|%d %b %Y}</b><br>%{y:,.4~g} '+unit+'<br>%{hovertext}<extra></extra>'))
    numeric=pd.to_numeric(values,errors='coerce')
    observed=numeric.notna().sum()
    if observed>=6:
        # Visual guide only: a window never spans a missing period, so gaps are not bridged.
        average=numeric.rolling(3,min_periods=3).mean()
        fig.add_trace(go.Scatter(x=periods,y=average,mode='lines',name='3-period moving average',connectgaps=False,
            line={'color':BLUE,'width':1.6,'dash':'dot'},hovertemplate='%{y:,.4~g} '+unit+'<extra>3-period moving average</extra>'))
    complete=numeric.copy()
    for p in partial:complete[periods==p]=float('nan')
    if complete.notna().sum()>=3 and complete.max()!=complete.min():
        for position,label,shift in ((complete.idxmax(),'Peak',-18),(complete.idxmin(),'Low',18)):
            fig.add_annotation(x=periods.iloc[position],y=numeric.iloc[position],text=label+' '+compact(numeric.iloc[position]),
                showarrow=False,yshift=-shift,font={'size':11,'color':INK},bgcolor='rgba(15,27,43,.75)')
    fig=style(fig,title,'Period · '+grain,unit)
    notes=[]
    if partial:notes.append('Hollow markers: edge periods whose observed dates do not cover the whole '+grain)
    if numeric.isna().any() and len(numeric):notes.append(f'{int(numeric.isna().sum())} missing period(s) shown as gaps')
    if notes:subtitle(fig,' · '.join(notes))
    fig.update_layout(hovermode='x unified' if observed>=6 else 'closest',margin={'b':86,'r':48},
                      legend={'orientation':'h','yanchor':'top','y':-0.2,'xanchor':'left','x':0})
    return fig


def _bin_width(starts,metadata):
    starts=[float(s) for s in starts if s is not None and math.isfinite(float(s))]
    bins=metadata.get('bins') if isinstance(metadata,dict) else None
    if len(starts)>=2 and isinstance(bins,int) and bins>0:
        # The engine always emits the minimum's bin first and the exact maximum's boundary bin last.
        return (starts[-1]-starts[0])/bins
    gaps=[b-a for a,b in zip(starts,starts[1:]) if b>a]
    return min(gaps) if gaps else 1.0


def histogram(result,title,summary=None):
    data=frame_of(result)
    metadata=result.get('method',{})
    units=metadata.get('units','units unconfirmed') if isinstance(metadata,dict) else 'units unconfirmed'
    starts=data.get('bin_start',pd.Series(dtype=float)).tolist();counts=data.get('records',pd.Series(dtype=float)).tolist()
    width=_bin_width(starts,metadata);total=sum(c for c in counts if c) or 1
    boundary=len(starts)>=2 and isinstance(metadata,dict) and isinstance(metadata.get('bins'),int)
    widths=[];offsets=[];labels=[]
    for index,(start,count) in enumerate(zip(starts,counts)):
        if boundary and index==len(starts)-1:
            # The exact maximum has its own zero-width boundary bin; draw it as a narrow marker at the value.
            widths.append(width*.18);offsets.append(-width*.09);labels.append(f'Exactly the maximum {start:,.6g}')
        else:
            widths.append(width);offsets.append(0);labels.append(f'[{start:,.6g}, {start+width:,.6g})')
    fig=go.Figure(go.Bar(x=starts,y=counts,width=widths,offset=offsets,name='Rows per bin',marker={'color':TEAL,'line':{'color':BACKGROUND,'width':1}},
        hovertext=[f'{label}<br>{count:,} rows · {count/total:.1%} of finite values' for label,count in zip(labels,counts)],
        hovertemplate='%{hovertext}<extra></extra>'))
    fig=style(fig,title,'Value · '+units,'Finite source rows')
    fig.update_layout(bargap=0)
    if summary is not None:
        stats=dict(zip(summary['columns'],summary['rows'][0])) if summary.get('rows') else {}
        q1,median,q3,mean=(stats.get(k) for k in ('q1','median','q3','mean'))
        if all(isinstance(v,(int,float)) and math.isfinite(v) for v in (q1,q3)) and q3>q1:
            fig.add_vrect(x0=q1,x1=q3,fillcolor=AMBER,opacity=.08,line_width=0,layer='below')
        for value,label,color,dash,anchor in ((median,'Median','#F1BC73','solid','right'),(mean,'Mean','#8EBAFF','dash','left')):
            if isinstance(value,(int,float)) and math.isfinite(value):
                fig.add_vline(x=value,line={'color':color,'width':2,'dash':dash})
                fig.add_annotation(x=value,y=1,yref='paper',text=label+' '+compact(value),showarrow=False,
                                   xanchor='left' if anchor=='left' else 'right',xshift=4 if anchor=='left' else -4,yanchor='top',font={'size':11,'color':color})
        if stats.get('valid'):subtitle(fig,f"{int(stats['valid']):,} finite values · shaded band = middle 50% (Q1–Q3), full-data quantiles")
    return fig


def contribution(result,title,unit):
    full=frame_of(result)
    data=full.head(20)
    labels=[category_label(v) for v in data.category] if len(data) else []
    changes=data.get('change',pd.Series(dtype=float)).tolist()
    bridge=len(full) and {'earlier','later','change'}<=set(full.columns) and not full[['earlier','later','change']].isna().any().any()
    if bridge:
        # A reconciling bridge: earlier total + every segment change = later total (all segments, not only those shown).
        earlier=float(full.earlier.sum());later=float(full.later.sum());rest=float(full.change.iloc[20:].sum())
        x=['Earlier period',*labels];y=[earlier,*changes];measure=['absolute',*['relative']*len(labels)]
        hover=['Total across all segments']+[f'{category_label(r.category)}: {r.earlier:,.4g} → {r.later:,.4g}' for r in data.itertuples()]
        if len(full)>20:
            x.append(f'Other {len(full)-20} segments');y.append(rest);measure.append('relative');hover.append('Combined change of segments not shown individually')
        x.append('Later period');y.append(later);measure.append('total');hover.append('Total across all segments')
        fig=go.Figure(go.Waterfall(x=x,y=y,measure=measure,hovertext=hover,
            text=[compact(v) if m!='relative' else ('+' if v>0 else '')+compact(v) for v,m in zip(y,measure)],textposition='outside',cliponaxis=False,
            hovertemplate='<b>%{x}</b><br>%{y:,.4~g} '+unit+'<br>%{hovertext}<extra></extra>',
            increasing={'marker':{'color':TEAL}},decreasing={'marker':{'color':NEGATIVE}},totals={'marker':{'color':'#4F6D8F'}},
            connector={'line':{'color':MUTED,'dash':'dot'}}))
        fig=style(fig,title+(' · largest 20 segments' if len(full)>20 else ''),'Segment',unit)
        change=later-earlier
        subtitle(fig,f'Net change {"+" if change>0 else ""}{compact(change)} {unit}'+(f' ({change/abs(earlier):+.1%})' if earlier else '')+' · bars reconcile the earlier total to the later total; contributions are not causes')
        return fig
    fig=go.Figure(go.Waterfall(x=labels,y=changes,measure=['relative']*len(data),
        increasing={'marker':{'color':TEAL}},decreasing={'marker':{'color':NEGATIVE}},connector={'line':{'color':MUTED}}))
    return style(fig,title+' · largest 20 segments','Segment',unit+' change')


def recommendations(profile):
    numeric=[c['column'] for c in profile['columns'] if c['inferred_type']=='numeric']
    dates=[c['column'] for c in profile['columns'] if c['recognized_dates']]
    categories=[c['column'] for c in profile['columns'] if 1<c['distinct']<=50 and c['inferred_type'] not in ('date','numeric','identifier')]
    choices=[{'chart':'ranked bars','question':'Which categories account for the most selected records?',
              'columns':categories,'reason':'Readable ordered comparisons; show top N and disclose omitted categories.'}] if categories else []
    if numeric: choices.append({'chart':'histogram','question':'How is this numeric field distributed?', 'columns':numeric,
                                'reason':'Shows the shape without imposing arbitrary business bands; excludes invalid numbers explicitly.'})
    if dates: choices.append({'chart':'line','question':'How does the selected metric change over time?', 'columns':dates,
                             'reason':'Genuine calendar axis; incomplete edge periods and missing periods must be disclosed.'})
    if len(numeric)>1: choices.append({'chart':'scatter','question':'Are two numeric variables associated?', 'columns':numeric,
                                      'reason':'Paired values reveal relationships; correlation does not establish causation.'})
    return choices


def plotted_data(figure):
    """Export exactly the displayed trace values, including precomputed box summaries."""
    rows=[]
    def values(value):
        if value is None:return []
        if isinstance(value,dict):
            # Plotly 6 serializes numeric arrays as typed base64 data. Python
            # Figure(JSON) leaves this encoding intact; iterating its keys would
            # export "dtype"/"bdata" instead of the actual plotted numbers.
            import base64
            import numpy as np
            if set(value)-{'dtype','bdata','shape'} or value.get('dtype') not in ('i1','u1','i2','u2','i4','u4','f4','f8'):
                raise ValueError('Unsupported plotted numeric encoding.')
            encoded=value.get('bdata')
            if not isinstance(encoded,str) or len(encoded)>8*1024*1024:raise ValueError('Plotted numeric array exceeds its export limit.')
            array=np.frombuffer(base64.b64decode(encoded,validate=True),dtype=value['dtype'])
            if 'shape' in value:
                shape=tuple(int(n.strip()) for n in value['shape'].split(','))
                if not shape or len(shape)>3 or any(n<0 for n in shape):raise ValueError('Invalid plotted numeric shape.')
                array=array.reshape(shape)
            return array.tolist()
        return list(value)
    for number,trace in enumerate(figure.data):
        name=trace.name or f'Series {number+1}'
        x,y=values(getattr(trace,'x',None)),values(getattr(trace,'y',None))
        if trace.type=='pie':
            for label,value in zip(values(trace.labels),values(trace.values)):
                rows.append({'series':name,'category':label,'value':value})
        elif trace.type=='heatmap':
            for i,row in enumerate(values(trace.z)):
                for j,value in enumerate(row): rows.append({'series':name,'x':x[j] if j<len(x) else j,'y':y[i] if i<len(y) else i,'value':value})
        elif trace.type=='box' and trace.q1 is not None:
            for i,q1 in enumerate(values(trace.q1)):
                rows.append({'series':name,'group':x[i] if i<len(x) else i,'q1':q1,'median':values(trace.median)[i],
                             'q3':values(trace.q3)[i],'lower_whisker':values(trace.lowerfence)[i],'upper_whisker':values(trace.upperfence)[i]})
        else:
            for i in range(max(len(x),len(y))):
                rows.append({'series':name,'x':x[i] if i<len(x) else None,'y':y[i] if i<len(y) else None})
    return pd.DataFrame(rows)
