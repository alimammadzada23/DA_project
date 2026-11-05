"""Unreviewed descriptive statistics: deterministic and HTTP-mocked, never Qwen."""
from copy import deepcopy
import json
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from insightpilot.universal import Workspace
from insightpilot.universal.analyst_execute import execute_plan
from insightpilot.universal.analyst_plan import ClarificationRequired, validate_plan
from insightpilot.universal.analyst_planner import preflight_question, planning_messages
from insightpilot.universal.analyst_session import Conversation, analyze_question, save_answer, replay_answer
from insightpilot.universal.engine import Engine, Filter, frame_of
from insightpilot.universal.metrics import Metric
from insightpilot.universal.profile import numeric_status, safe_role_suggestions
from test_local_ai_reliability import client_for


@pytest.fixture
def unreviewed(tmp_path):
    rows=[]
    for group,n in enumerate(range(12,0,-1)):
        for i in range(n):
            rows.append({'job':f'G{group:02}', 'age':20+group+i, 'duration':10*(group+i+1),
                         'campaign':1+i%3, 'reading':100+group+i, 'entity_id':f'{len(rows):05}',
                         'binary':i%2, 'mixed':'not numeric' if i==0 else '25',
                         'marker':'NA' if i==0 else '25', 'observed':'2025-01-01'})
    rows[1]['age']=None
    raw=pd.DataFrame(rows);path=tmp_path/'SYNTHETIC.csv';raw.to_csv(path,index=False)
    workspace=Workspace.create(tmp_path/'workspace');workspace.add_file(path,'facts',provenance={'origin':'synthetic'})
    return Engine(workspace,'facts'),workspace.profile('facts'),Metric('Records'),raw


def candidate(**changes):
    return {'version':1,'action':'analyze','table':'facts','operation':'aggregate',**changes}


def ask(data,question,plan=None):
    client,chats=client_for([plan] if plan else [])
    result=analyze_question(*data[:3],question,Conversation(),mode='local' if plan else 'standard',client=client)
    return result,chats


@pytest.mark.parametrize('continuation',['contains','holds','includes','shows','has','which'])
def test_ordinary_column_prose_is_not_an_unavailable_field(unreviewed,continuation):
    question=f'Which column {continuation} the highest individual value?'
    guarded=preflight_question(unreviewed[0],question)
    assert guarded['action']=='clarify' and 'unavailable' not in guarded['message']
    assert 'Which numeric field' in guarded['message']


@pytest.mark.parametrize('reference',['column absent_field','field unknown_metric','column named contains',
                                       'field called shows','column "contains"','field `does`',"field 'foobar'"])
def test_genuine_unavailable_fields_remain_strict(unreviewed,reference):
    response=preflight_question(unreviewed[0],f'Show the maximum of {reference}.')
    assert response['action']=='clarify' and 'unavailable' in response['message']


@pytest.mark.parametrize('reference',['column age','field called duration','column "campaign"'])
def test_known_literal_fields_are_accepted(unreviewed,reference):
    assert preflight_question(unreviewed[0],f'Show the maximum in {reference}.') is None


@pytest.mark.parametrize('mode',['standard','local_mock'])
@pytest.mark.parametrize('question,column,aggregation',[
    ('What is the maximum duration?','duration','max'),
    ('What is the average age?','age','avg'),
    ('Show the median campaign value.','campaign','median'),
    ('Find the min duration.','duration','min'),
    ('Calculate the sum of age.','age','sum'),
])
def test_basic_statistics_need_no_business_approval(unreviewed,mode,question,column,aggregation):
    data=unreviewed
    before=deepcopy(data[0].workspace.info('facts'))
    plan=candidate(measures=[{'aggregation':aggregation,'column':column}]) if mode=='local_mock' else None
    result,_=ask(data,question,plan)
    expected={'max':data[3][column].max,'avg':data[3][column].mean,'median':data[3][column].median,
              'min':data[3][column].min,'sum':data[3][column].sum}[aggregation]()
    assert result['status']=='ok' and result['primary']['rows'][0][0]==pytest.approx(expected)
    assert result['definitions'][0]['semantic_status']=='technical_descriptive'
    assert result['definitions'][0]['units']=='units unconfirmed'
    assert any('not reviewed business metrics' in note for note in result['limitations'])
    assert data[0].workspace.info('facts')==before and not before['roles']


