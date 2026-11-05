"""Protocol/session regressions use HTTP mocks; no actual Qwen claim."""
import importlib.util
import json
from pathlib import Path

import duckdb
import httpx
import pytest
from jsonschema import Draft202012Validator

from insightpilot.universal.analyst_plan import PLAN_SCHEMA,PlanError,decode_plan
from insightpilot.universal.analyst_session import Conversation,analyze_question,save_answer,replay_answer
from insightpilot.universal.local_ai import LocalConfig,OllamaClient,LocalAIError
from insightpilot.universal.engine import frame_of

ROOT=Path(__file__).parents[1]
spec=importlib.util.spec_from_file_location('acceptance_reliability',ROOT/'scripts/accept_local_ai.py')
accept=importlib.util.module_from_spec(spec);spec.loader.exec_module(accept)
MODEL='protocol-test-double:latest'


def counted(**extra):
    return {'version':1,'action':'analyze','table':'facts','operation':'aggregate','group_by':['job'],
        'measures':[{'aggregation':'records'}],'sort_by':'records','descending':True,'limit':5,'chart':'bar'}|extra


def client_for(plans):
    chats=[];remaining=iter(plans)
    def handler(request):
        if request.url.path=='/api/tags':return httpx.Response(200,json={'models':[{'name':MODEL}]})
        if request.url.path=='/api/show':return httpx.Response(200,json={'capabilities':['completion']})
        assert request.url.path=='/api/chat' and request.url.host=='127.0.0.1'
        chats.append(json.loads(request.content));value=next(remaining)
        content=value if isinstance(value,str) else json.dumps(value)
        return httpx.Response(200,json={'model':MODEL,'done':True,'message':{'content':content}})
    client=OllamaClient(LocalConfig(model=MODEL),transport=httpx.MockTransport(handler))
    return client,chats


@pytest.fixture
def data(tmp_path):return accept.fixture(tmp_path/'SYNTHETIC')


def ask(data,client,question=accept.ORIGINAL,conversation=None):
    e,p,m,_=data
    return analyze_question(e,p,m,question,conversation or Conversation(),mode='local',client=client,config=client.config)


def test_schema_requires_operation_for_analysis_including_followups():
    for value in [counted(),counted(inherit=True)]:
        value.pop('operation')
        assert not Draft202012Validator(PLAN_SCHEMA).is_valid(value)
        with pytest.raises(PlanError) as exc:decode_plan(json.dumps(value))
        assert exc.value.reason=='missing_operation'


@pytest.mark.parametrize('action',['clarify','refuse'])
def test_clarification_and_refusal_have_no_executable_arguments(action):
    value={'version':1,'action':action,'table':'facts','message':'Specify a supported field or measure.'}
    assert decode_plan(json.dumps(value))==value
    for extra in [{'operation':'aggregate'},{'limit':5},{'group_by':['job']},{'filters':[]}]:
        with pytest.raises(PlanError):decode_plan(json.dumps(value|extra))


def test_missing_operation_is_repaired_once_then_exact_top_five_matches_independent_duckdb(data):
    missing=counted();missing.pop('operation');client,chats=client_for([missing,counted()]);response=ask(data,client)
    assert response['status']=='ok' and response['backend']['repair_attempted'] and len(chats)==2
    assert response['backend']['model_attempts']==2
    assert [r['model'] for r in chats]==[MODEL,MODEL]
    assert len(chats[1]['messages'])==3 and 'Missing required property: operation' in chats[1]['messages'][-1]['content']
    assert chats[1]['messages'][:2]==chats[0]['messages']
    assert json.dumps(missing) not in chats[1]['messages'][-1]['content']
    with duckdb.connect() as con:
        con.register('fixture',data[3]);expected=con.execute('SELECT job,count(*) AS records FROM fixture GROUP BY job ORDER BY records DESC,job LIMIT 5').df()
    actual=frame_of(response['primary'])
    assert list(actual.group_0)==list(expected.job) and list(actual.m0)==list(expected.records)
    assert response['plan']['limit']==5 and response['plan']['descending'] is True
    assert len(response['figures'])==1
    assert accept.check(accept.questions()[2],response,expected)[0]


