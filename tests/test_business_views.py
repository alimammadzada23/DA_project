from pathlib import Path

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

from insightpilot import charts
from insightpilot.dashboard import Dashboard, Filters
from insightpilot.demo import create_portfolio_demo
from insightpilot.ingest import ingest
from insightpilot.reporting import reconcile
from insightpilot.ui import PAGES,SUGGESTIONS
from conftest import write_xes


def test_new_aggregations_have_independent_reference_answers(database):
    service=Dashboard(database)
    monthly=service.monthly_performance()
    row=monthly['rows'][0]
    assert row[1:]==pytest.approx((4,2.5,700.9,2))
    duration=service.duration_distribution()
    assert {row[0]:row[2] for row in duration['rows']}=={'<1 day':1,'1–<3 days':1,'3–<7 days':1,'30+ days':1}
    repeats=service.repeated_activities()
    assert {row[0]:tuple(row[1:]) for row in repeats['rows']}=={'Change':(1,1,2),'Record Goods Receipt':(1,1,2)}
    assert sum(row[2] for row in duration['rows'])==service.overview()[0]['span_coverage']
    assert service.review_breakdown()['rows']==[('3-way synthetic',1)]
    assert sum(row[1] for row in service.group_comparison()['rows'])==4


def test_chart_data_is_actual_sql_aggregation(database):
    service=Dashboard(database)
    monthly=service.monthly_performance()
    assert list(charts.monthly_volume(monthly).data[0].y)==[4]
    assert list(charts.duration_trend(monthly).data[0].y)==[2.5]
    assert list(charts.duration_trend(monthly).data[1].y)==pytest.approx([700.9])
    assert sum(charts.band_chart(service.duration_distribution(),'duration_band','items').data[0].y)==4
    assert sum(charts.activities(service.investigate('activity_frequency')).data[0].x)==10
    assert sum(charts.activities(service.repeated_activities(),True).data[0].x)==2
    assert list(charts.timeline(service.case_events('D')).data[0].y)==['Create','Change','Change']
    assert sum(charts.flow(service.transitions()).data[0].link.value)==6


def test_process_flow_retains_untimed_edges_without_inventing_intervals(tmp_path):
    source=tmp_path/'untimed.xes';write_xes(source,[('A','PO',None,[('Create',None,None),('Review',None,None)])])
    target=tmp_path/'untimed.duckdb';ingest(source,target,synthetic=True)
    service=Dashboard(target)
    result=service.flow_transitions()
    assert result['rows']==[('Create','Review',1,None)]
    assert service.transitions()['rows']==[]
    figure=charts.flow(result)
    assert sum(figure.data[0].link.value)==1
    assert figure.data[0].link.customdata[0][0]=='Not recorded'


def test_all_chart_views_share_the_activity_and_category_cohort(database):
    service=Dashboard(database,Filters(categories=('3-way synthetic',),activities=('Record Goods Receipt',)))
    assert service.overview()[0]['item_count']==1
    assert sum(row[1] for row in service.monthly_performance()['rows'])==1
    assert sum(row[2] for row in service.duration_distribution()['rows'])==1
    assert service.repeated_activities()['rows']==[('Record Goods Receipt',1,1,2)]
    assert sum(row[1] for row in service.group_comparison()['rows'])==1
    assert sum(charts.activities(service.investigate('activity_frequency')).data[0].x)==4
    assert service.group_cases('Process type','2-way synthetic')['rows']==[]
    assert service.case_events('B')['rows']==[]
    # Activity selection retains the full item: four events, not just the receipts.
    assert service.investigate('event_volume')['rows']==[(4,)]


def test_group_drilldown_and_company_filter_are_parameterized(database):
    service=Dashboard(database)
    assert [row[0] for row in service.group_cases('Process type',None)['rows']]==['D']
    assert service.group_cases('Vendor',"x' OR 1=1 --")['rows']==[]
    with pytest.raises(ValueError):service.group_comparison('source_attributes); DROP TABLE cases; --')
    assert Dashboard(database,Filters(companies=('unrecorded-company',))).overview()[0]['item_count']==0
    assert Dashboard(database,Filters(companies=(None,))).overview()[0]['item_count']==4
    assert service.item_summary('A','Process type','2-way synthetic')['rows']==[]
    assert service.item_summary('B','Process type','2-way synthetic')['rows'][0][0]=='B'


def test_recommendations_have_evidence_and_no_causal_or_savings_claim(database):
    cards=Dashboard(database).recommendations()
    assert cards
    for card in cards:
        assert card['metric']['median_days']>2.5
        assert len(card['evidence'])==2 and all(e['sql'] for e in card['evidence'])
        assert 'not a causal' in card['limitation']
        assert card['measurement'] and card['expected_direction'] and card['action']
    assert Dashboard(database).recommendations(min_items=2)==[]


