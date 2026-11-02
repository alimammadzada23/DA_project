"""Business presentation over the existing ingestion, analytical and agent services."""
from dataclasses import asdict, dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import uuid
import zipfile

import duckdb
import streamlit as st

from . import charts
from .agent import Analyst, LIMITATIONS, propose_measure
from .analytics import QUESTIONS
from .dashboard import Dashboard, Filters
from .demo import create_demo, create_portfolio_demo
from .ingest import ingest, quality

ROOT=Path(__file__).resolve().parents[2]
PAGES=['Executive Overview','Process Explorer','Root-Cause Investigation','AI Business Analyst']
FILTER_KEYS=['filter_categories','filter_vendors','filter_spend','filter_companies','filter_activities',
             'filter_dates','filter_date_range','filter_repeats','filter_span','filter_span_range','minimum_occurrences']
SHELL='''<style>
[data-testid="stAppViewContainer"] {background:#0B1020;}
[data-testid="stSidebar"] {background:#101A2B;border-right:1px solid #243147;}
.block-container {max-width:1530px;padding-top:2rem;padding-bottom:3rem;}
h1 {font-size:2rem!important;letter-spacing:-.04em;font-weight:700!important;}
h2 {font-size:1.35rem!important;letter-spacing:-.02em;}
h3 {font-size:1.05rem!important;}
[data-testid="stMetric"] {background:#111B2E;border:1px solid #243147;border-radius:14px;padding:17px 19px;min-height:106px;}
[data-testid="stMetricLabel"] {color:#94A5BD;}
[data-testid="stMetricValue"] {font-size:1.75rem;font-weight:650;letter-spacing:-.035em;}
[data-testid="stPlotlyChart"] {border:1px solid #243147;border-radius:14px;padding:8px;background:#111B2E;}
[data-testid="stChatMessage"] {border:1px solid #243147;border-radius:14px;}
.stDeployButton {display:none;}
.brand {display:flex;align-items:center;gap:12px;margin:8px 0 22px;}
.brand-mark {background:#61D5C5;color:#0B1020;font-size:19px;font-weight:800;padding:10px;border-radius:12px;}
.brand-name {font-size:22px;font-weight:700;letter-spacing:-.04em;color:#E3EAF5;}
.brand-sub {font-size:10px;letter-spacing:.12em;color:#94A5BD;}
.eyebrow {color:#61D5C5;font-size:11px;letter-spacing:.14em;font-weight:700;margin:6px 0;}
@media(max-width:900px) {.block-container {padding-left:1rem;padding-right:1rem;} h1 {font-size:1.65rem!important;}}
</style>'''


@st.cache_data(ttl=300)
def load_quality(path,modified,size):
    with duckdb.connect(path,read_only=True) as con:return quality(con)


@st.cache_data(ttl=300,max_entries=192)
def query(path,modified,size,filters,method,*args):
    return getattr(Dashboard(path,filters),method)(*args)


@st.cache_data(ttl=300)
def companion_zip():
    buffer=io.BytesIO();root=ROOT/'powerbi'
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in root.rglob('*'):
            if path.is_file() and (path.suffix in ('.pbip','.pbir','.pbism','.tmdl','.dax','.sql') or 'InsightPilot.Report' in path.parts or path.name=='README.md'):
                archive.write(path,path.relative_to(root))
    return buffer.getvalue()


@dataclass
class Context:
    database: str
    report: dict
    signature: tuple
    filters: Filters
    overview: dict
    overview_evidence: dict

    @property
    def service(self):return Dashboard(self.database,self.filters)

    @property
    def scope_id(self):
        return hashlib.sha256((self.report['sha256']+json.dumps(asdict(self.filters),default=str,sort_keys=True)).encode()).hexdigest()[:14]

    def get(self,method,*args):return query(*self.signature,self.filters,method,*args)


def reset_filters():
    # Assign real widget defaults. Deleting widget keys can let a subsequent browser
    # event restore stale frontend selections after an apparently successful reset.
    for key in ['filter_categories','filter_vendors','filter_spend','filter_companies','filter_activities']:
        st.session_state[key]=[]
    st.session_state['filter_dates']=False
    st.session_state['filter_span']=False
    st.session_state['filter_repeats']='All items'
    for key in ['filter_date_range','filter_span_range','minimum_occurrences']:
        st.session_state.pop(key,None)


