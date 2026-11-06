"""Grounded local interpretation: the model words computed values; checks remove anything unverifiable."""
import json

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from insightpilot.universal import Workspace
from insightpilot.universal.analyst_interpret import (INTERPRETABLE, InterpretationError, eligible, interpretation_context,
                                                       unsupported_numbers, verify)
from insightpilot.universal.analyst_session import Conversation, analyze_question
from insightpilot.universal.engine import Engine
from insightpilot.universal.local_ai import LocalAIError, LocalConfig, OllamaClient
from insightpilot.universal.metrics import Metric
from insightpilot.universal.analyst_execute import profile_for_engine

MODEL='test-double:latest'
GROUP_PLAN={'version':1,'action':'analyze','table':'facts','operation':'aggregate','group_by':['region'],
            'measures':[{'aggregation':'sum','column':'amount'}],'sort_by':'m0'}


@pytest.fixture
def engine(tmp_path):
    w=Workspace.create(tmp_path/'ws');p=tmp_path/'facts.csv'
    p.write_text('region,amount\nNorth,600\nNorth,400\nSouth,500\nEast,250\nWest,250\n')
    w.add_file(p,'facts')
    w.approve_semantics('facts',{'region':'geography','amount':'revenue'},'One order',units={'amount':'EUR'})
    return Engine(w,'facts')


class FakeModel:
    """Explicit test double: returns a fixed plan, then a fixed interpretation; records what it was sent."""
    def __init__(self,interpretation,*,fail=None):
        self.interpretation=interpretation;self.fail=fail;self.sent=[]
        self.config=LocalConfig(model=MODEL)
    def plan(self,messages):return GROUP_PLAN,{'backend':'test double','model':MODEL}
    def interpret(self,messages):
        self.sent.append(messages)
        if self.fail:raise self.fail
        return self.interpretation,{'model':MODEL}


def run(engine,model,question='Which region has the highest total amount?'):
    return analyze_question(engine,profile_for_engine(engine),Metric('Records'),question,Conversation(),mode='local',
                            client=model,config=model.config,interpret_results=True)


def good():
    return {'headline':'North has the highest total amount at 1,000 EUR, 50% of the 2,000 EUR selected total.',
            'findings':['North is twice South (1,000 vs 500 EUR).','East and West tie at 250 EUR each.',
                        'North exceeds South by 500 EUR, a 100% difference.'],
            'caveats':['Totals describe the selected rows only.'],
            'follow_ups':['How does amount vary by region over time?','Show the distribution of amount.']}


def test_verified_interpretation_cites_only_computed_or_derived_numbers(engine):
    model=FakeModel(good());response=run(engine,model)
    assert response['status']=='ok'
    item=response['interpretation']
    assert item['status']=='ok' and item['removed']==[] and len(item['findings'])==3
    assert item['numbers_checked']>=8 and item['follow_ups'][0].startswith('How does')
    # The deterministic, engine-computed answer and table are unchanged by interpretation.
    assert response['primary']['rows'][0][1]==1000 and response['answer']


def test_invented_numbers_and_causal_claims_are_removed(engine):
    raw=good()|{'findings':['North grew 37% year over year.','North leads because of stronger marketing.',
                            'South totals 500 EUR.','West reached 9,999 EUR.']}
    item=run(engine,FakeModel(raw))['interpretation']
    assert item['findings']==['South totals 500 EUR.']
    reasons=sorted(r['reason'] for r in item['removed'])
    assert reasons==['causal wording','number not in computed result','number not in computed result']


def test_unverifiable_interpretation_falls_back_without_touching_the_answer(engine):
    raw={'headline':'Revenue will reach 5 million next year.','findings':['Growth is driven by North.'],'caveats':[],'follow_ups':[]}
    response=run(engine,FakeModel(raw))
    assert response['status']=='ok' and response['interpretation']['status']=='unavailable'
    assert 'calculated answer above is unaffected' in response['interpretation']['message']


def test_model_failure_never_blocks_the_calculation(engine):
    response=run(engine,FakeModel(None,fail=LocalAIError('timeout','Ollama timed out.')))
    assert response['status']=='ok' and response['interpretation']['status']=='unavailable' and response['primary']['rows']


def test_missing_groups_reach_the_model_as_missing_not_none(tmp_path):
    w=Workspace.create(tmp_path/'ws');p=tmp_path/'facts.csv';p.write_text('region,amount\nNorth,10\n,4\n')
    w.add_file(p,'facts');w.approve_semantics('facts',{'region':'geography','amount':'revenue'},'One order',units={'amount':'EUR'})
    model=FakeModel(good());run(Engine(w,'facts'),model)
    rows=json.loads(model.sent[0][1]['content'])['table']['rows']
    assert ['Missing',4] in [r[:2] for r in rows] and not any(r[0] is None for r in rows)


def test_only_bounded_computed_aggregates_are_sent(engine):
    model=FakeModel(good());run(engine,model)
    payload=json.loads(model.sent[0][1]['content'])
    assert payload['table']['columns'][0]=='region' and 'EUR' in payload['table']['columns'][1]
    assert payload['table']['rows'][0][:2]==['North',1000]
    assert set(payload)>={'question','analysis','measures','table','limitations'}
    assert 'sql' not in json.dumps(payload).casefold()