@pytest.mark.parametrize('question',SUGGESTIONS)
def test_all_suggested_business_questions_execute_filtered_queries(database,question):
    result=Dashboard(database,Filters(categories=('3-way synthetic','2-way synthetic'))).guided_answer(question)
    assert result['evidence']
    assert all(e['filters']['categories']==('3-way synthetic','2-way synthetic') for e in result['evidence'])
    assert all(e['source_sha256'] for e in result['evidence'])
    assert not result['answer'].startswith('Choose a supported investigation')
    assert 'not an LLM' in result['mode'] or 'no LLM' in result['mode']


def test_large_demo_is_reproducible_explicit_and_multi_dimensional(tmp_path):
    a=create_portfolio_demo(tmp_path/'a',cases=80,seed=2019)
    b=create_portfolio_demo(tmp_path/'b',cases=80,seed=2019)
    assert Path(a['source']).read_bytes()==Path(b['source']).read_bytes()
    assert a['scope']=='SYNTHETIC PORTFOLIO DEMO ONLY'
    assert reconcile(a['source'],a['database'])['passed']
    with duckdb.connect(a['database'],read_only=True) as con:
        assert con.execute('SELECT count(*) FROM cases').fetchone()[0]==80
        assert con.execute('SELECT synthetic FROM provenance').fetchone()[0]
        assert con.execute('SELECT count(DISTINCT vendor) FROM cases').fetchone()[0]>4
        assert con.execute("SELECT count(*) FROM (SELECT purchase_document FROM cases GROUP BY purchase_document HAVING count(DISTINCT vendor)>1 OR count(DISTINCT json_extract_string(source_attributes,'$.Company'))>1)").fetchone()[0]==0
    options=Dashboard(a['database']).options()
    assert len(options['companies']['values'])==4
    with pytest.raises(ValueError):create_portfolio_demo(tmp_path/'a')


@pytest.mark.parametrize('page',PAGES)
def test_all_four_pages_load_and_recalculate_the_same_selection(database,monkeypatch,page):
    monkeypatch.setenv('INSIGHTPILOT_DATABASE',str(database))
    app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py')).run(timeout=40)
    app.radio(key='page').set_value(page).run(timeout=40)
    assert not app.exception
    app.multiselect(key='filter_categories').set_value(['2-way synthetic']).run(timeout=40)
    assert not app.exception
    assert not app.error
    assert any('Selected cohort: 1 of 4' in text.value and '2 events' in text.value for text in app.caption)
    if page!='AI Business Analyst':
        metric=next(m for m in app.metric if m.label=='Median observed span')
        assert metric.value=='2.00 days'
    assert any(expander.label=='Advanced / Developer Tools' for expander in app.expander)


def test_filter_change_clears_current_ai_answers_and_retains_audit_history(database,monkeypatch):
    monkeypatch.setenv('INSIGHTPILOT_DATABASE',str(database))
    app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py')).run(timeout=40)
    app.radio(key='page').set_value('AI Business Analyst').run(timeout=40)
    app.chat_input[0].set_value('Summarize the selected process performance for management.').run(timeout=40)
    assert len(app.session_state['investigations'])==1
    app.multiselect(key='filter_categories').set_value(['2-way synthetic']).run(timeout=40)
    assert not app.exception
    assert app.session_state['investigations']==[]
    assert len(app.session_state['investigation_archive'])==1


@pytest.mark.parametrize('page',PAGES)
def test_empty_selection_remains_usable_on_every_page(database,monkeypatch,page):
    monkeypatch.setenv('INSIGHTPILOT_DATABASE',str(database))
    app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py')).run(timeout=40)
    app.radio(key='page').set_value(page).run(timeout=40)
    app.multiselect(key='filter_categories').set_value(['3-way synthetic']).run(timeout=40)
    app.selectbox(key='filter_repeats').select('Without repeats').run(timeout=40)
    assert not app.exception
    assert any('No items match' in info.value for info in app.info)


def test_query_resource_error_has_a_business_empty_state(database,monkeypatch):
    monkeypatch.setenv('INSIGHTPILOT_DATABASE',str(database))
    def unavailable(self):raise duckdb.OutOfMemoryException('analytical resource limit')
    monkeypatch.setattr(Dashboard,'monthly_performance',unavailable)
    app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py')).run(timeout=40)
    assert not app.exception
    assert any('analytical view could not finish' in error.value for error in app.error)
    assert app.session_state['view_error']['type']=='OutOfMemoryException'