def test_repair_is_bounded_and_invalid_plan_never_executes(data,monkeypatch):
    missing=counted();missing.pop('operation');client,chats=client_for([missing,missing])
    monkeypatch.setattr('insightpilot.universal.analyst_session.execute_plan',lambda *a,**k:pytest.fail('Invalid plan executed'))
    result=ask(data,client)
    assert result['status']=='error' and result['evidence']==[] and len(chats)==2
    assert 'after one repair attempt' in result['answer']


@pytest.mark.parametrize('bad',['not JSON',counted(group_by=['unauthorized']),counted(operation='execute_sql'),counted(sql='DROP TABLE facts')])
def test_other_invalid_responses_are_not_retried_or_executed(data,bad,monkeypatch):
    client,chats=client_for([bad])
    monkeypatch.setattr('insightpilot.universal.analyst_session.execute_plan',lambda *a,**k:pytest.fail('Invalid plan executed'))
    result=ask(data,client)
    assert result['status']=='error' and not result['evidence'] and len(chats)==1


def test_repaired_plan_still_requires_live_field_validation(data):
    missing=counted();missing.pop('operation');client,chats=client_for([missing,counted(group_by=['other_workspace.field'])])
    result=ask(data,client)
    assert result['status']=='error' and result['evidence']==[] and len(chats)==2


def test_aggregate_never_silently_defaults_to_another_measure(data):
    client,chats=client_for([counted(measures=[]),counted(measures=[])])
    result=ask(data,client)
    assert result['status']=='error' and result['evidence']==[] and len(chats)==2
    assert 'incompatible arguments' in result['answer']


def test_reordering_followup_preserves_previously_displayed_groups(data):
    follow={'version':1,'action':'analyze','table':'facts','operation':'aggregate','inherit':True,'descending':False}
    client,_=client_for([counted(),follow]);conversation=Conversation()
    first=ask(data,client,conversation=conversation)
    result=ask(data,client,'Now sort those results by count from lowest to highest.',conversation)
    actual=frame_of(result['primary']);prior=frame_of(first['primary'])
    assert result['status']=='ok' and set(actual.group_0)==set(prior.group_0)
    assert list(actual.m0)==sorted(prior.m0)
    assert result['applied_cohort']


def test_filtered_records_are_actual_rows_not_substituted_counts(data):
    value=counted(operation='records',group_by=[],measures=[],chart='table',limit=100,filters=[{'column':'age','operator':'gt','value':40}])
    client,_=client_for([value]);result=ask(data,client,'Show records where age is above 40.')
    case=next(c for c in accept.questions() if c['id']=='rows')
    with duckdb.connect() as con:
        con.read_csv(str(data[0].workspace.directory.parent/'SYNTHETIC-acceptance.csv'),header=True,all_varchar=True).create_view('raw_fixture')
        expected=con.execute(case['sql']).df()
    assert accept.check(case,result,expected)[0] and result['figures']==[]


def test_standard_original_question_and_smallest_groups_match_independent_counts(data):
    e,p,m,frame=data
    for question,limit,desc in [(accept.ORIGINAL,5,True),('Show the ten smallest job groups by record count.',10,False)]:
        result=analyze_question(e,p,m,question,Conversation())
        with duckdb.connect() as con:
            con.register('fixture',frame);expected=con.execute('SELECT job,count(*) AS records FROM fixture GROUP BY job ORDER BY records '+('DESC' if desc else 'ASC')+',job LIMIT '+str(limit)).df()
        actual=frame_of(result['primary'])
        assert list(actual.group_0)==list(expected.job) and list(actual.m0)==list(expected.records)
        assert result['plan']['limit']==limit and result['plan']['descending']==desc


