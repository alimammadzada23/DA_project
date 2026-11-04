"""General-purpose analytics workspace, extending the existing Streamlit shell."""
from datetime import date, timedelta
from dataclasses import asdict
import duckdb
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
from .context import AnalysisContext, coverage
from .story import interpretation, plan, label, format_value, calendar_grain
from .sql_lab import SQLLab
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

PAGES=['Overview','Data Hub','Quality & Preparation','Explore','SQL Lab','Advanced','AI Analyst','Reports']


def download(*args,**kwargs):
    # Exporting one artifact must not erase the other artifacts on a rerun.
    return st.download_button(*args,on_click='ignore',**kwargs)


@st.cache_data(max_entries=24,show_spinner=False)
def cached_profile(path,table,version):
    return Workspace(path).profile(table)


def draw(figure,key,*,select=False,result=None):
    kwargs={'on_select':'rerun','selection_mode':('points','box')} if select else {}
    event=st.plotly_chart(figure,width='stretch',key=key,theme=None,config={'displaylogo':False,
        'toImageButtonOptions':{'format':'png','scale':2}},**kwargs)
    st.caption(st.session_state.get('analysis-caption',''))
    with st.expander('Chart data, save and export',expanded=False):
        data=visuals.plotted_data(figure)
        download('Download plotted values · CSV (formula-safe text)',safe_spreadsheet_frame(data).to_csv(index=False),
                           key='data-'+key,file_name='chart-values.csv',mime='text/csv')
        download('Download interactive HTML',figure.to_html(include_plotlyjs=True),key='html-'+key,
                           file_name='insightpilot-chart.html',mime='text/html')
        st.caption('Use the chart’s camera button for a PNG image. Expand with the Plotly toolbar; box-select supported bars to inspect records.')
        active=st.session_state.get('_active-analysis')
        if active and result:
            name=st.text_input('Finding or chart name',key='block-name-'+key,value=str(figure.layout.title.text or 'Chart'))
            if st.button('Save chart and evidence to Reports',key='block-save-'+key):
                engine,metric=active
                engine.workspace.save_object('block',name,{'analysis':configuration(engine,metric),'figure':figure.to_json(),'evidence':result})
                st.success('Saved this chart, executed evidence and analytical context.')
    return event


def evidence(result):
    with st.expander('Inspect calculations and provenance'):
        st.code(result['sql'],language='sql')
        st.json(result)
        download('Download evidence JSON',json.dumps(result,default=str,indent=2),
                           file_name='analysis-evidence.json',mime='application/json',key='ev-'+result['evidence_id'])


