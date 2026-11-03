"""Business-facing Plotly figures from bounded SQL aggregations; no database copies."""
import pandas as pd
import plotly.graph_objects as go

TEAL='#61D5C5'
BLUE='#7BA8FF'
AMBER='#F2B56A'
MUTED='#94A5BD'


def frame(result):
    return pd.DataFrame(result['rows'],columns=result['columns'])


def label(value):
    if value is None or pd.isna(value):return 'Not recorded'
    return str(value).removeprefix('SYNTHETIC · ')


def style(figure,title='',x_title=None,y_title=None,height=315):
    figure.update_layout(template='plotly_dark',paper_bgcolor='rgba(0,0,0,0)',plot_bgcolor='rgba(0,0,0,0)',
        title={'text':title,'font':{'size':15}},font={'family':'Arial, sans-serif','color':'#C9D5E8','size':12},
        margin={'l':12,'r':16,'t':35 if title else 12,'b':35},height=height,
        legend={'orientation':'h','y':1.14,'x':0},hoverlabel={'bgcolor':'#1B2940'},
        xaxis_title=x_title,yaxis_title=y_title,
        xaxis={'gridcolor':'#243147','zeroline':False},yaxis={'gridcolor':'#243147','zeroline':False})
    return figure


def monthly_volume(result):
    df=frame(result)
    fig=go.Figure(go.Bar(x=df['month'],y=df['items'],marker_color=TEAL,name='Items',
        hovertemplate='%{x|%b %Y}<br>%{y:,} purchase-order items<extra></extra>'))
    return style(fig,x_title='Item start month (UTC)',y_title='Items')


def duration_trend(result):
    df=frame(result);fig=go.Figure()
    for column,title,color in [('median_days','Median',TEAL),('p90_days','90th percentile',BLUE)]:
        fig.add_trace(go.Scatter(x=df['month'],y=df[column],mode='lines+markers',name=title,line={'color':color,'width':2},
            customdata=df[['items']].values,hovertemplate='%{x|%b %Y}<br>'+title+': %{y:.2f} days<br>%{customdata[0]:,} items<extra></extra>'))
    return style(fig,x_title='Item start month (UTC)',y_title='Observed span · calendar days')


def band_chart(result,band,value,color=TEAL):
    df=frame(result)
    fig=go.Figure(go.Bar(x=df[band],y=df[value],marker_color=color,
        hovertemplate='%{x}<br>%{y:,} '+value.replace('_',' ')+'<extra></extra>'))
    return style(fig,x_title='Recorded interval',y_title=value.replace('_',' ').title())


def group_chart(result,measure='median_days',top=10):
    df=frame(result).head(top).copy();fig=go.Figure()
    if not df.empty:
        fig.add_trace(go.Bar(x=df[measure],y=df['group_name'].map(label),orientation='h',marker_color=TEAL,
            customdata=df[['group_name','items','p90_days','repeat_rate']].values,
            hovertemplate='%{y}<br>Value: %{x:.2f}<br>Items: %{customdata[1]:,}<br>P90: %{customdata[2]:.2f} days<br>Repeat rate: %{customdata[3]:.1%}<extra></extra>'))
    style(fig,x_title='Median observed span · days' if measure=='median_days' else 'Repeat-item rate',height=max(280,min(440,70+35*len(df))))
    fig.update_yaxes(autorange='reversed')
    if measure=='repeat_rate':fig.update_xaxes(tickformat='.0%')
    return fig


def activities(result,repeated=False):
    df=frame(result).head(12).copy();value='excess_occurrences' if repeated else 'events'
    fig=go.Figure(go.Bar(x=df[value],y=df['activity'],orientation='h',marker_color=AMBER if repeated else BLUE,
        hovertemplate='%{y}<br>%{x:,} '+('excess recorded occurrences' if repeated else 'recorded events')+'<extra></extra>'))
    style(fig,x_title='Excess occurrences' if repeated else 'Recorded events',height=max(290,min(470,80+30*len(df))))
    fig.update_yaxes(autorange='reversed')
    return fig


def variants(result):
    df=frame(result).head(8)
    fig=go.Figure(go.Bar(x=df['case_count'],y=[f'Variant {i+1:02d}' for i in range(len(df))],orientation='h',
        marker_color=BLUE,customdata=df[['variant']].values,
        hovertemplate='%{y}<br>%{x:,} items<br>Full sequence available in the variant table<extra></extra>'))
    style(fig,x_title='Items',height=310);fig.update_yaxes(autorange='reversed')
    return fig


def review_chart(result):
    df=frame(result)
    fig=go.Figure(go.Bar(x=df['matching_category'].map(label),y=df['review_items'],marker_color=AMBER,
        hovertemplate='%{x}<br>%{y:,} items for contextual review<extra></extra>'))
    return style(fig,x_title='Procurement flow',y_title='Review signals · items')


def timeline(result):
    df=frame(result);df=df[df['event_time'].notna()]
    fig=go.Figure(go.Scatter(x=df['event_time'],y=df['activity'],mode='lines+markers',
        line={'color':TEAL,'width':2},marker={'size':10,'color':TEAL},
        customdata=df[['event_rank','resource']].values,
        hovertemplate='%{y}<br>%{x|%Y-%m-%d %H:%M UTC}<br>Event rank: %{customdata[0]}<br>Resource: %{customdata[1]}<extra></extra>'))
    return style(fig,x_title='Recorded event timestamp (UTC)',y_title='Activity',height=330)


def flow(result,top=18):
    """Sankey summarizes directed transitions; the separate graph preserves cycles."""
    df=frame(result).sort_values('occurrences',ascending=False,kind='stable').head(top)
    names=sorted(set(df['from_activity']).union(df['to_activity']))
    # Separate source/target layers handle repeats and cycles without an invalid cyclic Sankey.
    positions={name:i for i,name in enumerate(names)};n=len(names)
    fig=go.Figure(go.Sankey(arrangement='snap',node={'label':['From · '+s for s in names]+['To · '+s for s in names],
        'color':[BLUE]*n+[TEAL]*n,'pad':15,'thickness':14},
        link={'source':[positions[v] for v in df['from_activity']],
              'target':[n+positions[v] for v in df['to_activity']],
              'value':df['occurrences'],'color':'rgba(97,213,197,0.25)',
              'customdata':[[('Not recorded' if pd.isna(value) else f'{value:.2f} hours')] for value in df['median_wait_hours']],
              'hovertemplate':'%{source.label} → %{target.label}<br>%{value:,} transitions<br>Median interval: %{customdata[0]}<extra></extra>'}))
    return style(fig,height=390)
