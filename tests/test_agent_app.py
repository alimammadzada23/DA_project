import json
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest
from insightpilot.agent import Analyst


def mock_provider(monkeypatch,messages):
    monkeypatch.setenv('INSIGHTPILOT_LLM_API_KEY','synthetic-test-key')
    monkeypatch.setenv('INSIGHTPILOT_LLM_MODEL','mock-model')
    class Response:
        def __init__(self,message): self.message=message
        def raise_for_status(self): pass
        def json(self): return {'choices':[{'message':self.message}]}
    class Client:
        def __init__(self,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def post(self,*args,**kwargs): return Response(messages.pop(0))
    monkeypatch.setattr('insightpilot.agent.httpx.Client',Client)


def test_live_protocol_with_mock_provider(database,tmp_path,monkeypatch):
    mock_provider(monkeypatch,[
        {'role':'assistant','content':None,'tool_calls':[{'id':'call1','type':'function','function':{'name':'query','arguments':json.dumps({'sql':'SELECT count(*) AS items FROM cases'})}}]},
        {'role':'assistant','content':'The synthetic fixture has 4 items; see executed SQL.'},
    ])
    path=tmp_path/'audit.json'
    result=Analyst(database).chat('How many items?',audit_path=path)
    assert result['evidence'][0]['rows']==[(4,)]
    audit=json.loads(path.read_text())
    assert audit['messages'][-2]['role']=='tool'
    assert 'synthetic-test-key' not in path.read_text()


def test_ungrounded_response_rejected(database,tmp_path,monkeypatch):
    mock_provider(monkeypatch,[{'role':'assistant','content':'There are 1000 items.'}])
    with pytest.raises(ValueError,match='Ungrounded'):
        Analyst(database).chat('How many?',audit_path=tmp_path/'audit.json')


def test_missing_llm_credentials(database,tmp_path,monkeypatch):
    monkeypatch.delenv('INSIGHTPILOT_LLM_API_KEY',raising=False)
    with pytest.raises(ValueError,match='Set INSIGHTPILOT'):
        Analyst(database).chat('Question',audit_path=tmp_path/'audit.json')
    assert Analyst(database).investigate('item_volume')['rows']==[(4,)]


def test_app_missing_data(monkeypatch,tmp_path):
    monkeypatch.setenv('INSIGHTPILOT_DATABASE',str(tmp_path/'missing.duckdb'))
    app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py')).run()
    assert not app.exception
    assert any('No analytical database' in info.value for info in app.info)


def test_app_loaded_data(database,monkeypatch):
    monkeypatch.setenv('INSIGHTPILOT_DATABASE',str(database))
    app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py')).run(timeout=20)
    assert not app.exception
    assert any('SYNTHETIC' in w.value for w in app.warning)
    assert len(app.tabs)==5
    app.button[0].click().run(timeout=20)
    assert not app.exception
