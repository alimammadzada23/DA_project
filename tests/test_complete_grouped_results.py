"""Complete-result regressions use synthetic data and HTTP mocks, not real Qwen."""
import duckdb
import pandas as pd
import pytest

from insightpilot.universal import Workspace
from insightpilot.universal.analyst_plan import MAX_GROUPS, PlanError, validate_plan
from insightpilot.universal.analyst_session import Conversation, analyze_question, save_answer, replay_answer
from insightpilot.universal.context import AnalysisContext
from insightpilot.universal.engine import Engine, frame_of
from insightpilot.universal.metrics import Metric
from test_local_ai_reliability import accept, ask, client_for, counted, data


@pytest.mark.parametrize('case_id', ['groups', 'compound'])
@pytest.mark.parametrize('model_limit', [10, 5, None, 'omitted'])
def test_reported_unrequested_limits_return_all_twelve_groups(data, case_id, model_limit):
    # Only the limit defect was reported; these complete envelopes are explicit
    # protocol test doubles around it, not additional captured Qwen responses.
    case = next(c for c in accept.questions() if c['id'] == case_id)
    candidate = counted(limit=model_limit)
    if model_limit == 'omitted': candidate.pop('limit')
    if case_id == 'compound': candidate['measures'] = [{'aggregation':'avg','column':'age'}, {'aggregation':'records'}]
    client, chats = client_for([candidate]); result = ask(data, client, case['question'])
    with duckdb.connect() as connection:
        connection.register('fixture', data[3]); expected = connection.execute(case['sql']).df()
    assert result['status'] == 'ok' and len(chats) == 1
    assert accept.check(case, result, expected)[0]
    assert result['plan']['limit'] is None and len(result['primary']['rows']) == len(expected) == 12
    assert result['primary']['truncated'] is False and result['primary']['method']['complete_grouped_result']
    if model_limit in (5, 10): assert 'limit' in result['backend']['constraint_repairs']
    workspace = data[0].workspace; info = workspace.info('facts')
    save_answer(workspace, 'complete groups', result)
    fresh = Workspace(workspace.directory); replayed = replay_answer(fresh, fresh.saved_objects('block')['complete groups'])
    assert replayed['plan']['limit'] is None and replayed['primary']['rows'] == result['primary']['rows']
    assert fresh.info('facts') == info


@pytest.mark.parametrize('limit,word,ascending', [(5,'five',False), (3,'3',False), (10,'ten',True)])
def test_explicit_top_n_remains_exact(data, limit, word, ascending):
    question = f'Show the {word} smallest job groups by record count.' if ascending else f'Show the top {word} job groups by record count.'
    client, _ = client_for([counted(limit=10)])
    result = ask(data, client, question)
    with duckdb.connect() as connection:
        connection.register('fixture', data[3]); expected = connection.execute('SELECT job,count(*) AS n FROM fixture GROUP BY job ORDER BY n '+('ASC' if ascending else 'DESC')+',job LIMIT '+str(limit)).df()
    actual = frame_of(result['primary'])
    assert result['status'] == 'ok' and result['plan']['limit'] == limit
    assert list(actual.group_0) == list(expected.job) and list(actual.m0) == list(expected.n)
    assert not result['primary']['method']['complete_grouped_result']


def test_complete_result_can_shrink_then_reorder_without_expanding_membership(data):
    client, chats = client_for([counted(limit=10), counted(limit=10), counted(limit=10), counted(limit=10)])
    conversation = Conversation()
    first = ask(data, client, 'Count records by job.', conversation)
    five = ask(data, client, 'Now show only the top five.', conversation)
    three = ask(data, client, 'Now show only the top three.', conversation)
    sorted_three = ask(data, client, 'Now sort those results by record count from lowest to highest.', conversation)
    assert len(first['primary']['rows']) == 12 and first['plan']['limit'] is None
    assert five['plan']['limit'] == 5 and len(five['primary']['rows']) == 5
    assert three['plan']['limit'] == sorted_three['plan']['limit'] == 3
    assert sorted_three['primary']['rows'] == list(reversed(three['primary']['rows']))
    assert sorted_three['applied_cohort'] and len(chats) == 4


def synthetic_groups(tmp_path, count):
    rows = [{'segment':None if i == count-1 else f'Group {i:03}', 'value':None if i == 0 else i+j/2}
            for i in range(count) for j in range(i%3+1)]
    original = pd.DataFrame(rows); source = tmp_path/'SYNTHETIC.csv'; original.to_csv(source,index=False)
    workspace = Workspace.create(tmp_path/'workspace'); workspace.add_file(source,'facts',provenance={'origin':'synthetic'})
    workspace.approve_semantics('facts',{'segment':'category','value':'measure'},'One synthetic observation.',units={'value':'units'})
    engine = Engine(workspace,'facts',context=AnalysisContext(None,'month','segment'))
    return engine,workspace.profile('facts'),Metric('Records'),original


