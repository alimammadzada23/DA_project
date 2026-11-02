"""Run from repository root: streamlit run app.py"""
import json
import os
from pathlib import Path
import uuid
import io
import zipfile
import duckdb
import pandas as pd
import streamlit as st
from insightpilot.agent import Analyst, LIMITATIONS, propose_measure
from insightpilot.analytics import QUESTIONS
from insightpilot.ingest import quality, ingest
from insightpilot.demo import create_demo
from insightpilot.dashboard import Dashboard, Filters

st.set_page_config(page_title='InsightPilot',page_icon='🔎',layout='wide')
st.title('InsightPilot · Enterprise Process Intelligence')
st.caption('Portfolio case study · public anonymized procurement data · hypothetical stakeholder scenario')
if 'database_path' not in st.session_state:
    st.session_state['database_path']=os.environ.get('INSIGHTPILOT_DATABASE','data/analytics/bpi2019.duckdb')
if 'pending_database' in st.session_state:
    st.session_state['database_path']=st.session_state.pop('pending_database')
database = st.sidebar.text_input('DuckDB database',key='database_path')
with st.sidebar.expander('Load an event log or open the demo'):
    st.caption('The official log is obtained from the BPI Challenge 2019 DOI. Upload XES or XES.gz; large imports can also use the CLI.')
    with st.form('import_source'):
        uploaded=st.file_uploader('Original XES log',type=['xes','gz'])
        synthetic=st.checkbox('This upload is synthetic test data')
        import_now=st.form_submit_button('Import XES')
    if import_now:
        if uploaded is None: st.error('Select an XES or XES.gz file first.')
        else:
            directory=Path('data/imports')/str(uuid.uuid4());directory.mkdir(parents=True)
            source=directory/('source.xes.gz' if uploaded.name.lower().endswith('.gz') else 'source.xes')
            try:
                with st.spinner('Preserving source and importing events'):
                    with source.open('wb') as stream:
                        for chunk in iter(lambda:uploaded.read(1024*1024),b''):stream.write(chunk)
                    target=directory/'analytics.duckdb';ingest(source,target,synthetic=synthetic)
                st.session_state['pending_database']=str(target)
                st.rerun()
            except Exception as exc:st.error(f'Import failed; the original upload was retained: {exc}')
    if st.button('Open labeled synthetic demo'):
        target=Path(st.session_state.get('demo_database','data/demo/SYNTHETIC-demo.duckdb'))
        if not target.is_file():
            demo=create_demo(Path('data')/('demo-'+str(uuid.uuid4())))
            target=Path(demo['database'])
        st.session_state['demo_database']=str(target)
        st.session_state['pending_database']=str(target)
        st.rerun()
if not Path(database).is_file():
    st.info('No analytical database loaded. Import the official XES log using the README instructions. Synthetic test data must be explicitly labeled.')
    st.code('insightpilot ingest data/raw/BPI_Challenge_2019.xes data/analytics/bpi2019.duckdb')
    st.stop()
@st.cache_data(ttl=300)
def load_quality(path, modified, size):
    with duckdb.connect(path,read_only=True) as con:
        return quality(con)

def frame(result):
    return pd.DataFrame(result['rows'],columns=result['columns'])

@st.cache_data(ttl=300,max_entries=128)
def dashboard_result(path,modified,size,filters,method,*args):
    return getattr(Dashboard(path,filters),method)(*args)

def reset_filters():
    for key in ['filter_categories','filter_vendors','filter_spend','filter_dates','filter_date_range',
                'filter_repeats','filter_span','filter_span_range','minimum_occurrences']:
        st.session_state.pop(key,None)

try:
    analyst = Analyst(database)
    stat=Path(database).stat()
    report=load_quality(database,stat.st_mtime_ns,stat.st_size)
except Exception as exc:
    st.error(f'Cannot read an InsightPilot analytical database: {exc}')
    st.stop()
if st.session_state.get('active_database') != (database,report['sha256']):
    st.session_state['active_database']=(database,report['sha256'])
    st.session_state['investigations']=[]
    st.session_state.pop('sql_evidence',None)
    reset_filters()