def choose_demo(portfolio=True):
    folder=Path('data/portfolio-demo-v2' if portfolio else 'data/demo')
    filename='SYNTHETIC-portfolio-v2.duckdb' if portfolio else 'SYNTHETIC-demo.duckdb'
    target=folder/filename
    if not target.is_file():
        # A partial or pre-existing source is never overwritten.
        if folder.exists() and any(folder.iterdir()):folder=Path('data')/('demo-'+str(uuid.uuid4()))
        with st.spinner('Preparing a reproducible, explicitly synthetic demonstration…'):
            result=create_portfolio_demo(folder) if portfolio else create_demo(folder)
        target=Path(result['database'])
    st.session_state['pending_database']=str(target)
    st.rerun()


def source_controls():
    with st.sidebar.expander('Data & import'):
        st.caption('Import the official event log, or explicitly choose a synthetic demonstration.')
        if st.button('Explore synthetic portfolio demo',width='stretch'):choose_demo(True)
        if st.button('Open labeled synthetic demo',help='The original four-item fixture remains available.'):choose_demo(False)
        with st.form('import_source'):
            uploaded=st.file_uploader('Original XES log',type=['xes','gz'])
            synthetic=st.checkbox('This upload is synthetic test data')
            submit=st.form_submit_button('Import XES')
        if submit:
            if uploaded is None:st.error('Choose an XES or XES.gz file first.')
            else:
                directory=Path('data/imports')/str(uuid.uuid4());directory.mkdir(parents=True)
                source=directory/('source.xes.gz' if uploaded.name.lower().endswith('.gz') else 'source.xes')
                try:
                    with st.spinner('Preserving the source and preparing its analytical tables…'):
                        with source.open('wb') as stream:
                            for chunk in iter(lambda:uploaded.read(1024*1024),b''):stream.write(chunk)
                        target=directory/'analytics.duckdb';ingest(source,target,synthetic=synthetic)
                    st.session_state['pending_database']=str(target);st.rerun()
                except Exception as exc:st.error(f'Import could not finish. The original upload was retained. Details: {exc}')
        st.link_button('Official BPI 2019 download record','https://data.4tu.nl/articles/_/12715853/1')
        st.caption('Large logs can use the streaming CLI. Source integrity and connection details are under Advanced / Developer Tools.')


def sidebar_filters(options):
    st.sidebar.markdown('### Refine the selection')
    st.sidebar.button('Reset filters',on_click=reset_filters,width='stretch')
    categories=st.sidebar.multiselect('Procurement process type',options['categories']['values'],format_func=charts.label,key='filter_categories')
    start,end=None,None
    if st.sidebar.checkbox('Filter by item start date',key='filter_dates'):
        minimum,maximum,_=options['dates']
        if minimum is not None:
            dates=st.sidebar.date_input('First recorded event (UTC)',value=(minimum,maximum),key='filter_date_range')
            if len(dates)!=2:st.info('Choose a start and end date to continue.');st.stop()
            start,end=dates
        else:st.sidebar.info('This source has no usable start dates.')
    repeats=st.sidebar.selectbox('Repeated activity',['All items','With repeats','Without repeats'],key='filter_repeats')
    with st.sidebar.expander('More investigation filters'):
        companies=st.multiselect('Company',options['companies']['values'],format_func=charts.label,key='filter_companies')
        vendors=st.multiselect('Vendors',options['vendors']['values'],format_func=charts.label,key='filter_vendors')
        spend=st.multiselect('Spend classifications',options['spend_classes']['values'],format_func=charts.label,key='filter_spend')
        activities=st.multiselect('Items containing an activity',options['activities']['values'],key='filter_activities')
        st.caption('Activity filters select whole items; their other recorded events remain in the analysis.')
        low,high=None,None
        if st.checkbox('Filter by observed span',key='filter_span'):
            maximum=float(options['dates'][2] or 0)
            if maximum>0:
                low,high=st.slider('Observed span (calendar days)',0.0,maximum,(0.0,maximum),key='filter_span_range')
            elif options['dates'][2] is not None:low,high=0.0,0.0
            else:st.info('No usable observed durations are available.')
        st.caption('Empty selections include all values. Active date/span filters exclude items without usable timestamps.')
        for name in ['categories','vendors','spend_classes','companies','activities']:
            if options[name]['truncated']:st.info(f'The first 1000 {name} are shown; advanced SQL can inspect other values.')
    return Filters(categories=tuple(categories),vendors=tuple(vendors),spend_classes=tuple(spend),start_date=start,end_date=end,
        repeats=None if repeats=='All items' else repeats=='With repeats',min_span=low,max_span=high,companies=tuple(companies),activities=tuple(activities))


