"""Run from repository root: streamlit run app.py"""
import json
import os
from pathlib import Path
import uuid
import duckdb
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
analyst = Analyst(database)
with duckdb.connect(database,read_only=True) as con:
    report = quality(con)
st.warning('SYNTHETIC FIXTURE — no real-company findings' if report['dataset']=='synthetic fixture' else 'Source provenance and official count comparisons require review')
tabs = st.tabs(['Dataset & quality','Process analysis','Investigation & SQL','Power BI & approvals','Validation & limitations'])
with tabs[0]:
    cols = st.columns(3)
    for col,key in zip(cols,['cases','events','purchase_documents']): col.metric(key.replace('_',' ').title(),report['counts'][key])
    st.json(report)
with tabs[1]:
    st.subheader('Observed process intervals')
    result = analyst.investigate('categories')
    st.dataframe(dict(zip(result['columns'],zip(*result['rows']))) if result['rows'] else [])
    st.caption('Intervals between first and last recorded events; completion status is unknown.')
    if st.button('Discover directly-follows graph with PM4Py'):
        with duckdb.connect(database,read_only=True) as con:
            try: st.json(discover(con))
            except ValueError as exc: st.error(str(exc))
    transitions = analyst.investigate('transitions')
    st.subheader('Transition waiting intervals')
    st.dataframe(dict(zip(transitions['columns'],zip(*transitions['rows']))) if transitions['rows'] else [])
with tabs[2]:
    selected = st.selectbox('Verified query catalog',list(QUESTIONS),format_func=lambda k:QUESTIONS[k][0])
    result = analyst.investigate(selected)
    st.code(result['sql'],language='sql'); st.json(result)
    sql = st.text_area('Custom read-only SQL','SELECT * FROM category_performance')
    if st.button('Execute SQL'):
        try: st.json(analyst.query(sql))
        except Exception as exc: st.error(str(exc))
    st.subheader('Optional LLM investigation')
    consent = st.checkbox('Allow sending schema, question and query results to the configured LLM provider')
    question = st.chat_input('Ask about recorded procurement patterns')
    if question:
        if not consent: st.error('Enable data transmission consent before using the LLM.')
        else:
            with st.spinner('Investigating with controlled SQL tools'):
                try:
                    result = analyst.chat(question,audit_path=Path('outputs/audits')/(str(uuid.uuid4())+'.json'))
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
with tabs[4]:
    st.write(LIMITATIONS)
    st.write('Published count comparison is available under Dataset & quality. Local test evidence is documented in docs/validation.md. Tests on synthetic fixtures do not validate the official dataset.')