st.warning('SYNTHETIC FIXTURE — no real-company findings' if report['dataset']=='synthetic fixture' else 'Source provenance and official count comparisons require review')
signature=(database,stat.st_mtime_ns,stat.st_size)
options=dashboard_result(*signature,Filters(),'options')
st.sidebar.subheader('Item filters')
st.sidebar.caption('Empty selections include all values. Dates select items by their first recorded event (UTC); all events of selected items are retained.')
st.sidebar.button('Reset filters',on_click=reset_filters)
label=lambda value:'(not recorded)' if value is None else str(value)
categories=st.sidebar.multiselect('Matching categories',options['categories']['values'],format_func=label,key='filter_categories')
vendors=st.sidebar.multiselect('Vendors',options['vendors']['values'],format_func=label,key='filter_vendors')
spend=st.sidebar.multiselect('Spend classifications',options['spend_classes']['values'],format_func=label,key='filter_spend')
for dimension in ['categories','vendors','spend_classes']:
    if options[dimension]['truncated']:st.sidebar.info(f'Only the first 1000 {dimension} are listed. Use advanced SQL for other values.')
start,end=None,None
if st.sidebar.checkbox('Filter by item start date',key='filter_dates'):
    minimum,maximum,_=options['dates']
    if minimum is not None:
        dates=st.sidebar.date_input('First recorded event (UTC)',value=(minimum,maximum),key='filter_date_range')
        if len(dates)!=2:
            st.info('Choose both dates to apply the date range.');st.stop()
        start,end=dates
        st.sidebar.caption('Items without a recorded timestamp cannot match an active date filter.')
    else:st.sidebar.info('No recorded timestamps are available for date filtering.')
repeat_mode=st.sidebar.selectbox('Repeated activity', ['All items','With repeats','Without repeats'],key='filter_repeats')
span_min,span_max=None,None
if st.sidebar.checkbox('Filter by observed span',key='filter_span'):
    maximum=float(options['dates'][2] or 0)
    if maximum>0:
        span_min,span_max=st.sidebar.slider('Observed span (calendar days)',min_value=0.0,max_value=maximum,value=(0.0,maximum),key='filter_span_range')
        st.sidebar.caption('Items without a usable span cannot match an active span filter.')
    elif options['dates'][2] is not None:
        span_min,span_max=0.0,0.0
        st.sidebar.caption('All usable observed spans are zero days; items with no usable span are excluded.')
    else:st.sidebar.info('No usable observed spans are available for this filter.')
filters=Filters(tuple(categories),tuple(vendors),tuple(spend),start,end,
                None if repeat_mode=='All items' else repeat_mode=='With repeats',span_min,span_max)
dashboard=Dashboard(database,filters)
filters_key=json.dumps(filters.__dict__,default=str,sort_keys=True)
if st.session_state.get('filters_key')!=filters_key:
    st.session_state['filters_key']=filters_key
    st.session_state.pop('sql_evidence',None)
    st.session_state.pop('process',None)
    st.session_state.pop('lookup_id',None)
def filtered(method,*args):return dashboard_result(*signature,filters,method,*args)
overview,overview_evidence=filtered('overview')
st.caption(f"Selected cohort: {overview['item_count']:,} of {report['counts']['cases']:,} recorded items. All KPIs, charts and guided investigations below use this cohort.")
metrics=st.columns(4)
for column,name,key in zip(metrics,['PO items','Purchase documents','Recorded events','Items with repeats'],['item_count','document_count','event_count','repeat_cases']):
    column.metric(name,f"{overview[key]:,}")
metrics=st.columns(3)
for column,name,key in zip(metrics,['Median observed span','P90 observed span','Repeat-item rate'],['median_span_days','p90_span_days','repeat_rate']):
    value=overview[key]
    column.metric(name,'Not available' if value is None else (f'{value:.1%}' if key=='repeat_rate' else f'{value:,.2f} days'))
st.caption(f"Span coverage: {overview['span_coverage']:,}/{overview['item_count']:,} selected items. Observed spans do not establish completion; repetitions may be legitimate receipts or invoices.")
if overview['items_with_missing_times']:
    st.warning(f"{overview['items_with_missing_times']:,} selected items have missing timestamps. Their observed spans can understate elapsed time.")
if overview['missing_activities']:
    st.warning(f"{overview['missing_activities']:,} selected events have no activity name. Repetition and variant results require data-quality review.")