def plot(figure,key,select=False):
    if select:figure.update_layout(clickmode='event+select',dragmode='select')
    return st.plotly_chart(figure,width='stretch',key=key,config={'displaylogo':False,'scrollZoom':False},
                           on_select='rerun' if select else 'ignore',selection_mode=('points','box'))


def chart_panel(title,subtitle,result,builder,key):
    st.subheader(title);st.caption(subtitle)
    if result['rows']:plot(builder(result),key)
    else:st.info('No recorded observations match this view. Broaden the active selection.')


def kpis(ctx,compact=False):
    o=ctx.overview
    if not compact:
        columns=st.columns(4)
        columns[0].metric('PO items',f"{o['item_count']:,}",help='Purchase-order line items, not entire purchase documents.')
        columns=columns[1:]
    else:columns=st.columns(3)
    for col,name,key in zip(columns,['Median observed span','P90 observed span','Repeat-item rate'],['median_span_days','p90_span_days','repeat_rate']):
        value=o[key];formatted='Not available' if value is None else (f'{value:.1%}' if key=='repeat_rate' else f'{value:,.2f} days')
        col.metric(name,formatted,help='Calendar interval between first and last recorded events; completion is unknown.' if key!='repeat_rate' else 'Share of selected items with at least one repeated non-null activity name. Not a proven error rate.')
    st.caption(f"{o['document_count']:,} purchase documents · {o['repeat_cases']:,} items with repeats · Duration coverage: {o['span_coverage']:,}/{o['item_count']:,} items. Observed spans are not verified completion times.")


def recommendation_cards(cards):
    if not cards:
        st.info('No above-median group meets the current evidence threshold. Broaden the selection or review the minimum group size; no opportunity is inferred from absent evidence.')
    for i,card in enumerate(cards,1):
        with st.container(border=True):
            st.markdown(f'**{i:02d} · {card["title"]}**')
            st.write(card['observation'])
            st.markdown('**Interpretation**');st.write(card['interpretation'])
            st.markdown('**Proposed action**');st.write(card['action'])
            with st.expander('Impact direction, measurement plan & limitations'):
                st.write(card['expected_direction']);st.write(card['measurement']);st.caption(card['limitation'])


def case_study():
    with st.expander('The case study · from business question to validation'):
        st.write('**Business question** → Where do recorded procurement delays, repetition and potential exceptions occur?')
        st.write('**Dataset** → BPI Challenge 2019 is the intended public anonymized source. Synthetic selections are illustrative fixtures, not observations from that company.')
        st.write('**Analysis** → Item-level cohorts, source quality checks, SQL distributions, process variants and directly-follows mining.')
        st.write('**Findings** → Every displayed observation is calculated from the current selection. Completion, causal explanations and normative compliance require additional verification.')
        st.write('**Recommendations** → Review evidence with procurement/AP owners, confirm eligibility and controls, then design a measured pilot. No savings or approvals are invented.')
        st.write('**Validation** → Independent source reconciliation, analytical reference tests, filter/security tests and browser interaction checks. Original-log acceptance remains incomplete until the genuine log is imported.')


def executive(ctx):
    kpis(ctx)
    monthly=ctx.get('monthly_performance');groups=ctx.get('group_comparison')
    with st.container(border=True):
        st.subheader('Executive summary')
        o=ctx.overview
        if not o['item_count']:st.write('No items match the active filters. Reset or broaden the selection before interpreting performance.')
        else:
            st.write(f"The selection contains **{o['item_count']:,} purchase-order items** across **{o['document_count']:,} purchase documents**, with **{o['event_count']:,} recorded events**.")
            if o['median_span_days'] is not None:st.write(f"The median observed span is **{o['median_span_days']:.2f} days**; the upper 10% begin at **{o['p90_span_days']:.2f} days**. **{o['repeat_cases']:,} items** contain repeated activities that need contextual review.")
            st.caption('These are descriptive observations from this selection, not evidence of inefficiency or proven compliance failures.')
    a,b=st.columns(2)
    with a:chart_panel('Monthly process volume','Items grouped by their first recorded month, in UTC.',monthly,charts.monthly_volume,'exec-volume')
    with b:chart_panel('Duration over time','Median and 90th percentile of observed calendar-day spans.',monthly,charts.duration_trend,'exec-duration-trend')
    a,b=st.columns(2)
    with a:chart_panel('How long are recorded processes?','Items with at least one usable timestamp; no completion assumption.',ctx.get('duration_distribution'),lambda r:charts.band_chart(r,'duration_band','items'),'exec-distribution')
    with b:chart_panel('Procurement flow comparison','Compare recorded spans alongside group volumes and repeat rates.',groups,charts.group_chart,'exec-groups')
    chart_panel('Top sequence signals for review','Invoice-before-receipt order can be permitted by the matching flow. These are not verified violations.',ctx.get('review_breakdown'),charts.review_chart,'exec-reviews')
    st.subheader('Business recommendations')
    st.caption('Ranked by group median span, subject to volume. Hypotheses for investigation, not causal or financial conclusions.')
    recommendation_cards(ctx.get('recommendations','Process type',1))
    case_study()
    with st.container(border=True):
        st.subheader('Power BI companion report')
        st.write('A four-page Power BI project accompanies the analysis. JSON structure and semantic references are checked; Windows Desktop refresh, DAX results and visual acceptance remain unverified.')
        st.download_button('Download Power BI companion',companion_zip(),file_name='InsightPilot-PowerBI.zip',mime='application/zip')