@pytest.mark.parametrize('count', [17, MAX_GROUPS])
def test_complete_results_exceed_old_default_and_include_missing_category(tmp_path, count):
    engine,profile,metric,original = synthetic_groups(tmp_path,count)
    candidate = counted(group_by=['segment'], measures=[{'aggregation':'avg','column':'value'},{'aggregation':'records'}],limit=10,chart='table')
    client,_ = client_for([candidate])
    result = analyze_question(engine,profile,metric,'Compare average value and record count by segment.',Conversation(),mode='local',client=client)
    with duckdb.connect() as connection:
        connection.register('original',original)
        expected = connection.execute('SELECT segment,avg(value) AS average,count(*) AS n FROM original GROUP BY segment ORDER BY n DESC,segment ASC NULLS LAST').df()
    actual = frame_of(result['primary'])
    assert result['status'] == 'ok' and len(actual) == count and actual.group_0.isna().any()
    assert actual.group_0.fillna('<missing>').tolist() == expected.segment.fillna('<missing>').tolist()
    for a,b in zip(actual.m0,expected.average): assert accept.same(a,b)
    assert list(actual.m1) == list(expected.n) and not result['primary']['truncated']


def test_complete_result_over_resource_bound_fails_without_partial_answer(tmp_path):
    engine,profile,metric,_ = synthetic_groups(tmp_path,MAX_GROUPS+1)
    client,_ = client_for([counted(group_by=['segment'],limit=10,chart='table')])
    result = analyze_question(engine,profile,metric,'Count records by segment.',Conversation(),mode='local',client=client)
    assert result['status'] == 'error' and 'No partial result was returned' in result['answer']
    assert not result['evidence'] and not result['figures'] and 'primary' not in result
    with pytest.raises(PlanError): save_answer(engine.workspace,'must not save a partial result',result)


def test_explicit_limit_at_resource_bound_still_returns_requested_top_n(tmp_path):
    engine,profile,metric,original = synthetic_groups(tmp_path,MAX_GROUPS+1)
    client,_ = client_for([counted(group_by=['segment'],limit=10,chart='table')])
    result = analyze_question(engine,profile,metric,'Show the top one hundred segment groups by record count.',Conversation(),mode='local',client=client)
    assert result['status'] == 'ok' and result['plan']['limit'] == MAX_GROUPS
    with duckdb.connect() as connection:
        connection.register('original',original)
        expected = connection.execute('SELECT segment,count(*) AS n FROM original GROUP BY segment ORDER BY n DESC,segment ASC NULLS LAST LIMIT 100').df()
    actual=frame_of(result['primary'])
    assert actual.group_0.fillna('<missing>').tolist() == expected.segment.fillna('<missing>').tolist()
    assert list(actual.m0) == list(expected.n) and result['primary']['truncated']


@pytest.mark.parametrize('operation', ['records','schema','missing','summary','group_distribution','trend'])
def test_null_limit_cannot_remove_other_operation_bounds(data, operation):
    candidate=counted(operation=operation,group_by=[],measures=[],limit=None,chart='table')
    with pytest.raises(PlanError,match='only for grouped aggregates'):validate_plan(*data[:2],candidate,data[2])


@pytest.mark.parametrize('count,status', [(3,'ok'), (7,'clarify')])
def test_complete_pies_keep_the_six_category_and_full_composition_safeguard(tmp_path,count,status):
    engine,profile,metric,_ = synthetic_groups(tmp_path,count)
    client,_ = client_for([counted(group_by=['segment'],limit=3,chart='pie')])
    result = analyze_question(engine,profile,metric,'Show record counts by segment as a pie chart.',Conversation(),mode='local',client=client)
    assert result['status'] == status
    if status=='ok':assert len(result['primary']['rows']) == count and result['plan']['limit'] is None
    else:assert not result['evidence'] and not result['figures']


def test_old_saved_explicit_limit_replays_without_changing_historical_result(data):
    candidate=counted(limit=5)
    from insightpilot.universal.analyst_execute import execute_plan
    validated=validate_plan(*data[:2],candidate,data[2]); response=execute_plan(*data[:2],validated,data[2])
    save_answer(data[0].workspace,'historical explicit top five',response)
    replayed=replay_answer(data[0].workspace,data[0].workspace.saved_objects('block')['historical explicit top five'])
    assert replayed['plan']['limit'] == 5 and replayed['primary']['rows'] == response['primary']['rows']