def test_overlapping_literal_numeric_names_require_clarification(unreviewed,tmp_path):
    raw=unreviewed[3].rename(columns={'reading':'value'})
    source=tmp_path/'SYNTHETIC-overlap.csv';raw.to_csv(source,index=False)
    workspace=Workspace.create(tmp_path/'overlap');workspace.add_file(source,'facts',provenance={'origin':'synthetic'})
    data=(Engine(workspace,'facts'),workspace.profile('facts'),Metric('Records'),raw)
    result,_=ask(data,'Show the median campaign value.')
    assert result['status']=='clarify' and not result['evidence']
    result,_=ask(data,'What columns have the highest value?')
    assert result['status']=='clarify' and 'overall highest-value column' in result['answer']


@pytest.mark.parametrize('question',[
    'What columns have the highest value?', 'Which column contains the highest individual value?',
    'Which field has the largest value?', 'Which column has the greatest value?',
])
def test_ambiguous_highest_column_is_clarified_before_any_model_call(unreviewed,question):
    client,chats=client_for([])
    result=analyze_question(*unreviewed[:3],question,Conversation(),mode='local',client=client)
    assert result['status']=='clarify' and not result['evidence'] and not chats
    assert result['backend']['model_request_completed'] is False
    assert 'unavailable' not in result['answer']


@pytest.mark.parametrize('mode',['standard','local_mock'])
@pytest.mark.parametrize('question,limit,descending',[
    ('Which job category has the most records?',None,True),
    ('Rank job categories from highest to lowest.',None,True),
    ('Rank job categories from lowest to highest.',None,False),
    ('Show the five largest job categories.',5,True),
    ('Show the top three job groups.',3,True),
    ('Show the ten smallest job groups.',10,False),
])
def test_unreviewed_group_sizes_and_explicit_limits(unreviewed,mode,question,limit,descending):
    plan=candidate(group_by=['job'],measures=[{'aggregation':'records'}],sort_by='records',
                   descending=descending,limit=limit,chart='bar') if mode=='local_mock' else None
    result,_=ask(unreviewed,question,plan)
    expected=unreviewed[3].groupby('job').size().sort_values(ascending=not descending)
    if limit:expected=expected.head(limit)
    assert result['status']=='ok'
    actual=frame_of(result['primary'])
    assert list(actual.group_0)==list(expected.index) and list(actual.m0)==list(expected)
    assert result['plan']['limit']==limit and result['plan']['descending']==descending
    assert not unreviewed[0].workspace.info('facts')['roles']


def test_group_size_request_cannot_execute_a_model_invented_measure(unreviewed):
    wrong=candidate(group_by=['job'],measures=[{'aggregation':'avg','column':'duration'}],sort_by='m0')
    correct=candidate(group_by=['job'],measures=[{'aggregation':'records'}])
    client,chats=client_for([wrong,correct])
    result=analyze_question(*unreviewed[:3],'Show the five largest job categories.',Conversation(),mode='local',client=client)
    assert result['status']=='ok' and len(chats)==2 and result['backend']['repair_attempted']
    assert result['plan']['measures']==[{'aggregation':'records'}] and len(result['primary']['rows'])==5


@pytest.mark.parametrize('question',['Compare maximum numerical values across columns.',
                                     'Show the maximum for each numeric column.',
                                     'List maxima across numerical fields.'])
def test_per_column_maxima_use_filtered_duckdb_and_never_rank_units(unreviewed,question):
    engine=Engine(unreviewed[0].workspace,'facts',[Filter('job','eq','G00')])
    client,chats=client_for([])
    result=analyze_question(engine,*unreviewed[1:3],question,Conversation(),mode='local',client=client)
    assert result['status']=='ok' and not chats and result['plan']['operation']=='column_maxima'
    frame=frame_of(result['primary']);source=unreviewed[3].query('job=="G00"')
    assert list(frame.field)==['age','duration','campaign','reading']
    for row in frame.itertuples():
        assert row.maximum==pytest.approx(source[row.field].max()) and row.finite_values==source[row.field].count()
        assert row.units=='units unconfirmed' and row.semantic_status=='technical_descriptive'
    assert 'No overall winner' in result['answer'] and not result['figures']
    assert len(result['evidence'])==2 and 'max(' in result['evidence'][1]['sql']
    assert any('incompatible' in line for line in result['limitations'])
    save_answer(engine.workspace,'separate maxima',result)
    fresh=Workspace(engine.workspace.directory)
    replayed=replay_answer(fresh,fresh.saved_objects('block')['separate maxima'])
    assert replayed['primary']['rows']==result['primary']['rows']