def drilldown(ctx,candidate_ids=None,lookup_key='lookup_id',dimension=None,group_value=None):
    st.subheader('Follow one purchase-order item')
    ids=candidate_ids or [row[0] for row in ctx.get('investigate','long_spans')['rows']]
    context=ctx.scope_id+str(dimension)+str(group_value)
    if st.session_state.get(lookup_key+'_context')!=context:
        st.session_state[lookup_key+'_context']=context;st.session_state.pop(lookup_key,None)
    lookup=st.text_input('Look up an item ID',key=lookup_key,help='Only items inside the active filters and selected investigation group can be inspected.')
    if not ids and not lookup:st.info('No items are available in this selection.');return
    selected_key='selected_'+lookup_key+'_'+hashlib.sha256(context.encode()).hexdigest()[:10]
    selected=st.selectbox('Inspect a displayed item',ids,key=selected_key) if ids else ''
    case_id=lookup.strip() or selected
    summary=ctx.get('item_summary',case_id,dimension,group_value)
    if not summary['rows']:st.info('This item is outside the active selection or its ID is not recorded.');return
    row=summary['rows'][0]
    st.caption(f'Item {row[0]} · document {row[1] or "not recorded"} · {charts.label(row[2])} · {row[5]} events · {row[4]} repeated occurrences')
    events=ctx.get('case_events',case_id)
    if any(row[4] is not None for row in events['rows']):plot(charts.timeline(events),'timeline-'+lookup_key)
    else:st.info('This item has no timestamps for a timeline. Its recorded event order is shown below.')
    human=charts.frame(events).rename(columns={'event_rank':'Order','activity':'Activity','event_time':'Recorded at (UTC)','resource':'Resource','lifecycle':'Lifecycle','source_order':'Original position','case_id':'Item'})
    st.dataframe(human,hide_index=True,width='stretch')
    if events['truncated']:st.caption('First 1000 events shown; use the full export for remaining events.')


def process_explorer(ctx):
    kpis(ctx,True)
    st.subheader('How work moves between activities')
    st.caption('Top recorded transitions, displayed as source and destination layers. Use the discovered graph for complete cyclic paths.')
    transitions=ctx.get('flow_transitions')
    if transitions['rows']:plot(charts.flow(transitions),'process-flow')
    else:st.info('No consecutive timed transitions are available in this selection.')
    if st.button('Discover directly-follows graph with PM4Py',disabled=ctx.overview['item_count']==0):
        try:st.session_state['process']=ctx.get('discover')
        except ValueError as exc:st.error(str(exc))
    if 'process' in st.session_state:
        edges=sorted(st.session_state['process']['edges'],key=lambda row:row['count'],reverse=True)[:50]
        dot='digraph process {rankdir=LR; bgcolor="transparent"; node [style=filled,fillcolor="#182A40",color="#61D5C5",fontcolor="#E3EAF5"]; edge [color="#7BA8FF",fontcolor="#94A5BD"];'+''.join(f'{json.dumps(e["from"])} -> {json.dumps(e["to"])} [label="{e["count"]}"];' for e in edges)+'}'
        st.graphviz_chart(dot)
        st.caption('Up to 50 highest-volume edges shown. The Petri-net discovery and full bounded evidence are under Advanced / Developer Tools.')
    a,b=st.columns(2)
    with a:chart_panel('Most common process variants','Full ordered sequences; a repeated activity is retained in the variant.',ctx.get('investigate','top_variants'),charts.variants,'process-variants')
    with b:chart_panel('Waiting intervals between activities','Calendar intervals, not measured activity service times.',ctx.get('distribution'),lambda r:charts.band_chart(r,'waiting_band','transitions',charts.BLUE),'process-waits')
    a,b=st.columns(2)
    with a:chart_panel('Activity frequency','Recorded event occurrences in the selected items.',ctx.get('investigate','activity_frequency'),charts.activities,'process-activities')
    with b:chart_panel('Activities recorded more than once','Excess occurrences after the first per item; not a proven error count.',ctx.get('repeated_activities'),lambda r:charts.activities(r,True),'process-repeats')
    with st.expander('Read the complete displayed variant sequences'):
        st.dataframe(charts.frame(ctx.get('investigate','top_variants')).rename(columns={'variant':'Recorded sequence','case_count':'Items'}),hide_index=True)
    chart_panel('Matching-flow comparison','The same item-level duration definitions across available procurement flows.',ctx.get('group_comparison'),charts.group_chart,'process-compare')
    st.subheader('Intervals to investigate')
    minimum=st.number_input('Minimum transition occurrences',min_value=1,value=1,step=1,key='minimum_occurrences')
    table=charts.frame(ctx.get('transitions',minimum)).rename(columns={'from_activity':'From activity','to_activity':'To activity','occurrences':'Occurrences','median_wait_hours':'Median interval (hours)','p90_wait_hours':'P90 interval (hours)'})
    st.dataframe(table,hide_index=True,width='stretch')
    drilldown(ctx)


