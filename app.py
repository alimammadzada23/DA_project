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
from insightpilot.analytics import QUESTIONS, discover
from insightpilot.ingest import quality

st.set_page_config(page_title='InsightPilot',page_icon='🔎',layout='wide')
st.title('InsightPilot · Enterprise Process Intelligence')
st.caption('Portfolio case study · public anonymized procurement data · hypothetical stakeholder scenario')
database = st.sidebar.text_input('DuckDB database',os.environ.get('INSIGHTPILOT_DATABASE','data/analytics/bpi2019.duckdb'))
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
st.warning('SYNTHETIC FIXTURE — no real-company findings' if report['dataset']=='synthetic fixture' else 'Source provenance and official count comparisons require review')
tabs = st.tabs(['Dataset & quality','Process analysis','Investigation & SQL','Power BI & approvals','Validation & limitations'])
with tabs[0]:
    cols = st.columns(3)
    for col,key in zip(cols,['cases','events','purchase_documents']): col.metric(key.replace('_',' ').title(),report['counts'][key])
    st.json(report)
    st.download_button('Download data-quality report',json.dumps(report,default=str,indent=2),file_name='quality.json')
with tabs[1]:
    st.subheader('Observed process intervals')
    result = analyst.investigate('categories')
    st.dataframe(frame(result),hide_index=True)
    if result['rows']:
        st.bar_chart(frame(result),x='matching_category',y=['median_span_days','p90_span_days'])
    st.caption('Intervals between first and last recorded events; completion status is unknown.')
    if st.button('Discover directly-follows graph with PM4Py'):
        with duckdb.connect(database,read_only=True) as con:
            try:
                process=discover(con)
                st.json(process)
                edges=sorted(process['edges'],key=lambda edge:edge['count'],reverse=True)[:50]
                dot='digraph process { rankdir=LR; '+''.join(f'{json.dumps(e["from"])} -> {json.dumps(e["to"])} [label="{e["count"]}"];' for e in edges)+' }'
                st.graphviz_chart(dot)
                st.caption('Top 50 directly-follows edges by occurrence count. A descriptive map, not a BPMN compliance model.')
            except ValueError as exc: st.error(str(exc))
    transitions = analyst.investigate('transitions')
    st.subheader('Transition waiting intervals')
    st.dataframe(frame(transitions),hide_index=True)
    st.subheader('Case starts over time')
    trend=frame(analyst.investigate('monthly_cases'))
    if not trend.empty: st.line_chart(trend,x='month',y='cases')
    st.subheader('Recorded variants')
    st.dataframe(frame(analyst.investigate('top_variants')),hide_index=True)
    st.subheader('Matching-category investigation')
    categories=analyst.query('SELECT DISTINCT matching_category FROM cases ORDER BY matching_category')['rows']
    category=st.selectbox('Matching category',[r[0] for r in categories],format_func=lambda v:v if v is not None else '[missing category]')
    category_result=analyst.query('SELECT case_id,observed_span_days,repeated_event_count,missing_timestamps FROM case_metrics WHERE matching_category IS NOT DISTINCT FROM ? ORDER BY observed_span_days DESC NULLS LAST,case_id LIMIT 100',parameters=[category])
    st.dataframe(frame(category_result),hide_index=True)
    with st.expander('Category SQL evidence'): st.json(category_result)
with tabs[2]:
    selected = st.selectbox('Verified query catalog',list(QUESTIONS),format_func=lambda k:QUESTIONS[k][0])
    result = analyst.investigate(selected)
    st.code(result['sql'],language='sql'); st.json(result)
    sql = st.text_area('Custom read-only SQL','SELECT * FROM category_performance')
    if st.button('Execute SQL'):
        try: st.session_state['sql_evidence']=analyst.query(sql)
        except Exception as exc:
            st.session_state.pop('sql_evidence',None)
            st.error(str(exc))
    if 'sql_evidence' in st.session_state:
        st.dataframe(frame(st.session_state['sql_evidence']),hide_index=True)
        st.json(st.session_state['sql_evidence'])
        st.download_button('Download query evidence',json.dumps(st.session_state['sql_evidence'],default=str,indent=2),file_name='sql-evidence.json')
    st.subheader('Optional LLM investigation')
    consent = st.checkbox('Allow sending schema, question and query results to the configured LLM provider')
    if not os.environ.get('INSIGHTPILOT_LLM_API_KEY') or not os.environ.get('INSIGHTPILOT_LLM_MODEL'):
        st.info('Live AI is not configured. Set INSIGHTPILOT_LLM_API_KEY and INSIGHTPILOT_LLM_MODEL securely. The query catalog and SQL editor work without them.')
    for investigation in st.session_state['investigations']:
        with st.chat_message('user'): st.write(investigation['question'])
        with st.chat_message('assistant'):
            st.write(investigation['result']['answer'])
            with st.expander('Executed SQL, evidence and limitations'): st.json(investigation['result'])
    question = st.chat_input('Ask about recorded procurement patterns')
    if question:
        if not consent: st.error('Enable data transmission consent before using the LLM.')
        else:
            with st.spinner('Investigating with controlled SQL tools'):
                try:
                    result = analyst.chat(question,audit_path=Path('outputs/audits')/(str(uuid.uuid4())+'.json'))
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
