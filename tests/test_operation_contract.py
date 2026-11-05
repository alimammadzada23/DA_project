"""Systematic operation contract checks; all model responses are explicit mocks."""
from copy import deepcopy

import duckdb
import pandas as pd
import pytest

from insightpilot.universal import Workspace
from insightpilot.universal.analyst_contract import OPERATIONS
from insightpilot.universal.analyst_plan import ENVELOPE_VALIDATOR, VALIDATOR, validate_plan
from insightpilot.universal.analyst_session import Conversation, analyze_question, save_answer, replay_answer
from insightpilot.universal.context import AnalysisContext
from insightpilot.universal.engine import Engine
from insightpilot.universal.metrics import Metric
from test_local_ai_reliability import data, ask, client_for, counted, accept


def specimen(operation):
    result={'version':1,'action':'analyze','table':'facts','operation':operation,'limit':None}
    if operation=='aggregate':result.update(group_by=['division'],measures=[{'aggregation':'records'}])
    if operation in ('summary','distribution','group_distribution','outliers'):result['fields']=['reading']
    if operation=='scatter':result['fields']=['reading','elapsed']
    if operation in ('group_distribution','compare_periods'):result['group_by']=['division']
    if operation in ('trend','compare_periods'):result['measures']=[{'aggregation':'records'}]
    if operation=='compare_periods':result['periods']=['2025-01-01','2025-01-24','2025-03-01','2025-03-24']
    return result


@pytest.fixture
def contract_data(tmp_path):
    rows=[{'division':'D'+str(i%6),'reading':None if i==0 else i+(1000 if i==47 else 0),
           'elapsed':i*1.25+1,'recorded':f'2025-{1 if i<24 else 3:02}-{i%24+1:02}','outcome':'yes' if i%2 else 'no'} for i in range(48)]
    original=pd.DataFrame(rows);source=tmp_path/'SYNTHETIC.csv';original.to_csv(source,index=False)
    workspace=Workspace.create(tmp_path/'workspace');workspace.add_file(source,'facts',provenance={'origin':'synthetic'})
    workspace.approve_semantics('facts',{'division':'category','reading':'measure','elapsed':'duration','recorded':'date','outcome':'status'},'One synthetic observation.',units={'reading':'units','elapsed':'seconds'},date_basis='recorded',date_purposes={'recorded':'Observation event.'})
    engine=Engine(workspace,'facts',context=AnalysisContext('recorded','month','division'))
    return engine,workspace.profile('facts'),Metric('Records'),original


VALID=[(name,chart) for name,p in OPERATIONS.items() for chart in p.charts]
INVALID_CHARTS=[(name,chart) for name,p in OPERATIONS.items() for chart in ('bar','pie','line','histogram','box','scatter') if chart not in p.charts]


@pytest.mark.parametrize('operation,chart',VALID)
def test_producer_and_live_validator_agree_for_every_operation_chart(contract_data,operation,chart):
    candidate=specimen(operation)|{'chart':chart}
    assert VALIDATOR.is_valid(candidate)
    validated=validate_plan(*contract_data[:2],candidate,contract_data[2])
    assert VALIDATOR.is_valid(validated.data)
    expected=100 if operation=='records' else 2000 if operation=='scatter' else 1000 if operation=='outliers' else None
    assert validated.data['limit']==expected


@pytest.mark.parametrize('operation,chart',INVALID_CHARTS)
def test_incompatible_operation_charts_are_rejected_by_the_same_schema(operation,chart):
    candidate=specimen(operation)|{'chart':chart}
    assert ENVELOPE_VALIDATOR.is_valid(candidate) and not VALIDATOR.is_valid(candidate)


@pytest.mark.parametrize('operation',list(OPERATIONS))
def test_invalid_argument_combinations_are_operation_specific(operation):
    candidate=specimen(operation);policy=OPERATIONS[operation]
    for key,bounds in [('group_by',policy.groups),('fields',policy.fields),('measures',policy.measures)]:
        wrong=deepcopy(candidate)
        wrong[key]=[] if bounds[0] else ([{'aggregation':'records'}]*(bounds[1]+1) if key=='measures' else ['field_'+str(i) for i in range(bounds[1]+1)])
        assert not VALIDATOR.is_valid(wrong),(operation,key)
    wrong=deepcopy(candidate);wrong['limit']=True
    assert not ENVELOPE_VALIDATOR.is_valid(wrong)
    for limit in (0,-1,OPERATIONS[operation].limit_bound+1):
        wrong=deepcopy(candidate);wrong['limit']=limit
        assert not VALIDATOR.is_valid(wrong)