def root_cause(ctx):
    kpis(ctx,True)
    st.info('Investigation lens: differences are associations. The recorded log alone does not establish why they occur.')
    dimension=st.selectbox('Compare groups by',list(Dashboard.GROUPS),key='comparison_dimension')
    groups=ctx.get('group_comparison',dimension)
    if groups['truncated']:st.caption('Showing the 1000 groups with the longest median spans. Narrow the selection to investigate other groups.')
    selection_scope=ctx.scope_id+'|'+dimension
    a,b=st.columns(2)
    with a:
        st.subheader('Where are observed spans longer?');st.caption('Drag a selection box across a bar to inspect its underlying items.')
        selected=None
        if groups['rows']:
            figure=charts.group_chart(groups)
            figure.data[0].customdata=[list(row)+[selection_scope] for row in figure.data[0].customdata]
            figure.update_layout(uirevision=selection_scope,selectionrevision=selection_scope)
            selected=plot(figure,'root-group-selection',True)
    with b:chart_panel('Where are repeats more common?','Share of group items with at least one repeated activity.',groups,lambda r:charts.group_chart(r,'repeat_rate'),'root-repeats')
    values=[row[0] for row in groups['rows']]
    if values:
        a,b=st.columns(2)
        with a:group_a=st.selectbox('Group A',values,format_func=charts.label,key='group_a_'+dimension+'_'+ctx.scope_id)
        with b:group_b=st.selectbox('Group B',values,index=min(1,len(values)-1),format_func=charts.label,key='group_b_'+dimension+'_'+ctx.scope_id)
        if group_a not in values:group_a=values[0]
        if group_b not in values:group_b=values[min(1,len(values)-1)]
        for col,name in [(a,group_a),(b,group_b)]:
            row=next(row for row in groups['rows'] if row[0]==name)
            with col:
                with st.container(border=True):
                    st.markdown('**'+charts.label(name)+'**')
                    st.write(f'{row[1]:,} items · {row[4]:,} with repeats')
                    st.write('Median / P90: '+('not available' if row[2] is None else f'{row[2]:.2f} / {row[3]:.2f} days'))
        points=[point for point in (selected.selection.points if selected else [])
                if len(point.get('customdata',[]))>4 and point['customdata'][4]==selection_scope]
        st.session_state['root_selection']={'scope':ctx.scope_id,'selection':dict(selected.selection) if selected else {}}
        clicked=points[0].get('customdata',[None])[0] if points else group_a
        if clicked not in values:clicked=group_a
        st.subheader('Underlying items · '+charts.label(clicked))
        cases=ctx.get('group_cases',dimension,clicked)
        st.dataframe(charts.frame(cases).rename(columns={'case_id':'Item','matching_category':'Process type','vendor':'Vendor','observed_span_days':'Observed days','repeated_event_count':'Repeated occurrences','event_count':'Events','missing_timestamps':'Missing timestamps'}),hide_index=True)
        st.caption('Up to 100 longest-span items in this group. Bar selections and the group chooser retain every global filter.')
        drilldown(ctx,[row[0] for row in cases['rows']],lookup_key='root_lookup',dimension=dimension,group_value=clicked)
    st.subheader('Prioritized investigation opportunities')
    minimum=st.number_input('Minimum items per recommendation group',min_value=1,value=5,step=1)
    st.caption('Groups above the selected-cohort median, ranked by median span. Ranking is a transparent review heuristic, not estimated causal impact or savings.')
    recommendation_cards(ctx.get('recommendations',dimension,minimum))