if overview['item_count']==0:st.info('No items match these filters. Reset filters or broaden the selection.')
tabs = st.tabs(['Dataset & quality','Process analysis','Investigation & SQL','Power BI & approvals','Validation & limitations'])
with tabs[0]:
    st.subheader('Selected-cohort evidence')
    st.json(overview_evidence)
    st.download_button('Download selected-cohort KPI evidence',json.dumps(overview_evidence,default=str,indent=2),file_name='cohort-kpis.json')
    st.subheader('Full-source integrity checks')
    st.caption('These counts describe the entire imported source, independently of the active dashboard filters. Published totals are comparison targets, not assumed analysis results.')
    st.json(report)
    st.download_button('Download data-quality report',json.dumps(report,default=str,indent=2),file_name='quality.json')
with tabs[1]:
    st.subheader('Observed process intervals')
    result = filtered('investigate','categories')
    st.dataframe(frame(result),hide_index=True)
    if result['rows']:
        chart=frame(result)
        chart['matching_category']=chart['matching_category'].fillna('(not recorded)')
        st.bar_chart(chart,x='matching_category',y=['median_span_days','p90_span_days'])
    st.caption('Intervals between first and last recorded events; completion status is unknown.')
    if st.button('Discover directly-follows graph with PM4Py',disabled=overview['item_count']==0):
        try:st.session_state['process']=filtered('discover')
        except ValueError as exc:st.error(str(exc))
    if 'process' in st.session_state:
        process=st.session_state['process']
        edges=sorted(process['edges'],key=lambda edge:edge['count'],reverse=True)[:50]
        dot='digraph process { rankdir=LR; '+''.join(f'{json.dumps(e["from"])} -> {json.dumps(e["to"])} [label="{e["count"]}"];' for e in edges)+' }'
        st.graphviz_chart(dot)
        st.caption('Top 50 directly-follows edges in the selected cohort. A descriptive map, not a BPMN compliance model.')
        with st.expander('Process discovery evidence'):st.json(process)
    minimum_occurrences=st.number_input('Minimum transition occurrences',min_value=1,value=1,step=1,key='minimum_occurrences')
    transitions = filtered('transitions',minimum_occurrences)
    st.subheader('Transition waiting intervals')
    st.dataframe(frame(transitions),hide_index=True)
    distribution=filtered('distribution')
    if distribution['rows']:st.bar_chart(frame(distribution),x='waiting_band',y='transitions',sort=False)
    st.caption('Waiting bands use consecutive recorded intervals in the selected items. They do not isolate service time, holidays or causes of delay.')
    st.subheader('Case starts over time')
    trend=frame(filtered('investigate','monthly_cases'))
    if not trend.empty: st.line_chart(trend,x='month',y='cases')
    st.subheader('Top 20 recorded variants')
    st.dataframe(frame(filtered('investigate','top_variants')),hide_index=True)
    st.subheader('Review signals')
    st.dataframe(frame(filtered('investigate','sequence_signals')),hide_index=True)
    st.caption('Invoice-before-receipt signals require flow-specific interpretation. They are not verified violations; some matching flows explicitly permit this order.')
    st.subheader('Item drill-down')
    items=filtered('investigate','long_spans')
    st.dataframe(frame(items),hide_index=True)
    lookup=st.text_input('Look up an item ID',help='The lookup is restricted to the active filters. Leave blank to inspect one of the 30 displayed longest-span items.',key='lookup_id')
    if items['rows']:
        selected_id=st.selectbox('Inspect a displayed item',[row[0] for row in items['rows']])
        case_id=lookup.strip() or selected_id
        events=filtered('case_events',case_id)
        st.dataframe(frame(events),hide_index=True)
        if not events['rows']:st.info('No recorded events for this item in the selected cohort. Broaden the filters or check the ID.')
        if events['truncated']:st.info('Showing the first 1000 events of this item; download/export the full log for remaining events.')
        with st.expander('Item timeline evidence'):st.json(events)
    bundle={'overview':overview_evidence,'categories':result,'transitions':transitions,'waiting_distribution':distribution}
    st.download_button('Download dashboard evidence',json.dumps(bundle,default=str,indent=2),file_name='dashboard-evidence.json')
