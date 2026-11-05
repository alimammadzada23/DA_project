"""Share and concentration workbench over the validated engine."""
import streamlit as st
import plotly.graph_objects as go
from .concentration import pareto
from . import visuals


def concentration_view(engine, metric, scope, draw, evidence_view):
    st.caption('COMPOSITION / CONTRIBUTION / PARETO')
    left,right=st.columns([3,1])
    dimension=left.selectbox('Concentration grouping field',engine.info['columns'])
    threshold=right.selectbox('Cumulative threshold',[.8,.9,.95],format_func=lambda x:f'{x:.0%}')
    if not metric.additive:
        st.info('Choose a reviewed additive metric or Records in the sidebar. Shares of averages, ratios, or distinct counts would be misleading.');return
    result=pareto(engine,dimension,metric,threshold=threshold)
    data=result['data'];count=len(data)
    a,b,c=st.columns(3)
    a.metric('Selected total',f'{result["total"]:,.3g}',help=metric.name+' · '+metric.units)
    b.metric('Groups to reach '+f'{threshold:.0%}',str(result['groups_to_threshold']))
    c.metric('Largest group share',f'{data.share.iloc[0]:.1%}')
    labels=['Missing' if x is None else str(x) for x in data.category]
    if count<=8:
        chart=st.radio('Composition presentation',['Pareto','Donut'],horizontal=True)
    else:
        chart='Pareto';st.caption(f'{count:,} groups in the denominator. Chart shows the first 20; full values are available in the table.')
    if chart=='Donut':
        figure=go.Figure(go.Pie(labels=labels,values=data.value.tolist(),hole=.68,sort=False,
            marker={'colors':visuals.COLORS},textinfo='percent',hovertemplate='%{label}<br>%{value:,.3g} '+metric.units+'<br>%{percent}<extra></extra>'))
        visuals.style(figure,metric.name+' · selected share')
        figure.update_layout(legend={'orientation':'v','x':1,'y':.8},margin={'l':20,'r':80,'t':65,'b':30})
    else:
        shown=data.head(20)
        figure=go.Figure(go.Bar(x=labels[:20],y=shown.value.tolist(),name=metric.name,marker_color=visuals.TEAL))
        figure.add_trace(go.Scatter(x=labels[:20],y=shown.cumulative_share.tolist(),name='Cumulative share',
            yaxis='y2',line={'color':'#afbbef','width':2},mode='lines+markers',hovertemplate='%{y:.1%}<extra>Cumulative share</extra>'))
        visuals.style(figure,metric.name+' · concentration',dimension,metric.units)
        figure.update_layout(yaxis2={'overlaying':'y','side':'right','range':[0,1.05],'tickformat':'.0%','showgrid':False,'automargin':True},margin={'l':40,'r':65,'t':75,'b':80})
        figure.add_shape(type='line',xref='paper',yref='y2',x0=0,x1=1,y0=threshold,y1=threshold,line={'dash':'dot','color':'#899bad'})
    draw(figure,'concentration-'+scope,result=result['evidence'])
    st.caption(result['limitations'])
    st.dataframe(data[['category','value','records','share','cumulative_share']],hide_index=True,width='stretch',
        column_config={'share':st.column_config.NumberColumn('Share',format='percent'),'cumulative_share':st.column_config.NumberColumn('Cumulative share',format='percent')})
    evidence_view(result['evidence'])