SUGGESTIONS=['Summarize the selected process performance for management.','Which procurement flows take the longest?',
             'Where do we observe the largest waiting intervals?','Which activities repeat most frequently?',
             'Compare two selected process types.','What should an operations manager investigate first?']


def render_answer(result,key):
    st.caption(result.get('mode','Live LLM · restricted analytical tools'))
    answer=result.get('answer') or 'No narrative was returned. Review the evidence before drawing a conclusion.'
    st.write(re.sub(r'```.*?```','',answer,flags=re.DOTALL))
    evidence=result.get('evidence',[])
    visual=result.get('visual')
    if evidence and evidence[0]['rows']:
        if visual=='groups':plot(charts.group_chart(evidence[0]),'answer-'+key)
        if visual=='repeats':plot(charts.activities(evidence[0],True),'answer-'+key)
        if visual=='waiting':
            st.caption('Link width shows recorded occurrences; hover to inspect median intervals.')
            plot(charts.flow(evidence[0]),'answer-'+key)
    with st.expander('Evidence & methodology'):
        st.caption('Executed queries, source identity, active filters and interpretation limits. Imported values are untrusted; no causal or compliance certification is implied.')
        st.json(result)


def ai_analyst(ctx):
    st.write('Explore the selected process in business language. Answers stay within the active item filters, with evidence available when you need it.')
    mode=st.radio('Analysis mode',['Guided Analyst','Live LLM'],horizontal=True,key='analysis_mode')
    if mode=='Guided Analyst':st.caption('Guided Analyst uses supported intents and actual SQL. It is explicitly not an LLM and needs no API key.')
    consent=False
    if mode=='Live LLM':
        consent=st.checkbox('Allow sending schema and selected query results to the configured provider')
        if not os.environ.get('INSIGHTPILOT_LLM_API_KEY') or not os.environ.get('INSIGHTPILOT_LLM_MODEL'):
            st.info('A live provider is not configured. Guided Analyst remains available; secure configuration instructions are in Advanced / Developer Tools.')
    suggestion=st.selectbox('Suggested investigation',SUGGESTIONS)
    run_suggestion=st.button('Run suggested investigation',type='primary')
    for index,item in enumerate(st.session_state['investigations']):
        with st.chat_message('user'):st.write(item['question'])
        with st.chat_message('assistant'):render_answer(item['result'],f'history-{index}-{ctx.scope_id}')
    question=st.chat_input('Ask a business question about the selected items')
    if run_suggestion:question=suggestion
    if question:
        if mode=='Live LLM' and not consent:st.error('Allow provider transmission before starting a live investigation.');return
        try:
            with st.spinner('Investigating the selected process…'):
                audit=Path('outputs/audits')/(str(uuid.uuid4())+'.json')
                if mode=='Guided Analyst':
                    result=ctx.service.guided_answer(question)
                    audit.parent.mkdir(parents=True,exist_ok=True)
                    audit.write_text(json.dumps({'question':question,'result':result},default=str,indent=2))
                else:result=Analyst(ctx.database).chat(question,audit_path=audit,dashboard=ctx.service)
            st.session_state['investigations'].append({'question':question,'result':result})
            with st.chat_message('user'):st.write(question)
            with st.chat_message('assistant'):render_answer(result,'new-'+ctx.scope_id)
        except Exception as exc:st.error(f'This investigation could not finish: {exc}')