@pytest.mark.parametrize('operation',list(OPERATIONS))
def test_fresh_requirements_and_followup_inheritance_are_disjoint(operation):
    partial={'version':1,'action':'analyze','table':'facts','operation':operation,'inherit':True}
    assert VALIDATOR.is_valid(partial)
    if OPERATIONS[operation].required:assert not VALIDATOR.is_valid(partial|{'inherit':False})
    assert VALIDATOR.is_valid(specimen(operation))


@pytest.mark.parametrize('case_id',['count','rows'])
@pytest.mark.parametrize('metadata_omitted',[False,True])
def test_exact_reported_null_limit_shapes_and_bounded_metadata_repair(data,case_id,metadata_omitted):
    case=next(c for c in accept.questions() if c['id']==case_id)
    reported={'action':'analyze','operation':'aggregate','group_by':[],'measures':[{'aggregation':'records'}],'limit':None} if case_id=='count' else {'action':'analyze','operation':'records','filters':[{'column':'age','operator':'gt','value':40}],'limit':None}
    complete=reported|{'version':1,'table':'facts'}
    client,chats=client_for([reported,complete] if metadata_omitted else [complete])
    response=ask(data,client,case['question'])
    with duckdb.connect() as connection:
        connection.register('fixture',data[3])
        if case_id=='rows':connection.read_csv(str(data[0].workspace.directory.parent/'SYNTHETIC-acceptance.csv'),header=True,all_varchar=True).create_view('raw_fixture')
        expected=connection.execute(case['sql']).df()
    assert response['status']=='ok' and accept.check(case,response,expected)[0]
    assert response['plan']['limit']==(1 if case_id=='count' else 100)
    assert len(chats)==(2 if metadata_omitted else 1)
    if case_id=='rows':assert response['primary']['method']['result_scope']=='bounded_preview' and 'Bounded' in response['answer']


def test_source_preview_overflow_is_clearly_labeled_and_never_called_complete(tmp_path):
    source=tmp_path/'SYNTHETIC.csv';pd.DataFrame({'x':range(151)}).to_csv(source,index=False)
    workspace=Workspace.create(tmp_path/'workspace');workspace.add_file(source,'facts',provenance={'origin':'synthetic'})
    engine=Engine(workspace,'facts');client,_=client_for([{'version':1,'action':'analyze','table':'facts','operation':'records','limit':None}])
    result=analyze_question(engine,workspace.profile('facts'),Metric('Records'),'Show records.',Conversation(),mode='local',client=client)
    assert result['status']=='ok' and len(result['primary']['rows'])==100 and result['primary']['truncated']
    assert result['primary']['method']['preview_limit']==100 and not result['primary']['method']['complete_grouped_result']
    assert 'Additional eligible rows are not shown' in result['answer']


@pytest.mark.parametrize('repair_succeeds',[True,False])
def test_semantic_operation_error_gets_one_precise_repair_without_early_execution(data,monkeypatch,repair_succeeds):
    wrong={'version':1,'action':'analyze','table':'facts','operation':'records','limit':None,'chart':'bar','filters':[{'column':'age','operator':'gt','value':40}]}
    correct=wrong|{'chart':'table'};client,chats=client_for([wrong,correct if repair_succeeds else wrong]);executions=[]
    import insightpilot.universal.analyst_session as session
    execute=session.execute_plan
    def capture(*args,**kwargs):executions.append(args[2].data);return execute(*args,**kwargs)
    monkeypatch.setattr(session,'execute_plan',capture)
    result=ask(data,client,'Show records where age is above 40.')
    assert len(chats)==2 and 'Allowed charts: auto, table' in chats[1]['messages'][-1]['content']
    assert len(executions)==int(repair_succeeds)
    assert result['status']==('ok' if repair_succeeds else 'error')


@pytest.mark.parametrize('condition',[
    {'column':'reading','operator':'gte','value':20}, {'column':'outcome','operator':'eq','value':'yes'},
    {'column':'reading','operator':'not_missing'}, {'column':'recorded','operator':'from','value':'2025-01-01'},
])
def test_valid_filter_shapes_keep_the_same_contract_and_live_scope(contract_data,condition):
    candidate=specimen('aggregate')|{'filters':[condition]}
    assert VALIDATOR.is_valid(candidate)
    assert validate_plan(*contract_data[:2],candidate,contract_data[2]).data['filters']==[condition]