@pytest.mark.parametrize('operation',['records','scatter','outliers'])
def test_row_level_previews_are_never_interpreted(operation):
    response={'status':'ok','plan':{'operation':operation},'primary':{'columns':['x'],'rows':[[1]]}}
    assert operation not in INTERPRETABLE and not eligible(response)
    with pytest.raises(InterpretationError):interpretation_context('q',response)


def test_number_checks_accept_rounding_shares_and_suffixes():
    context={'table':{'columns':['g','v'],'rows':[['A',2148.4],['B',1061.2]]},'selected_source_rows':900,'rows_shown':2,'rows_total':2}
    assert unsupported_numbers('A reached 2,148 and 2.1k; B 1,061.2; total 3,209.6.',context)==[]
    assert unsupported_numbers('A is 66.9% of the total and 102.4% above B; 2 groups; in 2024.',context)==['2024']
    assert unsupported_numbers('A reached 2,400.',context)==['2,400']


def test_schema_violations_are_rejected():
    with pytest.raises(InterpretationError):verify({'headline':'x'},{'table':{'columns':[],'rows':[]}})


def test_client_interpret_uses_loopback_structured_output_and_rejects_bad_json():
    calls=[]
    def handler(request):
        calls.append(request);assert request.url.host=='127.0.0.1'
        if request.url.path=='/api/tags':return httpx.Response(200,json={'models':[{'name':MODEL}]})
        if request.url.path=='/api/show':return httpx.Response(200,json={'capabilities':['completion']})
        body=json.loads(request.content)
        assert body['format']['required']==['headline','findings','caveats','follow_ups'] and body['stream'] is False
        assert body['options']['temperature']==0 and 'tools' not in body
        content=json.dumps(good()) if len(calls)<4 else 'not json'
        return httpx.Response(200,json={'model':MODEL,'done':True,'message':{'role':'assistant','content':content}})
    client=OllamaClient(LocalConfig(model=MODEL),transport=httpx.MockTransport(handler))
    value,metadata=client.interpret([{'role':'system','content':'s'},{'role':'user','content':'{}'}])
    assert value['headline'].startswith('North') and metadata['model']==MODEL
    with pytest.raises(LocalAIError):client.interpret([{'role':'system','content':'s'},{'role':'user','content':'{}'}])


def test_ui_shows_checked_interpretation_and_one_click_follow_up(tmp_path,monkeypatch):
    from insightpilot.universal.projects import ProjectSession
    monkeypatch.chdir(tmp_path)
    w=Workspace.create(tmp_path/'data/universal/source');p=tmp_path/'SYNTHETIC.csv'
    p.write_text('region,amount\nNorth,600\nNorth,400\nSouth,500\nEast,250\nWest,250\n')
    w.add_file(p,'facts');w.approve_semantics('facts',{'region':'geography','amount':'revenue'},'One order',units={'amount':'EUR'})
    root=tmp_path/'catalog';session=ProjectSession.start(root,w.directory)
    monkeypatch.setenv('INSIGHTPILOT_WORKSPACE_ROOT',str(root));monkeypatch.setenv('INSIGHTPILOT_UNIVERSAL_WORKSPACE',str(session.directory))
    questions=[]
    monkeypatch.setattr('insightpilot.universal.analyst_ui.OllamaClient.discover',lambda self:{'status':'available','models':[{'name':MODEL}],'message':'test double'})
    monkeypatch.setattr('insightpilot.universal.analyst_ui.OllamaClient.inspect_model',lambda self:{'status':'available','name':MODEL})
    def plan(self,messages):
        questions.append(json.loads(messages[-1]['content'])['question']);return GROUP_PLAN,{'backend':'test double','model':MODEL}
    monkeypatch.setattr('insightpilot.universal.analyst_ui.OllamaClient.plan',plan)
    monkeypatch.setattr('insightpilot.universal.analyst_ui.OllamaClient.interpret',lambda self,messages:(good(),{'model':MODEL}))
    app=AppTest.from_file(str(__import__('pathlib').Path(__file__).parents[1]/'app.py')).run(timeout=30)
    app.radio(key='universal-page').set_value('AI Analyst').run(timeout=30)
    app.radio(key='analyst-mode').set_value('Local AI Analyst').run(timeout=30)
    next(t for t in app.text_input if t.label=='Your analytical question').set_value('Which region has the highest total amount?')
    next(b for b in app.button if b.label=='Calculate evidence-backed answer').click().run(timeout=30)
    assert not app.exception
    assert any('AI interpretation' in m.value and 'North has the highest' in m.value for m in app.markdown)
    assert any('number(s) checked against the computed result' in c.value for c in app.caption)
    next(b for b in app.button if b.label=='Show the distribution of amount.' and str(b.key).startswith('ai-follow-')).click().run(timeout=30)
    assert not app.exception and questions[-1]=='Show the distribution of amount.'