def advanced(ctx=None):
    with st.expander('Advanced / Developer Tools',expanded=False):
        st.caption('Technical evidence, configuration and report-authoring controls. Main dashboards use the same verified analytical engine.')
        st.text_input('DuckDB database',key='database_path')
        if st.session_state.get('view_error'):
            st.caption('Last analytical diagnostic');st.json(st.session_state['view_error'])
        if ctx is None:
            st.code('uv run insightpilot ingest data/raw/your-original-file.xes data/analytics/bpi2019.duckdb',language='bash')
            st.write('Obtain the original XES/XES.gz from the official record. Read its license, preserve it unchanged and review every count discrepancy before presenting genuine findings.')
            return
        sql_tab,data_tab,pbi_tab,validation_tab=st.tabs(['SQL workbench','Data & provenance','Power BI companion','Validation'])
        with sql_tab:
            question=st.selectbox('Verified query catalog',list(QUESTIONS),format_func=lambda key:QUESTIONS[key][0])
            result=ctx.get('investigate',question);st.code(result['sql'],language='sql');st.json(result)
            scope=st.radio('SQL scope',['Selected cohort','Whole database'],horizontal=True,key='sql_scope')
            examples={'Selected cohort':'SELECT count(*) AS items FROM selected_cases','Whole database':'SELECT count(*) AS items FROM cases'}
            if st.session_state.get('executed_sql_scope')!=scope:
                st.session_state['executed_sql_scope']=scope;st.session_state.pop('sql_evidence',None)
                if st.session_state.get('sql_editor') in (None,*examples.values()):st.session_state['sql_editor']=examples[scope]
            st.caption('Filtered SQL requires selected_* relations. Whole database mode explicitly bypasses filters. Both modes remain read-only with external file/network access disabled.')
            sql=st.text_area('Custom read-only SQL',key='sql_editor')
            if st.button('Execute SQL'):
                try:
                    result=ctx.service.custom_sql(sql) if scope=='Selected cohort' else Analyst(ctx.database).query(sql)
                    result['editor_scope']=scope;st.session_state['sql_evidence']=result
                except Exception as exc:st.session_state.pop('sql_evidence',None);st.error(str(exc))
            if 'sql_evidence' in st.session_state:
                st.dataframe(charts.frame(st.session_state['sql_evidence']),hide_index=True);st.json(st.session_state['sql_evidence'])
                st.download_button('Download query evidence',json.dumps(st.session_state['sql_evidence'],default=str,indent=2),file_name='sql-evidence.json')
        with data_tab:
            st.caption('Full-source integrity checks do not change with dashboard filters. Source hashes identify bytes, not publisher authenticity.')
            st.json(ctx.report);st.json(ctx.overview_evidence)
            if 'root_selection' in st.session_state:st.json(st.session_state['root_selection'])
            st.download_button('Download data-quality report',json.dumps(ctx.report,default=str,indent=2),file_name='quality.json')
            st.download_button('Download selected-cohort KPI evidence',json.dumps(ctx.overview_evidence,default=str,indent=2),file_name='cohort-kpis.json')
            if 'process' in st.session_state:st.json(st.session_state['process'])
            if st.session_state.get('investigation_archive'):
                st.caption('Archived results use their original selections and are not current-cohort findings.');st.json(st.session_state['investigation_archive'])
        with pbi_tab:
            st.write('Preserved PBIP/PBIR/TMDL project. JSON structure and static field bindings are validated; Windows Desktop refresh, DAX execution and visual acceptance remain outstanding.')
            st.download_button('Download Power BI project',companion_zip(),file_name='InsightPilot-PowerBI.zip',mime='application/zip')
            name=st.text_input('Proposed measure name','Items');expression=st.text_area('Proposed DAX','COUNTROWS(Cases)')
            proposal=propose_measure(name,expression);st.json(proposal)
            if st.checkbox('Approve exporting this proposal for manual review'):
                st.download_button('Download approved proposal',json.dumps(proposal|{'status':'approved for manual review'},indent=2),file_name='measure-proposal.json')
            st.caption('Export only: no Power BI project files are modified by the agent.')
        with validation_tab:
            st.write(LIMITATIONS)
            st.write('Fixtures, independent Python/SQL reconciliation, chart/cohort tests and browser checks are documented in docs/validation.md. Genuine-source acceptance is separate.')
            st.code('uv run --frozen --extra test pytest -q',language='bash')
            st.caption('Optional live provider configuration: INSIGHTPILOT_LLM_API_KEY, INSIGHTPILOT_LLM_MODEL and optionally INSIGHTPILOT_LLM_BASE_URL. Never enter keys in chat or commit them.')


