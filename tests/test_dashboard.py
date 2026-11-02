from datetime import date
import json
from pathlib import Path
import subprocess
import sys

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

from insightpilot.agent import Analyst
from insightpilot.dashboard import Dashboard,Filters
from insightpilot.ingest import ingest, prepare_analytics
from insightpilot.reporting import reconcile
from conftest import write_xes


def test_shared_category_cohort_reconciles_all_levels(database):
    dashboard=Dashboard(database,Filters(categories=('3-way synthetic',)))
    overview,evidence=dashboard.overview()
    assert {k:overview[k] for k in ['item_count','event_count','document_count','repeat_cases','median_span_days','repeat_rate']}=={
        'item_count':1,'event_count':4,'document_count':1,'repeat_cases':1,'median_span_days':3,'repeat_rate':1}
    assert dashboard.investigate('event_volume')['rows']==[(4,)]
    assert dashboard.investigate('monthly_cases')['columns']==['month','cases']
    assert dashboard.investigate('monthly_cases')['rows'][0][1]==1
    assert sum(row[1] for row in dashboard.investigate('top_variants')['rows'])==1
    assert sum(row[2] for row in dashboard.transitions()['rows'])==3
    assert sum(row[2] for row in dashboard.distribution()['rows'])==3
    assert evidence['parameters']==['3-way synthetic']
    assert dashboard.case_events('B')['rows']==[]
    process=dashboard.discover()
    assert sum(e['count'] for e in process['edges'])==3


def test_item_start_dates_retain_whole_event_histories(database):
    dashboard=Dashboard(database,Filters(start_date=date(2018,1,1),end_date=date(2018,1,1)))
    overview,_=dashboard.overview()
    assert overview['item_count']==3 and overview['event_count']==9
    events=dashboard.case_events('D')['rows']
    assert events[-1][4].year==2020
    assert Dashboard(database,Filters(start_date=date(2018,1,2),end_date=date(2018,1,2))).overview()[0]['item_count']==1


def test_null_values_and_empty_cohorts(database):
    assert Dashboard(database,Filters(categories=(None,))).overview()[0]['item_count']==1
    assert Dashboard(database,Filters(vendors=(None,))).overview()[0]['item_count']==4
    empty=Dashboard(database,Filters(categories=('not a real category',)))
    result,_=empty.overview()
    assert result['item_count']==0 and result['event_count']==0 and result['repeat_rate'] is None
    assert result['median_span_days'] is None
    with pytest.raises(ValueError,match='No items'):empty.discover()


def test_repeat_and_span_filters(database):
    assert Dashboard(database,Filters(repeats=True)).overview()[0]['item_count']==2
    assert Dashboard(database,Filters(repeats=False)).overview()[0]['item_count']==2
    assert Dashboard(database,Filters(min_span=2,max_span=3)).overview()[0]['item_count']==2
    with pytest.raises(ValueError):Filters(start_date=date(2020,1,1),end_date=date(2018,1,1)).predicate()


def test_parameterization_and_filtered_sql_boundaries(database):
    selected=Dashboard(database,Filters(categories=('2-way synthetic',)))
    assert selected.custom_sql('SELECT count(*) FROM selected_cases;')['rows']==[(1,)]
    assert selected.custom_sql('WITH a AS (SELECT * FROM selected_events) SELECT count(*) FROM a')['rows']==[(2,)]
    for query in ['SELECT * FROM cases','SELECT * FROM events','SELECT * FROM case_metrics','DELETE FROM selected_cases','SELECT 1; SELECT 2',
                  "SELECT * FROM query_table('cases')", "SELECT * FROM query('SELECT * FROM cases')",
                  'WITH cases AS (SELECT * FROM main.cases) SELECT * FROM cases',
                  'WITH cases AS (SELECT * FROM cases) SELECT * FROM cases']:
        with pytest.raises(ValueError):selected.custom_sql(query)
    injection=Dashboard(database,Filters(categories=("x') OR TRUE --",)))
    assert injection.overview()[0]['item_count']==0


def test_empty_dashboard_is_usable_and_date_filter_is_item_based(database,monkeypatch):
    app=app_for(database,monkeypatch)
    app.multiselect(key='filter_categories').set_value(['3-way synthetic']).run(timeout=30)
    app.selectbox(key='filter_repeats').select('Without repeats').run(timeout=30)
    assert app.metric[0].value=='0' and not app.exception
    next(b for b in app.button if b.label=='Reset filters').click().run(timeout=30)
    app.checkbox(key='filter_dates').check().run(timeout=30)
    app.date_input(key='filter_date_range').set_value((date(2018,1,1),date(2018,1,1))).run(timeout=30)
    assert app.metric[0].value=='3' and any('· 9 events ·' in caption.value for caption in app.caption) and not app.exception


