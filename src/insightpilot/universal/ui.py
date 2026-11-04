"""General-purpose analytics workspace, extending the existing Streamlit shell."""
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import uuid

import pandas as pd
import httpx
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from . import advanced, assistant, visuals
from .engine import Engine, Filter, frame_of
from .insights import discover
from .investigation import proportion_change
from .metrics import Metric, load_metrics
from .relationships import load_relationships, related_groups
from .report import configuration, csv_export, excel_export, html_report, replay, safe_spreadsheet_frame
from .statistics import mean_interval, regression, welch_test
from .statistics import categorical_association
from .segmentation import cluster
from .store import Workspace
from .ui_data import data_hub, metric_editor, quality_editor

PAGES=['Data Hub','Data Quality','Executive Intelligence','Visual Explorer','Advanced Analytics',
       'Investigation Lab','AI Business Analyst','Reports and Exports']


@st.cache_data(max_entries=24,show_spinner=False)
def cached_profile(path,table,version):
    return Workspace(path).profile(table)


def draw(figure,key,*,select=False):
    kwargs={'on_select':'rerun','selection_mode':('points','box')} if select else {}
    event=st.plotly_chart(figure,width='stretch',key=key,theme=None,config={'displaylogo':False,
        'toImageButtonOptions':{'format':'png','scale':2}},**kwargs)
    with st.expander('Chart data and image export',expanded=False):
        data=visuals.plotted_data(figure)
        st.download_button('Download plotted values · CSV (formula-safe text)',safe_spreadsheet_frame(data).to_csv(index=False),
                           key='data-'+key,file_name='chart-values.csv',mime='text/csv')
        st.download_button('Download interactive HTML',figure.to_html(include_plotlyjs=True),key='html-'+key,
                           file_name='insightpilot-chart.html',mime='text/html')
        st.caption('Use the chart’s camera button for a PNG image. Expand with the Plotly toolbar; box-select supported bars to inspect records.')
    return event


def evidence(result):
    with st.expander('Inspect calculations and provenance'):
        st.code(result['sql'],language='sql')
        st.json(result)
        st.download_button('Download evidence JSON',json.dumps(result,default=str,indent=2),
                           file_name='analysis-evidence.json',mime='application/json',key='ev-'+result['evidence_id'])


def sidebar_filters(workspace,table,report):
    filters=[]
    with st.sidebar.expander('Shared analysis filters'):
        tag=workspace.info(table)['current']
        epoch_key='universal-filter-epoch-'+tag
        epoch=st.session_state.get(epoch_key,0)
        if st.button('Reset analysis filters'):
            st.session_state[epoch_key]=epoch+1; st.rerun()
        suffix=tag+str(epoch)
        cols=[None]+workspace.columns(table)
        for index in range(2):
            col=st.selectbox(f'Category filter {index+1}',cols,key=f'uf-col-{index}-'+suffix,format_func=lambda v:'No filter' if v is None else v)
            if col:
                categories=Engine(workspace,table).categories(col)
                options=[r[0] for r in categories['rows']]
                mode=st.radio(f'Category entry {index+1}',['Choose value','Type exact value'],key=f'uf-mode-{index}-'+suffix,horizontal=True)
                if mode=='Choose value':
                    value=st.selectbox(f'Value {index+1}',options,key=f'uf-value-{index}-'+suffix+'-'+col,format_func=lambda v:'Missing' if v is None else str(v))
                else: value=st.text_input(f'Exact category {index+1}',key=f'uf-literal-{index}-'+suffix)
                if options or mode=='Type exact value': filters.append(Filter(col,'eq',value))
                if categories['truncated']: st.caption('1,000 most frequent choices shown; type an exact value for other categories.')
        dates=[c for c in report['columns'] if c['recognized_dates']]
        if dates and st.checkbox('Apply a date window',key='uf-date-enabled-'+suffix):
            col=st.selectbox('Filter date field',[c['column'] for c in dates],key='uf-date-col-'+suffix)
            bounds=next(c['date_range'] for c in dates if c['column']==col)
            chosen=st.date_input('Inclusive date window',value=tuple(bounds),key='uf-range-'+suffix+'-'+col)
            if len(chosen)==2:
                if chosen[0]>chosen[1]: raise ValueError('Date filter start must precede end.')
                filters.extend([Filter(col,'from',chosen[0]),Filter(col,'through',chosen[1])])
        numeric=[c for c in report['columns'] if c['statistics'] and c['inferred_type']!='identifier']
        if numeric and st.checkbox('Apply a numeric range',key='uf-num-enabled-'+suffix):
            name=st.selectbox('Filter numeric field',[c['column'] for c in numeric],key='uf-num-col-'+suffix)
            stats=next(c['statistics'] for c in numeric if c['column']==name)
            lower=st.number_input('Minimum inclusive',value=float(stats['min']),key='uf-num-lo-'+suffix+name)
            upper=st.number_input('Maximum inclusive',value=float(stats['max']),key='uf-num-hi-'+suffix+name)
            if lower>upper: raise ValueError('Minimum must not exceed maximum.')
            filters.extend([Filter(name,'gte',lower),Filter(name,'lte',upper)])
    return tuple(filters)