with tabs[2]:
    selected = st.selectbox('Verified query catalog',list(QUESTIONS),format_func=lambda k:QUESTIONS[k][0])
    result = filtered('investigate',selected)
    st.code(result['sql'],language='sql'); st.json(result)
    sql_scope=st.radio('SQL scope',['Selected cohort','Whole database'],horizontal=True,key='sql_scope')
    sql_examples={'Selected cohort':'SELECT count(*) AS items FROM selected_cases','Whole database':'SELECT count(*) AS items FROM cases'}
    if st.session_state.get('executed_sql_scope')!=sql_scope:
        st.session_state['executed_sql_scope']=sql_scope
        st.session_state.pop('sql_evidence',None)
        if st.session_state.get('sql_editor') in (None,*sql_examples.values()):
            st.session_state['sql_editor']=sql_examples[sql_scope]
    st.caption('For filtered SQL, use selected_cases, selected_events, selected_transitions or other selected_* relations. Whole database mode explicitly bypasses dashboard filters.')
    sql = st.text_area('Custom read-only SQL',key='sql_editor')
    if st.button('Execute SQL'):
        try:
            evidence=dashboard.custom_sql(sql) if sql_scope=='Selected cohort' else analyst.query(sql)
            evidence['editor_scope']=sql_scope
            st.session_state['sql_evidence']=evidence
        except Exception as exc:
            st.session_state.pop('sql_evidence',None)
            st.error(str(exc))
    if 'sql_evidence' in st.session_state:
        st.caption('Last executed query result; editing SQL does not run it until you click Execute SQL.')
        st.dataframe(frame(st.session_state['sql_evidence']),hide_index=True)
        st.json(st.session_state['sql_evidence'])
        st.download_button('Download query evidence',json.dumps(st.session_state['sql_evidence'],default=str,indent=2),file_name='sql-evidence.json')
    st.subheader('Analyst assistant')
    mode=st.radio('Analysis mode',['Guided SQL analyst (no API)','Live LLM with filtered tools'],key='analysis_mode')
    consent = st.checkbox('Allow sending schema, question and query results to the configured LLM provider')
    if not os.environ.get('INSIGHTPILOT_LLM_API_KEY') or not os.environ.get('INSIGHTPILOT_LLM_MODEL'):
        st.info('Guided analysis is available now without credentials. It routes supported questions to real SQL and is not an LLM. Live LLM mode is optional.')
    for investigation in st.session_state['investigations']:
        with st.chat_message('user'): st.write(investigation['question'])
        with st.chat_message('assistant'):
            st.caption(investigation['result'].get('mode','Live LLM investigation')+' · saved result with recorded filters')
            st.write(investigation['result']['answer'])
            with st.expander('Executed SQL, evidence and limitations'): st.json(investigation['result'])
    question = st.chat_input('Ask about recorded procurement patterns')
    if question:
        if mode=='Live LLM with filtered tools' and not consent: st.error('Enable data transmission consent before using the LLM.')
        else:
            with st.spinner('Investigating with controlled SQL tools'):
                try:
                    audit=Path('outputs/audits')/(str(uuid.uuid4())+'.json')
                    if mode=='Guided SQL analyst (no API)':
                        result=dashboard.guided_answer(question)
                        audit.parent.mkdir(parents=True,exist_ok=True)
                        audit.write_text(json.dumps({'question':question,'result':result},default=str,indent=2))
                    else:result = analyst.chat(question,audit_path=audit,dashboard=dashboard)
                    st.session_state['investigations'].append({'question':question,'result':result})
                    st.write(result['answer']); st.json(result)
                except Exception as exc: st.error(str(exc))
with tabs[3]:
    st.info('Generated PBIP/PBIR/TMDL candidate: JSON validated against official Microsoft schemas. Windows Desktop refresh, DAX execution and visual review remain outstanding. See powerbi/README.md.')
    name = st.text_input('Proposed measure name','Items')
    expression = st.text_area('Proposed DAX','COUNTROWS(Cases)')
    proposal = propose_measure(name,expression)
    st.json(proposal)
    approval = st.checkbox('Approve exporting this proposal for manual review')
    if approval: st.download_button('Download approved proposal',json.dumps(proposal|{'status':'approved for manual review'},indent=2),file_name='measure-proposal.json')
    st.caption('Export only. No Power BI files are modified by the agent.')
    buffer=io.BytesIO()
    root=Path(__file__).parent/'powerbi'
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in root.rglob('*'):
            if path.is_file() and (path.suffix in ('.pbip','.pbir','.pbism','.tmdl') or 'InsightPilot.Report' in path.parts):
                archive.write(path,path.relative_to(root))
    st.download_button('Download Power BI project',buffer.getvalue(),file_name='InsightPilot-PowerBI.zip',mime='application/zip')
with tabs[4]:
    st.write(LIMITATIONS)
    st.write('Published count comparison is available under Dataset & quality. Local test evidence is documented in docs/validation.md. Tests on synthetic fixtures do not validate the official dataset.')