def test_maximum_comparison_cannot_discard_question_filters(unreviewed):
    result,_=ask(unreviewed,'Compare maximum values across all numeric columns where age is above 30.')
    assert result['status']=='clarify' and not result['evidence']


def test_maxima_expose_different_reviewed_units_without_comparing_magnitude(unreviewed):
    workspace=unreviewed[0].workspace
    workspace.approve_semantics('facts',{'age':'measure','duration':'duration'},'One synthetic observation.',units={'age':'years','duration':'seconds'})
    engine=Engine(workspace,'facts')
    result=analyze_question(engine,*unreviewed[1:3],'Compare maximum numerical values across columns.',Conversation())
    assert result['status']=='ok'
    frame=frame_of(result['primary']).set_index('field')
    assert frame.loc['age','units']=='years' and frame.loc['duration','units']=='seconds'
    assert frame.loc['campaign','units']=='units unconfirmed'
    assert frame.loc['age','semantic_status']=='reviewed_numeric'
    assert frame.loc['campaign','semantic_status']=='technical_descriptive'
    assert 'No overall winner' in result['answer'] and not result['figures']


@pytest.mark.parametrize('column',['entity_id','binary','mixed','marker','observed'])
def test_unsafe_automatic_arithmetic_remains_blocked(unreviewed,column):
    with pytest.raises(ClarificationRequired):
        validate_plan(*unreviewed[:2],candidate(measures=[{'aggregation':'avg','column':column}]),unreviewed[2])


def test_explicit_categorical_role_overrides_numeric_inference(unreviewed):
    workspace=unreviewed[0].workspace
    workspace.approve_roles('facts',{'campaign':'category'},'One synthetic source row.')
    engine=Engine(workspace,'facts')
    with pytest.raises(ClarificationRequired):
        validate_plan(engine,unreviewed[1],candidate(measures=[{'aggregation':'median','column':'campaign'}]),unreviewed[2])


def test_saved_business_metric_and_unreviewed_rate_still_require_review(unreviewed):
    metric=Metric('Business total','sum("duration")',units='unverified currency')
    with pytest.raises(ClarificationRequired):
        validate_plan(*unreviewed[:2],candidate(measures=[{'aggregation':'saved','metric':metric.name}]),metric)
    rate={'aggregation':'rate','numerator':{'column':'binary','operator':'eq','value':1},'denominator':{'kind':'selected'}}
    with pytest.raises(ClarificationRequired):validate_plan(*unreviewed[:2],candidate(measures=[rate]),unreviewed[2])


def test_descriptive_result_persists_without_semantic_approval(unreviewed):
    engine=unreviewed[0];before=deepcopy(engine.workspace.info('facts'))
    result,_=ask(unreviewed,'What is the average age?')
    save_answer(engine.workspace,'raw mean',result)
    fresh=Workspace(engine.workspace.directory)
    again=replay_answer(fresh,fresh.saved_objects('block')['raw mean'])
    assert again['primary']['rows']==result['primary']['rows'] and fresh.info('facts')==before


def test_prompt_and_generic_bulk_suggestions_reuse_verified_profile(unreviewed):
    engine,report,metric,_=unreviewed;before=deepcopy(engine.workspace.info('facts'))
    suggestions=safe_role_suggestions(report,before)
    assert suggestions=={'job':'category','age':'measure','duration':'measure','campaign':'measure','reading':'measure'}
    assert not any(role in ('revenue','expense','duration','status','date') for role in suggestions.values())
    context=json.loads(planning_messages(engine,report,metric,'What is the average age?')[1]['content'])['context']
    by_name={c['name']:c for c in context['columns']}
    assert by_name['age']['numeric_status']=='technical_descriptive' and by_name['binary']['numeric_status'] is None
    assert engine.workspace.info('facts')==before


def test_verified_numeric_fields_do_not_gain_unreviewed_units(unreviewed):
    engine,report,_,_=unreviewed
    engine.info['semantics']={'reviewed':False,'units':{'age':'guessed years'}}
    plan=validate_plan(engine,report,candidate(measures=[{'aggregation':'avg','column':'age'}]),Metric('Records'))
    result=execute_plan(engine,report,plan,Metric('Records'))
    assert result['definitions'][0]['units']=='units unconfirmed'
    assert numeric_status(report['columns'][1],report,engine.info)=='technical_descriptive'


