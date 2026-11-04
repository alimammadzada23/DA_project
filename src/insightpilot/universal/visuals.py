"""Purposeful visual recommendations and a shared accessible presentation."""
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd

from .engine import frame_of

TEAL='#61D5C5'
COLORS=['#61D5C5','#8EBAFF','#F1BC73','#DCA8EF','#EF8E9A','#A4C897']


def style(fig,title,x=None,y=None):
    fig.update_layout(template='plotly_dark',paper_bgcolor='#111B2E',plot_bgcolor='#111B2E',
        font={'family':'Arial, sans-serif','size':13,'color':'#E3EAF5'},title={'text':title,'font':{'size':18}},
        margin={'l':24,'r':24,'t':65,'b':50},height=430,
        colorway=COLORS,legend={'orientation':'h','y':1.1},hoverlabel={'namelength':-1})
    fig.update_xaxes(title=x,gridcolor='#263348',automargin=True)
    fig.update_yaxes(title=y,gridcolor='#263348',automargin=True)
    return fig


def ranked(result,title,unit,scope=''):
    data=frame_of(result)
    if data.empty: return style(go.Figure(),title)
    categories=[r[result['columns'].index('category')] for r in result['rows']]
    data['display']=['Missing' if v is None else str(v) for v in categories]
    fig=px.bar(data,x='value',y='display',orientation='h',color_discrete_sequence=[TEAL],
        custom_data=['category'],hover_data={'records':True,'display':False})
    fig.update_yaxes(categoryorder='total ascending',tickfont={'size':12})
    fig.data[0].customdata=[[value,scope] for value in categories]
    fig.update_traces(hovertemplate='%{y}<br>%{x:,.3g} '+unit+'<extra></extra>')
    fig=style(fig,title,unit,None)
    if scope: fig.update_layout(dragmode='select',selectionrevision=scope)
    return fig


def trend(result,title,unit):
    data=frame_of(result)
    if len(data):
        data['period']=pd.to_datetime(data.period)
        grain=result.get('method',{}).get('time_grain','month') if isinstance(result.get('method'),dict) else 'month'
        frequency={'day':'D','week':'W-MON','month':'MS','quarter':'QS','year':'YS'}[grain]
        index=pd.date_range(data.period.min(),data.period.max(),freq=frequency)
        data=data.set_index('period').reindex(index).rename_axis('period').reset_index()
    fig=go.Figure(go.Scatter(x=data.get('period',[]),y=data.get('value',[]),mode='lines+markers',line={'color':TEAL},connectgaps=False))
    return style(fig,title,'Observed period',unit)


def histogram(result,title):
    data=frame_of(result)
    fig=go.Figure(go.Bar(x=data.get('bin_start',[]),y=data.get('records',[]),marker_color=TEAL))
    metadata=result.get('method',{})
    units=metadata.get('units','units unconfirmed') if isinstance(metadata,dict) else 'units unconfirmed'
    return style(fig,title,'Bin lower boundary · '+units,'Finite source rows')


def contribution(result,title,unit):
    data=frame_of(result).head(20)
    labels=data.category.map(lambda v:'Missing' if v is None else str(v)).tolist() if len(data) else []
    fig=go.Figure(go.Waterfall(x=labels,y=data.get('change',[]),measure=['relative']*len(data),
        increasing={'marker':{'color':TEAL}},decreasing={'marker':{'color':'#EF8E9A'}},connector={'line':{'color':'#94A5BD'}}))
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
    def values(value): return [] if value is None else list(value)
    for number,trace in enumerate(figure.data):
        name=trace.name or f'Series {number+1}'
        x,y=values(getattr(trace,'x',None)),values(getattr(trace,'y',None))
        if trace.type=='heatmap':
            for i,row in enumerate(trace.z):
                for j,value in enumerate(row): rows.append({'series':name,'x':x[j] if j<len(x) else j,'y':y[i] if i<len(y) else i,'value':value})
        elif trace.type=='box' and trace.q1 is not None:
            for i,q1 in enumerate(trace.q1):
                rows.append({'series':name,'group':x[i] if i<len(x) else i,'q1':q1,'median':trace.median[i],
                             'q3':trace.q3[i],'lower_whisker':trace.lowerfence[i],'upper_whisker':trace.upperfence[i]})
        else:
            for i in range(max(len(x),len(y))):
                rows.append({'series':name,'x':x[i] if i<len(x) else None,'y':y[i] if i<len(y) else None})
    return pd.DataFrame(rows)