def record_view(engine,key):
    total=engine.count()
    st.subheader('Supporting records')
    page=st.number_input('Record page',min_value=1,max_value=max(1,(total+99)//100),value=1,key='ur-page-'+key)
    result=engine.records(int(page)-1)
    st.dataframe(frame_of(result),hide_index=True,width='stretch')
    st.caption(f'{total:,} matching rows · at most 100 per page. _row_id traces to the imported source row, including after preparation.')
    st.download_button('Download this page · Excel (formula-safe text)',excel_export(result),file_name='filtered-records.xlsx',
                       key='ur-xlsx-'+key,mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    st.download_button('Download this page · CSV (formula-safe text)',csv_export(result),file_name='filtered-records.csv',
                       key='ur-csv-'+key,mime='text/csv')
    evidence(result)


def fields(report):
    approved=report.get('approved_roles',{})
    numeric=[c['column'] for c in report['columns'] if c['numeric_values'] and c['inferred_type']!='identifier'
             and approved.get(c['column']) not in ('identifier','date','customer','product','transaction','geography')
             and not any(r['role'] in ('date','identifier','customer','product','transaction','geography') for r in c['candidates'])]
    numeric.sort(key=lambda c:0 if approved.get(c) in ('measure','revenue','expense','quantity','price','duration') else 1)
    dates=[c['column'] for c in report['columns'] if c['recognized_dates']]
    dates.sort(key=lambda c:0 if approved.get(c)=='date' else 1)
    dimensions=[c['column'] for c in report['columns'] if c['inferred_type'] in ('category','text') and 1<=c['distinct']<=100]
    return numeric,dates,dimensions


def executive(engine,report,metric,scope):
    st.subheader('What matters in this selection?')
    result=engine.metric(metric); values=dict(zip(result['columns'],result['rows'][0]))
    a,b,c=st.columns(3)
    a.metric('Selected rows',f'{values["records"]:,}')
    b.metric(metric.name,'Unavailable' if values['value'] is None else f'{values["value"]:,.4g}',help=metric.description)
    c.metric('Fields',len(engine.info['columns']))
    st.caption(f'{metric.units} · {metric.description} · {metric.limitations}')
    st.write('**Row meaning:** '+engine.info['grain'])
    numeric,dates,dims=fields(report)
    left,right=st.columns([1.3,1])
    dimension=None; dt=None
    with left:
        if dates:
            dt=st.selectbox('Executive time field',dates)
            draw(visuals.trend(engine.trend(dt,metric),f'How does {metric.name} change over time?',metric.units),'ue-time-'+scope)
            st.caption('First and last observed periods may be incomplete. Missing periods are gaps, not zero.')
    with right:
        if dims:
            dimension=st.selectbox('Executive comparison field',dims)
            draw(visuals.ranked(engine.groups(dimension,metric),f'Which {dimension} groups lead?',metric.units),'ue-groups-'+scope)
            st.caption('Top 15 displayed; this view does not include an invented Other category.')
    st.subheader('Findings worth investigating')
    findings=discover(engine,dimension,metric,dt)
    if not findings: st.info('No concentration or missingness finding meets the current discovery rules. Use the explorer for more specific questions.')
    for item in findings:
        with st.container(border=True):
            st.write('**'+item['finding']+'**'); st.caption(item['context'])
            st.write(item['investigation']); st.caption(item['limitations'])
            with st.expander('Finding evidence'): st.json(item['evidence'])
    evidence(result)
    with st.expander('Recommended analytical questions'):
        for choice in visuals.recommendations(report):
            st.write('**'+choice['question']+'** — '+choice['reason'])


def explorer(engine,report,metric,scope):
    numeric,dates,dims=fields(report)
    options=['Ranked groups','Numeric distribution','Numeric relationship','Calendar trend','Distribution by group','Two-dimensional counts','Records']
    choice=st.selectbox('What would you like to understand?',options)
    if choice=='Ranked groups':
        dimension=st.selectbox('Group by',engine.info['columns'])
        top=st.slider('Top categories',5,30,15)
        result=engine.groups(dimension,metric,top)
        selection_epoch=st.session_state.get('ue-selection-epoch',0)
        if st.button('Reset chart selection'):
            st.session_state['ue-selection-epoch']=selection_epoch+1; st.rerun()
        event=draw(visuals.ranked(result,f'{metric.name} by {dimension}',metric.units,scope),
                   'ux-bars-'+scope+str(selection_epoch),select=True)
        st.caption(f'Top {top} categories displayed. Choose Box Select in the chart toolbar, then drag around a bar to filter its distribution and supporting records. All shared filters remain active.')
        points=event.selection.points
        drilled=engine
        if points:
            custom=points[-1].get('customdata',[])
            if len(custom)==2 and custom[1]==scope:
                value=custom[0]; drilled=Engine(engine.workspace,engine.table,[*engine.filters,Filter(dimension,'eq',value)])
                st.info(f'Chart selection: {dimension} = {value if value is not None else "Missing"}')
                if numeric:
                    column=st.selectbox('Selected-segment numeric field',numeric)
                    draw(visuals.histogram(drilled.distribution(column),'Selected segment: '+column),'ux-filtered-'+scope)
        evidence(result); record_view(drilled,scope+'-'+hashlib.sha256(repr(drilled.filters).encode()).hexdigest()[:10])
    elif choice=='Numeric distribution':
        if not numeric: st.info('No finite numeric fields are available. Review type conversions in Data Quality.'); return
        col=st.selectbox('Numeric field',numeric); bins=st.slider('Distribution bins',5,80,25)
        result=engine.distribution(col,bins)
        draw(visuals.histogram(result,'How is '+col+' distributed?'),'ux-hist-'+scope)
        st.dataframe(frame_of(engine.numeric_summary(col)),hide_index=True,width='stretch'); evidence(result)
    elif choice=='Numeric relationship':
        if len(numeric)<2: st.info('Two numeric fields are required.'); return
        x=st.selectbox('Horizontal field',numeric); y=st.selectbox('Vertical field',numeric,index=1)
        result=engine.scatter(x,y); data=frame_of(result)
        fig=px.scatter(data,x='x',y='y',hover_data=['_row_id'],opacity=.6,color_discrete_sequence=[visuals.TEAL])
        draw(visuals.style(fig,f'How do {x} and {y} relate?',x,y),'ux-scatter-'+scope)
        st.caption(str(result['method'])); evidence(result)
    elif choice=='Calendar trend':
        if not dates: st.info('A usable date field is required. Convert explicit slash dates in Data Quality.'); return
        col=st.selectbox('Time field',dates); grain=st.selectbox('Time grain',['day','week','month','quarter','year'],index=2)
        result=engine.trend(col,metric,grain)
        draw(visuals.trend(result,f'{metric.name} over time',metric.units),'ux-trend-'+scope)
        st.caption('Missing/invalid dates are excluded. Edge periods may be incomplete. Gaps remain missing.'); evidence(result)
    elif choice=='Distribution by group':
        if not numeric or not dims: st.info('A numeric field and a categorical grouping are required.'); return
        col=st.selectbox('Distribution field',numeric); group=st.selectbox('Distribution grouping',dims)
        from .profile import numeric_expression
        # Exact full-data quantiles avoid materializing every record in the browser.
        result=engine.run(f'''SELECT __group AS category,count(__numeric) AS n,
            min(__numeric) AS minimum,quantile_cont(__numeric,.25) AS q1,median(__numeric) AS median,
            quantile_cont(__numeric,.75) AS q3,max(__numeric) AS maximum FROM
            (SELECT {engine.column(group)} AS __group,{numeric_expression(col)} AS __numeric FROM selected)
            GROUP BY 1 ORDER BY n DESC LIMIT 15''')
        data=frame_of(result)
        fig=go.Figure(go.Box(q1=data.q1,median=data['median'],q3=data.q3,lowerfence=data.minimum,upperfence=data.maximum,
            x=data.category.map(str),name=col,marker_color=visuals.TEAL))
        draw(visuals.style(fig,'How do group distributions differ?',group,col),'ux-box-'+scope)
        st.caption('Full-data quartiles; whiskers show observed min/max, not Tukey fences. Top 15 groups by finite count.'); evidence(result)
    elif choice=='Two-dimensional counts':
        if len(dims)<2: st.info('Two grouping dimensions are required.'); return
        x=st.selectbox('Column grouping',dims); y=st.selectbox('Row grouping',dims,index=1)
        cx=engine.categories(x); cy=engine.categories(y)
        if len(cx['rows'])>20 or len(cy['rows'])>20 or cx['truncated'] or cy['truncated']:
            st.info('Filter to at most 20 values per dimension so the heatmap remains complete and readable.'); return
        result=engine.run(f'SELECT {engine.column(x)} AS x,{engine.column(y)} AS y,count(*) AS records FROM selected GROUP BY 1,2')
        data=frame_of(result)
        data['x']=data.x.map(lambda v:'Missing' if v is None else str(v)); data['y']=data.y.map(lambda v:'Missing' if v is None else str(v))
        pivot=data.pivot(index='y',columns='x',values='records').fillna(0)
        draw(visuals.style(px.imshow(pivot,color_continuous_scale='Teal',labels={'color':'Records'}),'Where are recorded rows concentrated?',x,y),'ux-heat-'+scope)
        evidence(result)
    else: record_view(engine,scope)


def advanced_page(engine,report,metric,scope):
    numeric,dates,dims=fields(report); columns=engine.info['columns']
    method=st.selectbox('Analytical method',['Mean confidence interval','Welch group comparison','Correlation and regression',
        'Time-series changes','Monthly baseline forecast','Record outliers','Time anomaly screening',
        'Customer RFM','Monthly retention cohorts','Ordered event funnel','Text frequencies','Categorical association','Exploratory clustering'])
    col=dt=group=None
    if method in ('Mean confidence interval','Welch group comparison','Correlation and regression','Record outliers','Customer RFM','Text frequencies'):
        if not numeric and method!='Text frequencies': st.info('A finite numeric field is required; review type conversion in Data Quality.'); return
        col=st.selectbox('Text field' if method=='Text frequencies' else 'Analysis value field',columns if method=='Text frequencies' else numeric,key='ua-value-'+method)
    if method in ('Time-series changes','Monthly baseline forecast','Time anomaly screening','Customer RFM','Monthly retention cohorts','Ordered event funnel'):
        if not dates: st.info('A recognized date field is required; review explicit date parsing in Data Quality.'); return
        dt=st.selectbox('Analysis time field',dates,key='ua-time-'+method)
    if method in ('Welch group comparison','Customer RFM','Monthly retention cohorts','Ordered event funnel'):
        group=st.selectbox('Analysis grouping / entity',dims+[c for c in columns if c not in dims],key='ua-group-'+method)
    extra={}
    if method=='Welch group comparison':
        values=[r[0] for r in engine.categories(group)['rows']]
        if len(values)<2: st.info('At least two represented groups are required.'); return
        extra['first']=st.selectbox('First group',values); extra['second']=st.selectbox('Second group',values,index=1)
    elif method=='Correlation and regression': extra['y']=st.selectbox('Outcome numeric field',numeric or columns,index=min(1,len(numeric or columns)-1))
    elif method=='Monthly baseline forecast': extra['horizon']=st.slider('Forecast months',1,12,3)
    elif method=='Customer RFM':
        extra['transaction']=st.selectbox('Transaction identifier',columns)
        extra['as_of']=st.date_input('RFM as-of date',value=date.today())
    elif method=='Monthly retention cohorts':
        extra['cutoff']=st.date_input('First day after last complete month · exclusive',value=date.today().replace(day=1))
        extra['confirmed']=st.checkbox('I confirm the included months have complete observation coverage.')
    elif method=='Ordered event funnel':
        extra['event']=st.selectbox('Event name field',columns)
        extra['steps']=st.multiselect('Ordered funnel steps · selection order matters',[r[0] for r in engine.categories(extra['event'])['rows'] if r[0] is not None])
        extra['window']=st.slider('Conversion window in days',1,90,7)
        extra['end']=st.date_input('Observation end date · exclusive',value=date.today())
        extra['confirmed']=st.checkbox('I confirm event identities, timestamp order and observation completeness.')
    elif method=='Categorical association':
        extra['first']=st.selectbox('First categorical field',dims or columns)
        extra['second']=st.selectbox('Second categorical field',dims or columns,index=min(1,len(dims or columns)-1))
    elif method=='Exploratory clustering':
        extra['features']=st.multiselect('Numeric clustering features',numeric)
        extra['k']=st.slider('Number of clusters',2,8,3)
        extra['confirmed']=st.checkbox('I have reviewed feature units, outliers and the independent entity grain.')
    st.caption('Methods can reject unsuitable inputs. Statistical significance is not business significance; observed contributions are not proven causes. No LLM is used.')
    if not st.button('Run selected analysis',type='primary'): return
    if method=='Mean confidence interval': result=mean_interval(engine,col)
    elif method=='Welch group comparison': result=welch_test(engine,col,group,extra['first'],extra['second'])
    elif method=='Correlation and regression': result=regression(engine,col,extra['y'])
    elif method=='Time-series changes': result=advanced.time_analysis(engine,dt,metric)
    elif method=='Monthly baseline forecast': result=advanced.forecast(engine,dt,metric,extra['horizon'])
    elif method=='Record outliers': result=advanced.outliers(engine,col)
    elif method=='Time anomaly screening': result=advanced.time_anomalies(engine,dt,metric)
    elif method=='Customer RFM': result=advanced.rfm(engine,group,extra['transaction'],dt,col,extra['as_of'])
    elif method=='Monthly retention cohorts':
        if not extra['confirmed']: st.warning('Confirm the observation window before calculating retention.'); return
        result=advanced.cohorts(engine,group,dt,extra['cutoff'])
    elif method=='Ordered event funnel':
        if not extra['confirmed']: st.warning('Confirm sequence and observation completeness before calculating conversion.'); return
        result=advanced.funnel(engine,group,extra['event'],dt,extra['steps'],extra['window'],extra['end'])
    elif method=='Text frequencies': result=advanced.text_terms(engine,col)
    elif method=='Categorical association': result=categorical_association(engine,extra['first'],extra['second'])
    else:
        if not extra['confirmed']: st.warning('Review feature meanings and row grain before clustering.'); return
        result=cluster(engine,extra['features'],extra['k'])
    st.info(result.get('assumptions',result.get('limitations','Review applicability and source coverage.')))
    if 'data' in result:
        data=result['data']; st.dataframe(data,hide_index=True,width='stretch')
        if method=='Monthly retention cohorts':
            pivot=data.pivot(index='cohort',columns='month_offset',values='retention')
            draw(visuals.style(px.imshow(pivot,zmin=0,zmax=1,color_continuous_scale='Teal',labels={'color':'Retention'}),
                               'Observed entity retention by cohort','Months since first observed activity','Cohort'),'ua-cohort-'+scope)
        elif method=='Monthly baseline forecast':
            fig=go.Figure()
            for field in ['lower','upper','forecast']:
                fig.add_trace(go.Scatter(x=data.period,y=data[field],name=field,mode='lines',fill='tonexty' if field=='upper' else None))
            draw(visuals.style(fig,'Baseline forecast and empirical error band','Month',metric.units),'ua-forecast-'+scope)
        elif method=='Ordered event funnel':
            draw(visuals.style(go.Figure(go.Funnel(y=data.step,x=data.entities)),'Where do eligible entities drop off?',None,'Entities'),'ua-funnel-'+scope)
        elif method=='Exploratory clustering':
            plotted=data.head(2000).copy(); plotted['cluster']=plotted.cluster.astype(str)
            draw(visuals.style(px.scatter(plotted,x='projection_1',y='projection_2',color='cluster',hover_data=['_row_id'],
                color_discrete_sequence=visuals.COLORS),'Exploratory clusters · first 2,000 projected rows','Projection 1','Projection 2'),'ua-clusters-'+scope)
            st.dataframe(result['centroids'],hide_index=True,width='stretch')
    facts={k:v for k,v in result.items() if k not in ('evidence','data','centroids','summary','assumptions','limitations')}
    if facts:
        if 'terms' in facts:
            st.dataframe(pd.DataFrame(facts['terms'],columns=['term','occurrences']),hide_index=True)
            st.dataframe(pd.DataFrame(facts['phrases'],columns=['phrase','occurrences']),hide_index=True)
        else:
            def display(value):
                if isinstance(value,float): return f'{value:.5g}'
                if isinstance(value,dict): return '; '.join(str(k)+': '+display(v) for k,v in value.items())
                if isinstance(value,(list,tuple)): return ', '.join(display(v) for v in value)
                return str(value)
            st.dataframe(pd.DataFrame([{'Result':k.replace('_',' ').capitalize(),'Value':display(v)} for k,v in facts.items()]),hide_index=True,width='stretch')
    evidence(result['evidence'])
    st.download_button('Download analysis results and evidence',json.dumps(result,default=lambda v:v.to_dict('records') if isinstance(v,pd.DataFrame) else str(v),indent=2),
                       file_name='advanced-analysis.json',mime='application/json')


def investigation(engine,report,metric,scope):
    numeric,dates,dims=fields(report)
    st.subheader('Which segments explain the recorded change?')
    st.caption('Additive contribution accounting, not a causal explanation. Use equal, nonoverlapping periods with verified reporting coverage.')
    if dates and dims:
        dim=st.selectbox('Contribution grouping',dims); dt=st.selectbox('Contribution time field',dates)
        a,b=st.columns(2)
        earlier=a.date_input('Earlier inclusive period',(date(2025,1,1),date(2025,1,31)))
        later=b.date_input('Later inclusive period',(date(2025,3,1),date(2025,3,31)))
        if st.button('Calculate segment contributions') and len(earlier)==2 and len(later)==2:
            result=engine.compare_periods(dim,metric,dt,earlier,later)
            st.dataframe(frame_of(result),hide_index=True,width='stretch')
            draw(visuals.contribution(result,'Segment contribution to metric change',metric.units),'ui-waterfall-'+scope); evidence(result)
        with st.expander('Separate a proportion change into segment mix and within-segment rates'):
            candidates=[Metric('Records'),*[m for m in load_metrics(engine.workspace,engine.table) if m.additive]]
            if len(candidates)<2:
                st.info('Define separate additive numerator and denominator metrics in Executive Intelligence first. For example, count_if("status","==","Yes") and count_if("status","==","Yes") + count_if("status","==","No").')
            else:
                numerator=st.selectbox('Proportion numerator',list(range(len(candidates))),format_func=lambda i:candidates[i].name)
                denominator=st.selectbox('Proportion denominator',list(range(len(candidates))),format_func=lambda i:candidates[i].name)
                if st.button('Decompose the observed proportion change') and len(earlier)==2 and len(later)==2:
                    result=proportion_change(engine,dim,dt,candidates[numerator],candidates[denominator],earlier,later)
                    st.write(f'Observed proportion: {result["rate_earlier"]:.2%} → {result["rate_later"]:.2%}. '
                             f'Mix contribution: {result["mix_contribution"]*100:+.3f} percentage points; '
                             f'within-segment rate contribution: {result["rate_contribution"]*100:+.3f} percentage points.')
                    st.dataframe(result['data'],hide_index=True,width='stretch')
                    st.caption(result['limitations']); evidence(result['evidence'])
    else: st.info('Contribution analysis needs a date field and a grouping dimension.')
    relations={k:r for k,r in load_relationships(engine.workspace).items() if r['left']==engine.table}
    if relations:
        st.subheader('Analyze a metric across related tables')
        name=st.selectbox('Validated relationship',list(relations)); rel=relations[name]
        dim=st.selectbox('Related-table dimension',engine.workspace.columns(rel['right']))
        if st.button('Calculate related-table groups'):
            result=related_groups(engine,rel,dim,metric)
            draw(visuals.ranked(result,'Related dimension comparison',metric.units),'ui-related-'+scope)
            st.dataframe(frame_of(result),hide_index=True,width='stretch'); evidence(result)
    record_view(engine,scope)


def analyst(engine,report,metric,scope):
    st.subheader('Ask a question, inspect the calculation')
    st.caption('The selected explicit metric governs every answer. The optional model only chooses a validated analysis plan; numerical answers are produced by the local engine.')
    numeric,dates,dims=fields(report)
    dimension=st.selectbox('Analyst grouping',[None]+dims)
    dt=st.selectbox('Analyst date field',[None]+dates)
    question=st.text_input('Your analytical question','Summarize the selected metric')
    provider=st.checkbox('Use optional external model for planning')
    consent=st.checkbox('I authorize sending this question, column names and selected metric definition. No source records or calculated values will be sent.',disabled=not provider)
    if st.button('Calculate evidence-backed answer',type='primary'):
        result=assistant.answer(engine,question,metric,dimension=dimension,date_column=dt,use_provider=provider,consent=consent)
        st.write(result['answer']); st.caption(result['mode']); st.caption(result['limitations'])
        st.dataframe(frame_of(result['evidence']),hide_index=True,width='stretch'); evidence(result['evidence'])
        if result['usage']: st.write('Provider-reported usage:',result['usage'])


def reports(engine,report,metric,scope):
    st.subheader('Create a reproducible analytical report')
    with st.expander('Re-run a saved analysis manifest'):
        saved=st.file_uploader('Analysis manifest JSON',type=['json'])
        if saved is not None and st.button('Reproduce saved analysis'):
            if saved.size>1024*1024: raise ValueError('Analysis manifests must be smaller than 1 MiB.')
            restored,definition=replay(engine.workspace,json.loads(saved.getvalue()))
            st.dataframe(frame_of(restored.metric(definition)),hide_index=True,width='stretch')
            st.download_button('Download reproduced report',html_report(restored,definition),file_name='reproduced-report.html',mime='text/html')
            st.caption('Reproduction uses the saved source hash, immutable version, metric and filters. The currently active table version is not changed.')
    title=st.text_input('Report title',engine.table+' — analytical findings')
    _,dates,dims=fields(report)
    dimension=st.selectbox('Report grouping',[None]+dims)
    dt=st.selectbox('Report date field',[None]+dates)
    if st.button('Build report from this selection',type='primary'):
        results=[]; figures=[]
        if dimension:
            item=engine.groups(dimension,metric); results.append(item)
            figures.append(visuals.ranked(item,metric.name+' by '+dimension,metric.units))
        if dt:
            item=engine.trend(dt,metric); results.append(item)
            figures.append(visuals.trend(item,metric.name+' over time',metric.units))
        findings=discover(engine,dimension,metric,dt)
        html=html_report(engine,metric,findings,results,figures,title)
        manifest=configuration(engine,metric)
        st.download_button('Download standalone HTML report',html,file_name='insightpilot-report.html',mime='text/html')
        st.download_button('Download reusable analysis manifest',json.dumps(manifest,default=str,indent=2),file_name='analysis-manifest.json',mime='application/json')
        summary=engine.metric(metric)
        st.download_button('Download KPI table · CSV',frame_of(summary).to_csv(index=False),file_name='kpi.csv',mime='text/csv')
        st.download_button('Download KPI table · Excel',excel_export(summary),file_name='kpi.xlsx')
        st.success('Report calculated from the current filters and immutable source version. It includes definitions, queries, source hash, transformations, findings, charts and limitations.')
    st.caption('HTML is self-contained and can be printed to PDF in your browser. No native PDF renderer or native Power BI report generation is claimed. Existing Power BI preparation remains available in its original workspaces.')


def _main():
    st.title('Universal Intelligence')
    st.write('Understand unfamiliar data. Define what matters. Follow each finding to its evidence.')
    configured=os.environ.get('INSIGHTPILOT_UNIVERSAL_WORKSPACE')
    directory=Path(configured) if configured else Path(st.session_state.get('universal-directory','data/universal/default'))
    if not (directory/'workspace.duckdb').is_file(): workspace=Workspace.create(directory)
    else: workspace=Workspace(directory)
    if not configured:
        directories=sorted(Path('data/universal').glob('*/workspace.duckdb'))
        options=[str(p.parent) for p in directories]
        selected=st.sidebar.selectbox('Analytics workspace',options,index=options.index(str(directory)) if str(directory) in options else 0)
        directory=Path(selected); workspace=Workspace(directory)
        st.session_state['universal-directory']=str(directory)
        if st.sidebar.button('Create independent workspace'):
            path=Path('data/universal')/uuid.uuid4().hex[:12]; Workspace.create(path)
            st.session_state['universal-directory']=str(path); st.session_state['universal-page']=PAGES[0]; st.rerun()
    page=st.sidebar.radio('Universal analytics',PAGES,key='universal-page')
    if page==PAGES[0]: data_hub(workspace); return
    names=list(workspace.tables())
    if not names: st.info('Import one or more tables in Data Hub to begin. No sample data or findings are silently substituted.'); return
    table=st.sidebar.selectbox('Analysis table',names,key='universal-table-'+hashlib.sha256(str(directory).encode()).hexdigest()[:8])
    info=workspace.info(table)
    with st.spinner('Profiling the current table version…'):
        report=cached_profile(str(workspace.directory),table,info['current'])
    report=report|{'approved_roles':info['roles']}
    if info['source']['origin']=='synthetic': st.warning('SYNTHETIC DATA · Software validation only; no genuine business findings are implied.')
    else:
        label={'verified_public_source_extract':'Public source extract with pinned source hash',
               'user_upload':'User-uploaded data · source authenticity unverified',
               'database_extract':'Local read-only database snapshot','postgres_extract':'Read-only PostgreSQL snapshot',
               'api_extract':'Configured API extract'}.get(info['source']['origin'],'Imported source')
        st.caption(label+' · Review source provenance, coverage and field definitions in Data Quality.')
    if page==PAGES[1]:
        st.caption('Data Quality describes the full current table version. Shared analysis filters apply to the analytical pages.')
        quality_editor(workspace,table,report); return
    filters=sidebar_filters(workspace,table,report); engine=Engine(workspace,table,filters)
    metrics=[Metric('Records')]+load_metrics(workspace,table)
    index=st.sidebar.selectbox('Analysis metric',list(range(len(metrics))),format_func=lambda i:metrics[i].name,
        key='universal-metric-'+hashlib.sha256(str(workspace.directory).encode()).hexdigest()[:8]+'-'+table)
    metric=metrics[index]
    metric.compile(info['columns'],info['roles'])
    st.caption(f'{table} · {engine.count():,} selected / {info["rows"]:,} rows · {page} · metric: {metric.name} ({metric.units})')
    scope=hashlib.sha256((str(workspace.directory)+info['current']+repr(filters)+repr(metric)).encode()).hexdigest()[:16]
    if page==PAGES[2]:
        metric_editor(workspace,table); executive(engine,report,metric,scope)
    elif page==PAGES[3]: explorer(engine,report,metric,scope)
    elif page==PAGES[4]: advanced_page(engine,report,metric,scope)
    elif page==PAGES[5]: investigation(engine,report,metric,scope)
    elif page==PAGES[6]: analyst(engine,report,metric,scope)
    else: reports(engine,report,metric,scope)


def main():
    try: _main()
    except ValueError as exc: st.warning(str(exc))
    except httpx.HTTPError as exc:
        status=f'HTTP {exc.response.status_code}' if isinstance(exc,httpx.HTTPStatusError) else type(exc).__name__
        st.warning(f'The external service request could not complete ({status}). Check the configured endpoint, network access and credentials. Local datasets and deterministic analysis remain available.')