def test_standard_is_at_least_filter_uses_the_requested_population(data):
    e,p,m,frame=data
    result=analyze_question(e,p,m,'Count records by job where age is at least 30.',Conversation())
    with duckdb.connect() as con:
        con.register('fixture',frame);expected=con.execute('SELECT job,count(*) AS records FROM fixture WHERE age>=30 GROUP BY job ORDER BY records DESC,job').df()
    actual=frame_of(result['primary'])
    assert list(actual.group_0)==list(expected.job) and list(actual.m0)==list(expected.records)


def test_unparsed_condition_does_not_silently_become_unfiltered_counts(data,monkeypatch):
    e,p,m,_=data
    # The plan dispatcher also formats clarifications; block actual engine
    # queries to prove that the unsupported condition never runs unfiltered.
    monkeypatch.setattr('insightpilot.universal.engine.Engine.run',lambda *a,**k:pytest.fail('Unfiltered query executed'))
    result=analyze_question(e,p,m,'Count records by job where nonexistent_field is at least 30.',Conversation())
    assert result['status']=='clarify' and not result['evidence']


def test_saved_data_roles_names_and_analysis_survive_a_new_workspace_instance(data):
    from insightpilot.universal import Workspace
    from insightpilot.universal.workspace_labels import save_name,metadata
    e,p,m,frame=data;client,_=client_for([counted()]);response=ask(data,client)
    save_name(e.workspace,'Tomorrow review');save_answer(e.workspace,'Keep this',response)
    original=e.workspace.info('facts');fresh=Workspace(e.workspace.directory)
    assert metadata(fresh)['name']=='Tomorrow review' and fresh.info('facts')==original
    replayed=replay_answer(fresh,fresh.saved_objects('block')['Keep this'])
    assert replayed['primary']['rows']==response['primary']['rows'] and len(replayed['figures'])==1


def test_repair_does_not_get_a_second_full_time_budget(data,monkeypatch):
    clock=[0];calls=[]
    class SlowInvalidClient:
        def plan(self,messages):
            calls.append(messages);clock[0]=100
            raise LocalAIError('invalid_output','Missing operation',repair_feedback='Missing required property: operation')
    monkeypatch.setattr('insightpilot.universal.analyst_session.time.perf_counter',lambda:clock[0])
    e,p,m,_=data
    result=analyze_question(e,p,m,accept.ORIGINAL,Conversation(),mode='local',client=SlowInvalidClient())
    assert result['status']=='error' and result['error_code']=='timeout' and len(calls)==1 and not result['evidence']


def test_version_change_before_repair_prevents_retry(data):
    calls=[]
    class ChangingClient:
        def plan(self,messages):
            calls.append(messages);data[0].workspace.transform('facts',{'kind':'trim','column':'job'})
            raise LocalAIError('invalid_output','Missing operation',repair_feedback='Missing required property: operation')
    e,p,m,_=data
    result=analyze_question(e,p,m,accept.ORIGINAL,Conversation(),mode='local',client=ChangingClient())
    assert result['status']=='error' and len(calls)==1 and not result['evidence'] and 'changed' in result['answer']


