"""UI integration with an explicitly named model test double, never actual Qwen."""
from pathlib import Path
import json

import pytest
from streamlit.testing.v1 import AppTest

from insightpilot.universal import Workspace
from insightpilot.universal.local_ai import LocalAIError
from insightpilot.universal.workspace_labels import save_name

APP=str(Path(__file__).parents[1]/'app.py')


@pytest.fixture
def ai_workspace(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    w=Workspace.create(tmp_path/'data/universal/opaque-id')
    p=tmp_path/'SYNTHETIC.csv';p.write_text('group,value,outcome\nA,10,yes\nA,20,no\nA,30,yes\nB,40,yes\nB,50,no\nC,90,no\n')
    w.add_file(p,'facts',provenance={'origin':'synthetic'})
    w.approve_semantics('facts',{'group':'category','value':'measure','outcome':'status'},'One synthetic observation',units={'value':'units'})
    save_name(w,'Research workspace')
    monkeypatch.setenv('INSIGHTPILOT_UNIVERSAL_WORKSPACE',str(w.directory))
    return w


def open_analyst():
    a=AppTest.from_file(APP).run(timeout=30);a.radio(key='universal-page').set_value('AI Analyst').run(timeout=30);return a


def field(app,kind,label):return next(w for w in getattr(app,kind) if w.label==label)


def mock_model(monkeypatch,calls):
    monkeypatch.setattr('insightpilot.universal.analyst_ui.OllamaClient.discover',lambda self:{'status':'available','models':[{'name':'test-double:latest'}],'message':'Explicit protocol test double'})
    monkeypatch.setattr('insightpilot.universal.analyst_ui.OllamaClient.inspect_model',lambda self:{'status':'available','name':'test-double:latest'})
    def propose(self,messages):
        q=json.loads(messages[-1]['content'])['question'];calls.append(q)
        root={'version':1,'action':'analyze','table':'facts'}
        if q.startswith('Now'):p=root|{'inherit':True,'limit':2}
        elif 'average' in q:p=root|{'inherit':True,'measures':[{'aggregation':'avg','column':'value'}]}
        else:p=root|{'operation':'aggregate','group_by':['group'],'measures':[{'aggregation':'records'}],'sort_by':'records'}
        return p,{'backend':'MOCK integration only','model':'test-double:latest'}
    monkeypatch.setattr('insightpilot.universal.analyst_ui.OllamaClient.plan',propose)


def ask(app,question):
    field(app,'text_input','Your analytical question').set_value(question)
    field(app,'button','Calculate evidence-backed answer').click().run(timeout=30)
    assert not app.exception and not app.error


def test_standard_compound_request_results_tables_figures_and_history(ai_workspace):
    a=open_analyst();ask(a,'Which group has the most records, and what is their average value?')
    assert any('3 records' in m.value and '20 units' in m.value for m in a.markdown)
    assert len(a.get('plotly_chart'))==2
    assert len(a.session_state['analyst-conversation'].entries)==1
    ask(a,'Now show only the top two.')
    assert len(a.session_state['analyst-conversation'].entries)==2
    follow=a.session_state['analyst-conversation'].last_response
    assert follow['plan']['limit']==2 and len(follow['primary']['rows'])==2


def test_unavailable_ollama_never_breaks_standard_or_sql(ai_workspace,monkeypatch):
    def absent(self):raise LocalAIError('unavailable','Ollama is not reachable. Start the app or choose Standard analysis.')
    monkeypatch.setattr('insightpilot.universal.analyst_ui.OllamaClient.discover',absent)
    a=open_analyst();a.radio(key='analyst-mode').set_value('Local AI Analyst').run(timeout=30)
    assert any('not reachable' in w.value for w in a.warning)
    assert field(a,'button','Calculate evidence-backed answer').disabled
    a.radio(key='analyst-mode').set_value('Standard analysis').run(timeout=30)
    ask(a,'How many records?')
    assert any('6 selected rows' in m.value for m in a.markdown)
    a.radio(key='universal-page').set_value('SQL Lab').run(timeout=30)
    assert not a.exception and field(a,'button','Run query')


def test_mock_local_followup_and_no_duplicate_inference(ai_workspace,monkeypatch):
    calls=[];mock_model(monkeypatch,calls)
    a=open_analyst();a.radio(key='analyst-mode').set_value('Local AI Analyst').run(timeout=30)
    assert any('test-double:latest' in s.value for s in a.success)
    ask(a,'Show record counts by group.')
    assert calls==['Show record counts by group.']
    a.run(timeout=30);assert len(calls)==1
    ask(a,'Now show only the top two.')
    ask(a,'And compare their average value.')
    last=a.session_state['analyst-conversation'].last_response
    assert {r[0] for r in last['primary']['rows']}=={'A','B'}
    assert len(calls)==3
    ask(a,'And compare their average value.')
    assert len(calls)==3


def test_filter_and_dataset_changes_do_not_reuse_answers(ai_workspace,monkeypatch,tmp_path):
    calls=[];mock_model(monkeypatch,calls)
    a=open_analyst();a.radio(key='analyst-mode').set_value('Local AI Analyst').run(timeout=30);ask(a,'Show record counts by group.')
    field(a,'selectbox','Category filter 1').select('group').run(timeout=30)
    assert not a.session_state['analyst-conversation'].entries
    p=tmp_path/'other.csv';p.write_text('unrelated,value\nZ,123\n');ai_workspace.add_file(p,'other')
    a.run(timeout=30);field(a,'selectbox','Analysis table').select('other').run(timeout=30)
    assert not a.exception and not a.session_state['analyst-conversation'].entries
    assert len(calls)==1


def test_workspace_display_rename_does_not_invalidate_existing_analyst(ai_workspace):
    a=open_analyst();ask(a,'How many records?')
    before=a.session_state['analyst-conversation'].signature
    field(a,'text_input','Workspace display name').set_value('Renamed research')
    field(a,'button','Save workspace name').click().run(timeout=30)
    assert not a.exception and a.session_state['analyst-conversation'].signature==before
    assert len(a.session_state['analyst-conversation'].entries)==1