def test_guided_questions_have_actual_scoped_evidence(database):
    dashboard=Dashboard(database,Filters(categories=('2-way synthetic',)))
    response=dashboard.guided_answer('How many purchase-order items are recorded?')
    assert response['mode']=='Guided SQL analyst (no LLM)'
    assert response['evidence'][0]['rows']==[(1,)]
    assert '1 purchase-order items' in response['answer']
    response=dashboard.guided_answer('Which flows have the longest spans?')
    assert '2.0 calendar days' in response['answer']
    assert dashboard.guided_answer('Write me a poem')['evidence']==[]
    assert 'does not establish' in dashboard.guided_answer('Why is one category slower?')['answer']


def test_prepared_tables_are_idempotent_and_raw_unchanged(database):
    with duckdb.connect(str(database)) as con:
        assert con.execute("SELECT table_type FROM information_schema.tables WHERE table_name='case_metrics'").fetchone()[0]=='BASE TABLE'
        assert prepare_analytics(con)==[]
        assert con.execute('SELECT count(*) FROM events').fetchone()[0]==10
    assert reconcile(database.parent/'synthetic.xes',database)['passed']


def test_documented_none_resource_is_missing_but_raw_is_preserved(tmp_path):
    source=tmp_path/'none.xes';write_xes(source,[('A','PO',None,[('Create',0,'NONE')])])
    target=tmp_path/'none.duckdb';ingest(source,target,synthetic=True)
    with duckdb.connect(str(target),read_only=True) as con:
        resource,raw=con.execute('SELECT resource,source_attributes FROM events').fetchone()
        assert resource is None and json.loads(raw)['org:resource']=='NONE'
    assert reconcile(source,target)['passed']


def app_for(database,monkeypatch):
    monkeypatch.setenv('INSIGHTPILOT_DATABASE',str(database))
    app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py')).run(timeout=30)
    assert not app.exception
    return app


def test_dashboard_widgets_change_metrics_queries_and_drilldown(database,monkeypatch):
    app=app_for(database,monkeypatch)
    assert app.metric[0].value=='4'
    app.multiselect(key='filter_categories').set_value(['2-way synthetic']).run(timeout=30)
    assert not app.exception
    assert app.metric[0].value=='1' and any('· 2 events ·' in caption.value for caption in app.caption)
    app.text_area[0].set_value('SELECT count(*) AS items FROM selected_cases')
    next(b for b in app.button if b.label=='Execute SQL').click().run(timeout=30)
    assert app.session_state['sql_evidence']['rows']==[(1,)]
    app.multiselect(key='filter_categories').set_value(['3-way synthetic']).run(timeout=30)
    assert 'sql_evidence' not in app.session_state
    assert app.metric[0].value=='1' and any('· 4 events ·' in caption.value for caption in app.caption)
    next(b for b in app.button if b.label=='Reset filters').click().run(timeout=30)
    assert app.metric[0].value=='4' and not app.exception


def test_guided_chat_works_without_credentials(database,monkeypatch):
    monkeypatch.delenv('INSIGHTPILOT_LLM_API_KEY',raising=False)
    app=app_for(database,monkeypatch)
    app.multiselect(key='filter_categories').set_value(['2-way synthetic']).run(timeout=30)
    app.radio(key='page').set_value('AI Business Analyst').run(timeout=30)
    app.chat_input[0].set_value('How many items?').run(timeout=30)
    assert not app.exception
    result=app.session_state['investigations'][-1]['result']
    assert result['evidence'][0]['rows']==[(1,)]
    assert result['mode']=='Guided SQL analyst (no LLM)'


def test_missing_data_is_visible_without_breaking_dashboard(tmp_path,monkeypatch):
    source=tmp_path/'missing.xes';write_xes(source,[('A','PO',None,[('',None,'NONE')])])
    target=tmp_path/'missing.duckdb';ingest(source,target,synthetic=True)
    app=app_for(target,monkeypatch)
    assert app.metric[0].value=='1'
    assert any('missing timestamps' in w.value for w in app.warning)
    assert any('no activity name' in w.value for w in app.warning)
    assert not app.exception


def test_cli_guided_analysis_is_available_without_credentials(database):
    process=subprocess.run([sys.executable,'-m','insightpilot.cli','guide',str(database),'How many items?',
                            '--category','2-way synthetic'],capture_output=True,text=True)
    assert process.returncode==0,process.stderr
    result=json.loads(process.stdout)
    assert result['mode']=='Guided SQL analyst (no LLM)'
    assert result['evidence'][0]['rows']==[[1]]


def test_first_open_demo_loads_from_empty_configuration(tmp_path,monkeypatch,caplog):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('INSIGHTPILOT_DATABASE',str(tmp_path/'missing.duckdb'))
    app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py')).run(timeout=30)
    assert not app.metric
    next(button for button in app.button if button.label=='Open labeled synthetic demo').click().run(timeout=30)
    assert not app.exception
    assert app.metric[0].value=='4'
    assert any('SYNTHETIC' in warning.value for warning in app.warning)
    assert not any('created with a default value' in record.message for record in caplog.records)