@pytest.mark.parametrize('diagnostics',[False,True])
@pytest.mark.parametrize('captured_failures',[False,True])
def test_acceptance_runner_exercises_real_pipeline_with_explicit_mock_identity(tmp_path,diagnostics,captured_failures):
    plans=[]
    for case in accept.questions():
        name=case['id']
        if name=='guard_refuse':continue
        if name in ('clarify','refuse'):
            plans.append({'version':1,'action':'clarify' if name=='clarify' else 'refuse','table':'facts','message':'Unavailable field.' if name=='clarify' else 'Read-only analysis; writing files is unsupported.'});continue
        value=counted(limit=15)
        if name=='count':value.update(group_by=[],chart='auto')
        elif name=='groups':pass
        elif name=='top5':value['limit']=5
        elif name in ('top3','chart','resort'):
            value={'version':1,'action':'analyze','table':'facts','operation':'aggregate','inherit':True}|({'limit':3} if name=='top3' else {'chart':'bar'} if name=='chart' else {'descending':False})
        elif name in ('average','median','compound'):
            value['measures']=[{'aggregation':'median' if name=='median' else 'avg','column':'age'}]
            if name=='median':value['group_by']=['education']
            if name=='compound':value['measures'].append({'aggregation':'records'})
        elif name=='filter':value['filters']=[{'column':'age','operator':'gte','value':30}]
        elif name=='rows':value.update(operation='records',group_by=[],measures=[],chart='table',limit=100,filters=[{'column':'age','operator':'gt','value':40}])
        elif name=='missing':value.update(group_by=[],chart='auto',filters=[{'column':'age','operator':'not_missing'}])
        elif name=='smallest':value.update(limit=10,descending=False)
        if captured_failures:
            # Reconstructed from user-reported redacted properties, not a new
            # real Qwen execution. Original questions/oracles remain unchanged.
            if name in ('groups','compound'):value['limit']=10
            elif name=='resort':value=counted(limit=10);value.pop('descending')
            elif name=='median':value['operation']='group_distribution'
            elif name=='smallest':value.pop('limit');value.pop('descending')
            elif name in ('count','rows'):value['limit']=None
        plans.append(value)
    client,chats=client_for(plans)
    code,report,path=accept.run(client.config,client=client,directory=tmp_path/'mock-acceptance',diagnostics=diagnostics)
    assert code==0 and report['passed']==16 and report['failed']==0 and len(chats)==13
    assert all(r['handling']=='application_guard' and r['model_attempts']==0 for r in report['results'] if r['id'] in ('clarify','refuse','guard_refuse'))
    assert report['actual_model_test'] is False and report['actual_model_executed'] is False
    assert all('plan_validated' in r and 'correct_numbers' in r and 'appropriate_chart' in r and 'model_attempts' in r for r in report['results'])
    assert report['diagnostics_enabled']==diagnostics
    assert (path.parent/'rejected-plans.jsonl').exists()==diagnostics
    if captured_failures:
        for case in report['results']:
            if case['id'] in ('groups','compound','resort','median','smallest'):assert case['application_constraint_repairs']
            if case['id'] in ('groups','compound'):
                assert case['validated_plan']['limit'] is None and len(case['actual']['rows'])==12


def test_plotly_typed_numeric_arrays_export_actual_values_after_json_roundtrip():
    import numpy as np
    import plotly.graph_objects as go
    from insightpilot.universal.visuals import plotted_data
    fig=go.Figure(go.Heatmap(x=['a','b'],y=['c','d'],z=np.array([[1.,2.],[3.,4.]])))
    restored=go.Figure(json.loads(fig.to_json()));table=plotted_data(restored)
    assert list(table.value)==[1.,2.,3.,4.]


def test_question_state_suggestions_edits_reset_and_modes_have_no_duplication_warning(ai_workspace,monkeypatch):
    from streamlit.elements.lib import policies
    from test_local_ai_ui import open_analyst,field
    monkeypatch.setattr(policies,'_shown_default_value_warning',False)
    def absent(self):raise LocalAIError('unavailable','Ollama unavailable; Standard analysis remains available.')
    monkeypatch.setattr('insightpilot.universal.analyst_ui.OllamaClient.discover',absent)
    app=open_analyst();suggestion=app.button(key='ai-example-0');text=suggestion.label;suggestion.click().run(timeout=30)
    assert field(app,'text_input','Your analytical question').value==text
    field(app,'text_input','Your analytical question').set_value('How many records?').run(timeout=30)
    app.radio(key='analyst-mode').set_value('Local AI Analyst').run(timeout=30)
    assert field(app,'text_input','Your analytical question').value=='How many records?'
    app.radio(key='analyst-mode').set_value('Standard analysis').run(timeout=30)
    field(app,'button','Start new conversation').click().run(timeout=30)
    assert field(app,'text_input','Your analytical question').value=='' and not app.session_state['analyst-conversation'].entries
    assert not app.exception and not policies._shown_default_value_warning


# Reuse this existing public test fixture without moving or weakening it.
from test_local_ai_ui import ai_workspace