def sidebar_filters(workspace,table,report):
    filters=[]
    with st.sidebar.expander('Shared analysis filters'):
        tag=workspace.info(table)['current']
        epoch_key='universal-filter-epoch-'+tag
        epoch=st.session_state.get(epoch_key,0)
        if st.button('Reset analysis filters'):
            st.session_state[epoch_key]=epoch+1
            st.session_state.pop('restored-analysis-'+report.get('state_key',''),None)
            st.rerun()
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
        dates=[c for c in report['columns'] if c['column']==report.get('date_basis')]
        if dates and st.checkbox('Apply a date window',key='uf-date-enabled-'+suffix):
            col=report['date_basis']; st.caption('Date window uses '+col)
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
    download('Download this page · Excel (formula-safe text)',excel_export(result),file_name='filtered-records.xlsx',
                       key='ur-xlsx-'+key,mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    download('Download this page · CSV (formula-safe text)',csv_export(result),file_name='filtered-records.csv',
                       key='ur-csv-'+key,mime='text/csv')
    evidence(result)


def fields(report):
    approved=report.get('approved_roles',{})
    numeric=[c['column'] for c in report['columns'] if c['numeric_values'] and (c['inferred_type']!='identifier' or approved.get(c['column']) in ('measure','revenue','expense','quantity','price','duration'))
             and approved.get(c['column']) not in ('identifier','date','customer','product','transaction','geography','category','status','text')
             and not c['recognized_dates']]
    numeric.sort(key=lambda c:0 if approved.get(c) in ('measure','revenue','expense','quantity','price','duration') else 1)
    dates=[report['date_basis']] if report.get('date_basis') else []
    dimensions=[c['column'] for c in report['columns'] if 1<=c['distinct']<=100 and
                (approved.get(c['column']) in ('category','status','geography','company','product') or c['inferred_type']=='category')]
    return numeric,dates,dimensions


def executive(engine,report,metric,scope):
    st.subheader(metric.name+' · '+engine.table)
    result=engine.metric(metric); values=dict(zip(result['columns'],result['rows'][0]))
    a,b,c=st.columns(3)
    a.metric('Selected rows',f'{values["records"]:,}')
    b.metric(metric.name,format_value(values['value']),help=metric.description)
    c.metric('Defined fields',f'{sum(v!="unassigned" for v in engine.info["roles"].values())} / {len(engine.info["columns"])}')
    st.write(metric.description)
    st.caption(metric.units+' · '+metric.limitations)
    st.caption('Row grain: '+engine.info['grain'])
    if metric.components:
        components=[]
        for part,formula in metric.components.items():
            components.append({'Component':part,'Formula':formula,'Value':values[part]})
        st.dataframe(pd.DataFrame(components),hide_index=True,width='stretch')
        st.caption('Eligible denominator: '+(metric.denominator or 'Review the denominator formula above. Zero denominators are unavailable.'))
    charts=plan(engine,report,metric)
    if engine.context.date_basis and not any(c['kind']=='trend' for c in charts):
        st.info('Time view withheld: this date basis has fewer than two observed periods at the chosen grain, or has no usable ISO dates. Choose a finer calendar grain or review date conversion in Quality & Preparation.')
    if not engine.info.get('semantics',{}).get('reviewed'):
        st.info('Review what one row represents, field roles, units and timestamp purposes in Quality & Preparation. Record counts describe structure; they do not establish business KPIs.')
    if not charts:
        st.info('Choose a comparison field or a reviewed date basis in the sidebar, or define a measure in the metric dictionary. InsightPilot does not infer a business objective from column order.')
        st.dataframe(pd.DataFrame([{'Field':x['column'],'Type':x['inferred_type'],'Distinct':x['distinct'],'Missing':x['missing']} for x in report['columns']]),hide_index=True,width='stretch')
    for index,chart in enumerate(charts):
        field=chart['field']
        if chart['kind']=='trend':
            item=engine.trend(field,metric,engine.context.time_grain); figure=visuals.trend(item,chart['title'],metric.units)
        elif chart['kind']=='ranked':
            item=engine.groups(field,metric); figure=visuals.ranked(item,chart['title'],metric.units)
        else:
            item=engine.distribution(field); figure=visuals.histogram(item,chart['title'])
        draw(figure,'overview-'+scope+str(index),result=item)
        st.write(interpretation(item,metric,field)); evidence(item)
    findings=discover(engine,engine.context.comparison,metric,engine.context.date_basis)
    if findings:
        st.subheader('Evidence to investigate')
    for item in findings:
        with st.container(border=True):
            st.write('**'+item['finding']+'**'); st.caption(item['context'])
            st.write(item['investigation']); st.caption(item['limitations'])
            with st.expander('Finding evidence'): st.json(item['evidence'])
    evidence(result)
    record_view(engine,scope)


def explorer(engine,report,metric,scope):
    numeric,dates,dims=fields(report)
    options=['Ranked groups','Numeric distribution','Numeric relationship','Calendar trend','Distribution by group','Two-dimensional counts','Records']
    choice=st.selectbox('What would you like to understand?',options,key='explore-chart-type')
    if choice=='Ranked groups':
        choices=[None]+engine.info['columns']
        dimension=st.selectbox('Group by',choices,index=choices.index(engine.context.comparison) if engine.context.comparison in choices else 0,
                               format_func=lambda x:'Choose a grouping field' if x is None else x,key='explore-groupby')
        if dimension is None: st.info('Choose a field to compare. The shared comparison field supplies the default when selected.'); return
        top=st.slider('Top categories',5,30,15,key='explore-top')
        result=engine.groups(dimension,metric,top)
        selection_epoch=st.session_state.get('ue-selection-epoch',0)
        if st.button('Reset chart selection'):
            st.session_state['ue-selection-epoch']=selection_epoch+1; st.rerun()
        event=draw(visuals.ranked(result,f'{metric.name} by {dimension}',metric.units,scope),
                   'ux-bars-'+scope+str(selection_epoch),select=True,result=result)
        st.caption(f'Top {top} categories displayed. Choose Box Select in the chart toolbar, then drag around a bar to filter its distribution and supporting records. All shared filters remain active.')
        points=event.selection.points
        drilled=engine
        if points:
            custom=points[-1].get('customdata',[])
            if len(custom)==2 and custom[1]==scope:
                value=custom[0]; drilled=Engine(engine.workspace,engine.table,[*engine.filters,Filter(dimension,'eq',value)],
                                               version=engine.info['current'],context=engine.context)
                st.info(f'Chart selection: {dimension} = {value if value is not None else "Missing"}')
                if numeric:
                    column=st.selectbox('Selected-segment numeric field',numeric)
                    draw(visuals.histogram(drilled.distribution(column),'Selected segment: '+column),'ux-filtered-'+scope)
        evidence(result); record_view(drilled,scope+'-'+hashlib.sha256(repr(drilled.filters).encode()).hexdigest()[:10])
    elif choice=='Numeric distribution':
        if not numeric: st.info('No finite numeric fields are available. Review type conversions in Data Quality.'); return
        col=st.selectbox('Numeric field',numeric); bins=st.slider('Distribution bins',5,80,25)
        result=engine.distribution(col,bins)
        draw(visuals.histogram(result,'Distribution of '+label(col)),'ux-hist-'+scope,result=result)
        st.write(interpretation(result,metric))
        st.dataframe(frame_of(engine.numeric_summary(col)),hide_index=True,width='stretch'); evidence(result)
    elif choice=='Numeric relationship':
        if len(numeric)<2: st.info('Two numeric fields are required.'); return
        x=st.selectbox('Horizontal field',numeric); y=st.selectbox('Vertical field',numeric,index=1)
        result=engine.scatter(x,y); data=frame_of(result)
        fig=px.scatter(data,x='x',y='y',hover_data=['_row_id'],opacity=.6,color_discrete_sequence=[visuals.TEAL])
        draw(visuals.style(fig,label(y)+' against '+label(x),x,y),'ux-scatter-'+scope,result=result)
        st.caption(str(result['method'])); evidence(result)
    elif choice=='Calendar trend':
        if not dates: st.info('A usable date field is required. Convert explicit slash dates in Data Quality.'); return
        col=engine.context.date_basis; grain=engine.context.time_grain; st.caption('Shared date basis: '+col+' · '+grain)
        result=engine.trend(col,metric,grain)
        draw(visuals.trend(result,f'{metric.name} by {grain} · {label(col)}',metric.units),'ux-trend-'+scope,result=result)
        st.write(interpretation(result,metric))
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
        draw(visuals.style(fig,label(col)+' distribution by '+label(group),group,col),'ux-box-'+scope,result=result)
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
        dt=engine.context.date_basis; st.caption('Shared date basis: '+dt)
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
    download('Download analysis results and evidence',json.dumps(result,default=lambda v:v.to_dict('records') if isinstance(v,pd.DataFrame) else str(v),indent=2),
                       file_name='advanced-analysis.json',mime='application/json')


def investigation(engine,report,metric,scope):
    numeric,dates,dims=fields(report)
    st.subheader('Which segments explain the recorded change?')
    st.caption('Additive contribution accounting, not a causal explanation. Use equal, nonoverlapping periods with verified reporting coverage.')
    if dates and dims:
        dim=engine.context.comparison or st.selectbox('Contribution grouping',dims); dt=engine.context.date_basis
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
    from .analyst_ui import render
    render(engine,report,metric,scope)


def reports(engine,report,metric,scope):
    st.subheader('Create a reproducible analytical report')
    with st.expander('Re-run a saved analysis manifest'):
        saved=st.file_uploader('Analysis manifest JSON',type=['json'])
        if saved is not None and st.button('Reproduce saved analysis'):
            if saved.size>1024*1024: raise ValueError('Analysis manifests must be smaller than 1 MiB.')
            restored,definition=replay(engine.workspace,json.loads(saved.getvalue()))
            st.dataframe(frame_of(restored.metric(definition)),hide_index=True,width='stretch')
            download('Download reproduced report',html_report(restored,definition),file_name='reproduced-report.html',mime='text/html')
            st.caption('Reproduction uses the saved source hash, immutable version, metric and filters. The currently active table version is not changed.')
    title=st.text_input('Report title',engine.table+' — analytical findings')
    _,dates,dims=fields(report)
    dimension=engine.context.comparison
    dt=engine.context.date_basis
    report_key='report-artifacts-'+scope
    if st.button('Build report from this selection',type='primary'):
        results=[]; figures=[]
        if dimension:
            item=engine.groups(dimension,metric); results.append(item)
            figures.append(visuals.ranked(item,metric.name+' by '+dimension,metric.units))
        if dt:
            item=engine.trend(dt,metric,engine.context.time_grain); results.append(item)
            figures.append(visuals.trend(item,metric.name+' by '+engine.context.time_grain+' · '+dt,metric.units))
        findings=discover(engine,dimension,metric,dt)
        st.session_state[report_key]={'html':html_report(engine,metric,findings,results,figures,title),
            'manifest':configuration(engine,metric),'summary':engine.metric(metric)}
    built=st.session_state.get(report_key)
    if built:
        download('Download standalone HTML report',built['html'],file_name='insightpilot-report.html',mime='text/html')
        download('Download reusable analysis manifest',json.dumps(built['manifest'],default=str,indent=2),file_name='analysis-manifest.json',mime='application/json')
        download('Download KPI table · CSV',frame_of(built['summary']).to_csv(index=False),file_name='kpi.csv',mime='text/csv')
        download('Download KPI table · Excel',excel_export(built['summary']),file_name='kpi.xlsx')
        st.success('Report calculated from the current filters and immutable source version. It includes definitions, queries, source hash, transformations, findings, charts and limitations.')
    st.caption('HTML is self-contained and can be printed to PDF in your browser. No native PDF renderer or native Power BI report generation is claimed. Power BI preparation exports the current selection and its metric dictionary below.')


def _main():
    pending=st.session_state.pop('pending-view-layout',None)
    if pending:
        st.session_state['universal-page']=pending['page']
        for key,value in pending.get('presets',{}).items(): st.session_state[key]=value
    st.title('InsightPilot')
    from .ui_workspaces import choose_workspace
    from .workspace_labels import table_labels
    workspace=choose_workspace();directory=workspace.directory
    names=list(workspace.tables())
    if 'universal-page' not in st.session_state:
        st.session_state['universal-page']='Overview' if names else 'Data Hub'
    page=st.sidebar.radio('Navigate',PAGES,key='universal-page')
    if page=='Data Hub' and not names: data_hub(workspace); return
    if not names: st.info('Import a dataset in Data Hub to begin. Your source files and original versions are retained.'); return
    tag=hashlib.sha256(str(workspace.directory).encode()).hexdigest()[:8]
    labels=table_labels(workspace)
    table=st.sidebar.selectbox('Analysis table',names,format_func=labels.get,key='universal-table-'+tag)
    info=workspace.info(table)
    with st.spinner('Profiling the active data version…'):
        report=cached_profile(str(workspace.directory),table,info['current'])
    semantics=info.get('semantics',{})
    report=report|{'approved_roles':info['roles'],'date_basis':semantics.get('date_basis')}
    if info['source']['origin']=='synthetic': st.warning('SYNTHETIC DATA · Software validation only; no business findings are implied.')
    else: st.caption('Source: '+info['source']['origin']+' · '+info['source']['filename']+' · '+info['source']['sha256'][:12])
    state=tag+'-'+table+'-'+info['current']
    saved=workspace.saved_objects('view')
    with st.sidebar.expander('Saved analyses'):
        view=st.selectbox('Saved view',[None]+list(saved),format_func=lambda v:'Choose a view' if v is None else v,key='view-picker-'+state)
        if view and st.button('Restore saved view'):
            restored,m=replay(workspace,saved[view]['analysis'])
            if restored.table!=table or restored.info['current']!=info['current']:
                st.warning('Select the saved table and version in Quality & Preparation before restoring the visible dashboard. Reports can replay older versions directly.')
            else:
                st.session_state['restored-analysis-'+state]=saved[view]['analysis']
                st.session_state['view-epoch-'+state]=st.session_state.get('view-epoch-'+state,0)+1
                st.session_state['universal-filter-epoch-'+info['current']]=st.session_state.get('universal-filter-epoch-'+info['current'],0)+1
                st.session_state['pending-view-layout']=saved[view]['layout']; st.rerun()
    restored=st.session_state.get('restored-analysis-'+state)
    epoch=str(st.session_state.get('view-epoch-'+state,0)); widget=state+'-'+epoch
    preferred=info.get('analysis_defaults',{})
    if preferred.get('version')!=info['current']: preferred={}
    current=AnalysisContext(**restored['context']) if restored else AnalysisContext(**preferred['context']) if preferred else AnalysisContext(date_basis=semantics.get('date_basis'),time_grain=calendar_grain(report,semantics.get('date_basis')))
    dates=[None]+[c for c,r in info['roles'].items() if r=='date' and semantics.get('reviewed') and semantics.get('date_purposes',{}).get(c)]
    date_basis=st.sidebar.selectbox('Date basis',dates,index=dates.index(current.date_basis) if current.date_basis in dates else 0,
        key='basis-'+widget,format_func=lambda x:'No time analysis' if x is None else x+' · '+semantics['date_purposes'][x])
    _,_,dims=fields(report)
    comparison=st.sidebar.selectbox('Comparison field',[None]+dims,index=([None]+dims).index(current.comparison) if current.comparison in dims else 0,
        key='comparison-'+widget,format_func=lambda x:'No grouping selected' if x is None else x)
    grain=st.sidebar.selectbox('Calendar grain',['day','week','month','quarter','year'],index=['day','week','month','quarter','year'].index(current.time_grain),key='grain-'+widget)
    context=AnalysisContext(date_basis,grain,comparison).validate(info)
    report['date_basis']=date_basis
    if coverage(info,report)['date_ambiguity'] and not date_basis: st.info(coverage(info,report)['notice'])
    report['state_key']=state
    filters=sidebar_filters(workspace,table,report)
    if restored:
        st.sidebar.caption('Saved-view filters are active: '+json.dumps(restored['filters'],default=str))
        filters=tuple(Filter(**f) for f in restored['filters'])+filters
        if st.sidebar.button('Clear restored view and filters'):
            st.session_state.pop('restored-analysis-'+state,None); st.rerun()
    engine=Engine(workspace,table,filters,context=context)
    metrics=[Metric('Records')];invalid=[]
    for definition in load_metrics(workspace,table):
        try: definition.compile(info['columns'],info['roles']); metrics.append(definition)
        except ValueError as exc: invalid.append(definition.name+': '+str(exc))
    default=next((i for i,m in enumerate(metrics) if restored and asdict(m)==restored['metric'] or not restored and m.name==preferred.get('metric_name')),
                 1 if len(metrics)==2 and semantics.get('reviewed') else 0)
    pending=st.session_state.pop('select-new-metric-'+str(workspace.directory)+'-'+table,None)
    if pending:
        st.session_state['metric-'+widget]=next((i for i,m in enumerate(metrics) if m.name==pending),default)
    index=st.sidebar.selectbox('Analysis metric',list(range(len(metrics))),index=default,format_func=lambda i:metrics[i].name,key='metric-'+widget)
    metric=metrics[index]; metric.compile(info['columns'],info['roles'])
    workspace.save_analysis_defaults(table,context.dictionary(),metric.name)
    if invalid: st.warning('Saved definitions need review after schema/role changes: '+'; '.join(invalid))
    if len(metrics)>2 and index==0 and not preferred:
        st.info('Several metric definitions are available. Choose the one that answers your question; record counts describe structure.')
    description=f'{table} · {engine.count():,} selected / {info["rows"]:,} rows · {metric.name} ({metric.units})'
    st.caption(description)
    st.session_state['analysis-caption']=description+' · date: '+str(date_basis)+' · filters: '+(repr([(f.column,f.operator,f.value) for f in filters]) if filters else 'all rows')
    st.session_state['_active-analysis']=(engine,metric)
    with st.sidebar.expander('Save this analysis'):
        name=st.text_input('View name',key='view-name-'+state)
        if st.button('Save view and layout'):
            presets={key:st.session_state[key] for key in ('explore-chart-type','explore-groupby','explore-top') if key in st.session_state}
            workspace.save_object('view',name,{'analysis':configuration(engine,metric),'layout':{'page':page,'comparison':comparison,'presets':presets}})
            st.success('Saved metric, date basis, filters, grouping, source version and page.')
    scope=hashlib.sha256((str(workspace.directory)+info['current']+repr(filters)+repr(metric)+repr(context)).encode()).hexdigest()[:16]
    if page=='Data Hub': data_hub(workspace); return
    if page=='Quality & Preparation':
        quality_editor(workspace,table,report); metric_editor(workspace,table)
        dictionary=[]
        for definition in [Metric('Records')]+load_metrics(workspace,table):
            try: dictionary.append(definition.dictionary(info))
            except ValueError as exc: dictionary.append(asdict(definition)|{'validation':'Needs review: '+str(exc),'components':{}})
        st.subheader('Metric dictionary'); st.dataframe(pd.DataFrame(dictionary).drop(columns=['components']),hide_index=True,width='stretch'); return
    if page=='Overview': executive(engine,report,metric,scope)
    elif page=='Explore': explorer(engine,report,metric,scope)
    elif page=='SQL Lab': sql_page(engine,report,metric,scope)
    elif page=='Advanced':
        mode=st.selectbox('Advanced workflow',['Statistical and analytical methods','Investigate a change','Event sequence and process'])
        if mode=='Investigate a change': investigation(engine,report,metric,scope)
        elif mode=='Event sequence and process': process_page(engine,scope)
        else: advanced_page(engine,report,metric,scope)
    elif page=='AI Analyst': analyst(engine,report,metric,scope)
    else: reports(engine,report,metric,scope); saved_reports(engine,metric,scope)


def main():
    try: _main()
    except ValueError as exc: st.warning(str(exc))
    except httpx.HTTPError as exc:
        status=f'HTTP {exc.response.status_code}' if isinstance(exc,httpx.HTTPStatusError) else type(exc).__name__
        st.warning(f'The external service request could not complete ({status}). Local deterministic analysis remains available.')
    except (OSError, duckdb.Error) as exc:
        st.error('The local data operation could not complete: '+str(exc)[:600])


def sql_page(engine,report,metric,scope):
    from streamlit_ace import st_ace
    lab=SQLLab(engine)
    st.subheader('SQL Lab')
    st.caption('One read-only SELECT or CTE over the sources below. Active filters are applied before SQL. Unique-key relationships are required for joins. 768 MB memory, 10-second execution budget, bounded result pages.')
    with st.expander('Available sources and columns',expanded=True): st.json(lab.catalog())
    if st.session_state.get('sql-next-mode'):
        st.session_state['sql-mode']=st.session_state.pop('sql-next-mode')
    mode=st.radio('Query mode',['Guided builder','SQL editor'],horizontal=True,key='sql-mode')
    seed='SELECT * FROM "'+engine.table.replace('"','""')+'" LIMIT 100'
    loaded=st.session_state.get('sql-loaded-'+engine.table,seed)
    saved=engine.workspace.saved_objects('query')
    with st.expander('Saved queries'):
        name=st.selectbox('Named query',[None]+list(saved))
        if name and st.button('Reopen query with its saved context'):
            restored,query=lab.reopen(name)
            st.session_state['sql-loaded-'+engine.table]=query
            st.session_state['sql-reopened-'+engine.table]=saved[name]
            st.session_state['sql-next-mode']='SQL editor'; st.rerun()
    if mode=='Guided builder':
        rel=st.selectbox('Join a validated dimension',[None]+list(lab.relationships),format_func=lambda x:'Single table' if x is None else x)
        columns=list(engine.info['columns'])
        if rel:
            right=lab.relationships[rel]['right']; columns += [right+'.'+c for c in engine.workspace.columns(right)]
            st.caption(json.dumps(lab.relationships[rel]))
        default=[engine.context.comparison] if engine.context.comparison else []
        groups=st.multiselect('Group by dimensions',columns,default=default,max_selections=3)
        by_time=st.checkbox('Group by shared date basis',disabled=not engine.context.date_basis)
        limit=st.number_input('Result limit',1,5000,100)
        descending=st.checkbox('Order metric descending',True)
        query=lab.guided(metric,groups,date=engine.context.date_basis if by_time else None,
                         grain=engine.context.time_grain,relationship=rel,top=int(limit),descending=descending)
        st.code(query,language='sql'); st.caption('Metric: '+metric.formula+' · filters: '+repr(engine.filters))
        if st.button('Edit generated SQL'):
            st.session_state['sql-loaded-'+engine.table]=query; st.session_state['sql-next-mode']='SQL editor'; st.rerun()
    else:
        query=st_ace(value=loaded,language='sql',theme='tomorrow_night',font_size=15,wrap=True,auto_update=True,key='sql-ace-'+hashlib.sha256(loaded.encode()).hexdigest()[:10]) or loaded
        with st.expander('Accessible plain-text editor'):
            use_plain=st.checkbox('Use plain-text input')
            plain=st.text_area('SQL query',value=query,height=200,key='sql-plain-'+engine.table)
            if use_plain: query=plain
        st.caption('Syntax highlighting, line numbers and keyboard editing. Unknown functions, table functions, recursive queries, cross-workspace reads and writes are rejected.')
    reopened=st.session_state.get('sql-reopened-'+engine.table)
    use_saved=st.checkbox('Run using reopened query’s saved filters and version',value=True) if reopened else False
    active=SQLLab(replay(engine.workspace,reopened['analysis'])[0]) if use_saved else lab
    if use_saved: st.caption('Saved query context: '+json.dumps(reopened['analysis']['filters'],default=str)+' · '+reopened['analysis']['version'])
    page=st.number_input('SQL result page',min_value=1,max_value=1001,value=1)
    size=st.selectbox('Rows per page',[100,500,1000,5000])
    run,explain=st.columns(2)
    if explain.button('Explain validated query'):
        item=active.execute(query,explain=True)
        st.code('\n'.join(str(r[-1]) for r in item['rows'])); evidence(item)
    if run.button('Run query',type='primary'):
        item=active.execute(query,page=int(page)-1,size=size)
        st.session_state['sql-result-'+scope]=item
    result=st.session_state.get('sql-result-'+scope)
    if result:
        if result.get('user_sql')!=query.strip().removesuffix(';'):
            st.warning('These results belong to the previously executed query. Run the current SQL to refresh its table, chart and evidence.')
        st.dataframe(frame_of(result),hide_index=True,width='stretch')
        st.caption(f'{len(result["rows"]):,} displayed rows · more rows: {result["truncated"]} · {result["elapsed_seconds"]:.3f} s. Pagination needs a stable ORDER BY for repeatable pages.')
        download('Download query result page CSV',csv_export(result),file_name='query-results.csv')
        download('Download query result page Excel',excel_export(result),file_name='query-results.xlsx')
        evidence(result)
        data=frame_of(result)
        if len(data.columns)>=2 and len(data):
            x=st.selectbox('Result chart dimension',list(data.columns))
            numeric=list(data.select_dtypes(include='number').columns)
            if numeric:
                y=st.selectbox('Result chart value',numeric)
                kind=st.selectbox('Result chart type',['Bar','Line','Scatter'])
                fig={'Bar':px.bar,'Line':px.line,'Scatter':px.scatter}[kind](data,x=x,y=y,color_discrete_sequence=[visuals.TEAL])
                draw(visuals.style(fig,y+' by '+x+' · SQL result',x,y),'sql-chart-'+scope,result=result)
        block_name=st.text_input('Report table block name')
        if st.button('Save query result to Reports'):
            engine.workspace.save_object('block',block_name,{'analysis':configuration(active.engine,metric),'evidence':result})
            st.success('Saved query result and execution evidence.')
    name=st.text_input('Save query as')
    if st.button('Validate and save named query'):
        active.save(name,query,metric); st.success('Saved validated SQL and its version-bound filter context.')


def process_page(engine,scope):
    from .process import analyze_events
    st.subheader('Observed event sequences')
    st.caption('Select an entity and activity field. The shared date basis supplies event time; timestamp ties use source-row order. No process completion rule or causality is inferred.')
    entity=st.selectbox('Event entity identifier',engine.info['columns'])
    activity=st.selectbox('Event activity field',engine.info['columns'],index=min(1,len(engine.info['columns'])-1))
    if st.button('Analyze event sequences'):
        results=analyze_events(engine,entity,activity)
        for name,item in results.items():
            st.write('**'+name.capitalize()+'**'); st.dataframe(frame_of(item),hide_index=True,width='stretch'); evidence(item)


def saved_reports(engine,metric,scope):
    from .exports import selection_package
    st.subheader('Saved findings, charts and query results')
    blocks=engine.workspace.saved_objects('block')
    chosen=st.multiselect('Report blocks',list(blocks))
    if chosen and st.button('Build report from saved evidence'):
        figures=[]; results=[]
        for name in chosen:
            block=blocks[name]
            replay(engine.workspace,block['analysis'])  # Validate retained source and definitions.
            for serialized in block.get('figures',[]) or ([block['figure']] if block.get('figure') else []):
                figures.append(go.Figure(json.loads(serialized)))
            results.append(block['evidence'])
            results.extend(block.get('evidence_bundle',[]))
        html=html_report(engine,metric,evidence=results,figures=figures,title='InsightPilot · saved analytical evidence')
        download('Download assembled report',html,file_name='saved-findings.html')
        st.caption('Each block retains its original SQL, filters, version and source hash. Blocks can represent different selections; inspect their individual evidence.')
    with st.expander('Power BI and full-selection exports'):
        st.caption('Export the active filtered table, metric dictionary, manifest and Power Query template. CSV formula-like text is escaped. Maximum 100,000 rows per package; narrow filters for larger sources. This does not generate or validate a native PBIX file.')
        if st.button('Prepare current selection package'):
            payload=selection_package(engine,metric)
            download('Download selection and Power BI preparation ZIP',payload,file_name='insightpilot-selection.zip',mime='application/zip')