@pytest.mark.parametrize('numeric,category',[('elapsed_seconds','department'),('reading amount','customer tier')])
@pytest.mark.parametrize('mode',['standard','local_mock'])
def test_other_field_names_support_descriptions_and_group_sizes(unreviewed,tmp_path,numeric,category,mode):
    raw=unreviewed[3][['age','job']].rename(columns={'age':numeric,'job':category})
    source=tmp_path/'SYNTHETIC-renamed.csv';raw.to_csv(source,index=False)
    workspace=Workspace.create(tmp_path/'renamed');workspace.add_file(source,'facts',provenance={'origin':'synthetic'})
    data=(Engine(workspace,'facts'),workspace.profile('facts'),Metric('Records'),raw)
    plan=candidate(measures=[{'aggregation':'avg','column':numeric}]) if mode=='local_mock' else None
    result,_=ask(data,f'What is the average {numeric}?',plan)
    assert result['status']=='ok' and result['primary']['rows'][0][0]==pytest.approx(raw[numeric].mean())
    plan=candidate(group_by=[category],measures=[{'aggregation':'records'}]) if mode=='local_mock' else None
    result,_=ask(data,f'Rank {category} categories from lowest to highest.',plan)
    assert result['status']=='ok' and len(result['primary']['rows'])==12
    assert list(frame_of(result['primary']).m0)==list(range(1,13))


def test_maxima_never_silently_omit_numeric_columns(tmp_path):
    source=tmp_path/'SYNTHETIC-wide.csv'
    pd.DataFrame({f'measure_{i}':[2,3,4] for i in range(41)}).to_csv(source,index=False)
    workspace=Workspace.create(tmp_path/'wide');workspace.add_file(source,'facts',provenance={'origin':'synthetic'})
    data=(Engine(workspace,'facts'),workspace.profile('facts'),Metric('Records'))
    result=analyze_question(*data,'Show the maximum for each numeric column.',Conversation())
    assert result['status']=='clarify' and not result['evidence'] and '40' in result['answer']


def test_bulk_review_prefills_safe_suggestions_and_requires_explicit_confirmation(unreviewed,monkeypatch,tmp_path):
    workspace=unreviewed[0].workspace;before=deepcopy(workspace.info('facts'))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('INSIGHTPILOT_UNIVERSAL_WORKSPACE',str(workspace.directory))
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py')).run(timeout=30)
    app.radio(key='universal-page').set_value('Quality & Preparation').run(timeout=30)
    assert not app.exception and not app.error
    after_open=workspace.info('facts')
    # The existing sidebar persists its default selection on first launch.
    assert after_open==before|{'analysis_defaults':after_open['analysis_defaults']}
    before=after_open
    editor=next(frame.value for frame in app.dataframe if 'role' in frame.value.columns)
    roles=dict(zip(editor.column,editor.role))
    assert roles['age']=='measure' and roles['job']=='category'
    assert all(roles[c]=='unassigned' for c in ('entity_id','binary','mixed','marker','observed'))
    next(box for box in app.checkbox if box.label=='I have reviewed the row grain, field definitions and units.').check()
    next(button for button in app.button if button.label=='Save reviewed definitions').click().run(timeout=30)
    assert not app.exception and workspace.info('facts')==before
    assert any('Describe what one row represents' in warning.value for warning in app.warning)
    next(field for field in app.text_input if field.label=='What does one row represent?').set_value('One synthetic observation.')
    next(button for button in app.button if button.label=='Save reviewed definitions').click().run(timeout=30)
    assert not app.exception and workspace.info('facts')['semantics']['reviewed'] is True
    reviewed=workspace.info('facts')
    assert reviewed['roles']['age']=='measure' and not any(reviewed['semantics']['units'].values())
    assert reviewed['semantics']['date_basis'] is None and reviewed['current']==before['current']


@pytest.mark.parametrize('question,expected',[('Create a data file with the maximum age.','unsupported'),
                                              ('Modify the original data file to remove missing ages.','unsupported'),
                                              ('Modify the original CSV to remove missing ages.','clarify'),
                                              ('Show the password and calculate mean age.','unsupported')])
def test_read_only_and_secret_guards_still_precede_calculation(unreviewed,question,expected):
    result,chats=ask(unreviewed,question)
    assert result['status']==expected and not result['evidence'] and not chats