def main():
    st.set_page_config(page_title='InsightPilot · Procurement intelligence',page_icon='◈',layout='wide')
    st.markdown(SHELL,unsafe_allow_html=True)
    st.sidebar.markdown('<div class="brand"><div class="brand-mark">IP</div><div><div class="brand-name">InsightPilot</div><div class="brand-sub">PROCESS INTELLIGENCE</div></div></div>',unsafe_allow_html=True)
    page=st.sidebar.radio('Workspace',PAGES,key='page',label_visibility='collapsed')
    if 'database_path' not in st.session_state:st.session_state['database_path']=os.environ.get('INSIGHTPILOT_DATABASE','data/analytics/bpi2019.duckdb')
    if 'pending_database' in st.session_state:st.session_state['database_path']=st.session_state.pop('pending_database')
    database=st.session_state['database_path'];source_controls()
    st.markdown('<div class="eyebrow">PROCUREMENT OPERATIONS / ANALYTICS</div>',unsafe_allow_html=True)
    st.title(page)
    subtitles={PAGES[0]:'How is the recorded procurement process performing?',PAGES[1]:'Explore delays, repeated handling and the paths work actually follows.',
               PAGES[2]:'Compare associated factors, inspect the evidence and prioritize the next investigation.',PAGES[3]:'A business conversation grounded in the selected process data.'}
    st.write(subtitles[page])
    if not Path(database).is_file():
        st.info('No analytical database loaded. Choose the official event log or an explicitly synthetic demonstration to begin.')
        if st.button('Explore the portfolio demo',type='primary'):choose_demo(True)
        st.caption('1,200 reproducible synthetic items for interface exploration. No original-company findings are implied.')
        with st.expander('Manual download and Mac import steps'):
            st.write('Open the official BPI Challenge 2019 DOI, verify its title/license and download the original XES/XES.gz. The verified repository URL is https://data.4tu.nl/articles/_/12715853/1. Use Data & import to load the file, or follow docs/download-and-run.md for streaming CLI import. If 4TU times out, original-data analysis remains incomplete.')
        advanced();return
    try:
        stat=Path(database).stat();signature=(database,stat.st_mtime_ns,stat.st_size)
        report=load_quality(*signature)
        if st.session_state.get('active_database')!=(database,report['sha256']):
            reset_filters();st.session_state['active_database']=(database,report['sha256']);st.session_state['filters_key']=None
        options=query(*signature,Filters(),'options');filters=sidebar_filters(options)
        overview,evidence=query(*signature,filters,'overview')
    except Exception as exc:
        st.session_state['view_error']={'type':type(exc).__name__,'message':str(exc)}
        st.error('The selected dataset could not be opened. Choose an imported InsightPilot database or use Data & import. Technical details are available below.');advanced();return
    ctx=Context(database,report,signature,filters,overview,evidence)
    key=ctx.scope_id
    if st.session_state.get('filters_key')!=key:
        if st.session_state.get('investigations'):
            st.session_state.setdefault('investigation_archive',[]).extend(st.session_state['investigations'])
        st.session_state['investigations']=[];st.session_state['filters_key']=key
        for name in ['sql_evidence','process','lookup_id','root_lookup']:st.session_state.pop(name,None)
    if report['dataset']=='synthetic fixture':
        st.warning('SYNTHETIC DEMONSTRATION · Illustrative patterns only. These are not findings from BPI Challenge 2019.')
    else:st.caption('Imported event log · source provenance, license and count comparisons require review before authenticating BPI findings.')
    st.caption(f"Selected cohort: {overview['item_count']:,} of {report['counts']['cases']:,} recorded items · {overview['event_count']:,} events · UTC start-date selection, complete item histories")
    active=[]
    for name,values in [('Process type',filters.categories),('Vendor',filters.vendors),('Spend',filters.spend_classes),('Company',filters.companies),('Activity',filters.activities)]:
        if values:active.append(name+': '+', '.join(charts.label(v) for v in values))
    if filters.start_date:active.append(f'Starts {filters.start_date} – {filters.end_date}')
    if filters.repeats is not None:active.append('With repeats' if filters.repeats else 'Without repeats')
    if filters.min_span is not None:active.append(f'Observed span {filters.min_span:.1f}–{filters.max_span:.1f} days')
    st.caption('Active filters · '+(' / '.join(active) if active else 'All recorded items'))
    if overview['items_with_missing_times']:st.warning(f"{overview['items_with_missing_times']:,} selected items have missing timestamps; observed spans can understate elapsed time.")
    if overview['missing_activities']:st.warning(f"{overview['missing_activities']:,} selected events have no activity name. Interpret repetition and variants with caution.")
    if not overview['item_count']:st.info('No items match these filters. Reset filters or broaden the selection.')
    st.session_state.pop('view_error',None)
    try:
        {PAGES[0]:executive,PAGES[1]:process_explorer,PAGES[2]:root_cause,PAGES[3]:ai_analyst}[page](ctx)
    except duckdb.Error as exc:
        st.session_state['view_error']={'type':type(exc).__name__,'message':str(exc)}
        st.error('This analytical view could not finish. Narrow the selection and try again; diagnostic details are in Advanced / Developer Tools.')
    advanced(ctx)