@pytest.mark.parametrize('condition',[
    {'column':'reading','operator':'gt'}, {'column':'reading','operator':'gt','value':'20'},
    {'column':'reading','operator':'not_missing','value':5}, {'column':'outcome','operator':'eq'},
    {'column':'recorded','operator':'from','value':'yesterday'},
])
def test_invalid_filter_argument_combinations_never_enter_reconciliation(condition):
    assert not ENVELOPE_VALIDATOR.is_valid(specimen('aggregate')|{'filters':[condition]})


def test_invented_filter_cannot_turn_total_count_into_an_unrelated_calculation(data,monkeypatch):
    bad=counted(group_by=[],chart='table',filters=[{'column':'age','operator':'gt','value':40}])
    client,chats=client_for([bad,bad])
    monkeypatch.setattr('insightpilot.universal.analyst_session.execute_plan',lambda *a,**k:pytest.fail('Invented filter ran'))
    result=ask(data,client,'How many records are there?')
    assert result['status']=='error' and not result['evidence'] and len(chats)==2


def test_legacy_inert_trend_defaults_replay_the_same_chronological_calculation(contract_data):
    from insightpilot.universal.analyst_execute import execute_plan
    engine,profile,metric,_=contract_data
    canonical=validate_plan(engine,profile,specimen('trend'),metric)
    response=execute_plan(engine,profile,canonical,metric);save_answer(engine.workspace,'trend',response)
    payload=engine.workspace.saved_objects('block')['trend'];payload['analysis_plan'].update(limit=15,sort_by='records',descending=True)
    replayed=replay_answer(engine.workspace,payload)
    assert replayed['primary']['rows']==response['primary']['rows'] and replayed['plan']['sort_by']=='period'
    assert replayed['plan']['descending'] is False


@pytest.mark.parametrize('operation',list(OPERATIONS))
def test_supported_sort_keys_and_wrong_operation_sort_keys(operation):
    for sort in OPERATIONS[operation].sorts:assert VALIDATOR.is_valid(specimen(operation)|{'sort_by':sort})
    wrong=next((v for v in ('records','period','group','m0') if v not in OPERATIONS[operation].sorts),None)
    if wrong is not None:assert not VALIDATOR.is_valid(specimen(operation)|{'sort_by':wrong})


def test_integral_json_numbers_become_bounded_integer_runtime_arguments(contract_data):
    candidate=specimen('records')|{'limit':5.0,'bins':25.0}
    assert VALIDATOR.is_valid(candidate)
    result=validate_plan(*contract_data[:2],candidate,contract_data[2])
    assert type(result.data['limit']) is int and type(result.data['bins']) is int


def test_explicit_rate_eligibility_is_not_confused_with_numerator_or_denominator():
    from insightpilot.universal.analyst_constraints import requested_filters
    filters,_=requested_filters('Exclude outcome "unknown"; among nonmissing outcome codes, what share is "yes"?',['outcome'])
    assert filters==[{'column':'outcome','operator':'ne','value':'unknown'}]
    filters,_=requested_filters('What share of all selected rows has outcome equal to "yes"?',['outcome'])
    assert filters==[]


def test_period_comparison_preserves_its_existing_budget_above_grouped_top_n_bound(tmp_path):
    from insightpilot.universal.analyst_execute import execute_plan
    rows=[{'division':f'D{i:03}','recorded':day} for i in range(101) for day in ('2025-01-01','2025-03-01')]
    source=tmp_path/'SYNTHETIC-periods.csv';pd.DataFrame(rows).to_csv(source,index=False)
    workspace=Workspace.create(tmp_path/'workspace');workspace.add_file(source,'facts',provenance={'origin':'synthetic'})
    workspace.approve_semantics('facts',{'division':'category','recorded':'date'},'One synthetic event.',date_basis='recorded',date_purposes={'recorded':'Event date.'})
    engine=Engine(workspace,'facts',context=AnalysisContext('recorded','month','division'));metric=Metric('Records')
    value=specimen('compare_periods');validated=validate_plan(engine,workspace.profile('facts'),value,metric)
    result=execute_plan(engine,workspace.profile('facts'),validated,metric)
    assert result['status']=='ok' and len(result['primary']['rows'])==101
    assert result['primary']['method']['complete_grouped_result'] and not result['primary']['truncated']
