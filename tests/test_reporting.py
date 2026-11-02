import json
from pathlib import Path
import duckdb
import pytest
from insightpilot.ingest import ingest
from insightpilot.reporting import reconcile, analysis_report
from conftest import write_xes
from insightpilot.demo import create_demo
from insightpilot.analytics import discover


def test_independent_source_reconciliation(database):
    result=reconcile(database.parent/'synthetic.xes',database)
    assert result['passed']
    assert len(result['checks'])==10
    assert result['checks']['p90_span']['python_reference']==pytest.approx(700.9)


def test_wrong_source_rejected(database,tmp_path):
    path=tmp_path/'other.xes';path.write_text('<log/>')
    with pytest.raises(ValueError,match='SHA-256'):reconcile(path,database)


def test_tampered_database_detected(database):
    with duckdb.connect(str(database)) as con:con.execute("DELETE FROM events WHERE case_id='C'")
    result=reconcile(database.parent/'synthetic.xes',database)
    assert not result['passed']
    assert not result['checks']['event_volume']['matches']


def test_generated_decision_brief_is_labeled_and_traceable(database,tmp_path):
    result=analysis_report(database,tmp_path/'report')
    brief=Path(result['decision_brief']).read_text()
    evidence=json.loads(Path(result['evidence']).read_text())
    assert 'SYNTHETIC ONLY' in brief
    assert 'Recorded items: 4' in brief
    assert len(evidence['questions'])==20
    assert evidence['quality']['sha256'] in brief


def test_global_defaults_and_nested_attributes(tmp_path):
    source=tmp_path/'global.xes'
    source.write_text('''<log><global scope="event"><string key="concept:name" value="Default activity"/></global><trace><string key="concept:name" value="A"/><event/></trace></log>''')
    target=tmp_path/'global.duckdb';ingest(source,target,synthetic=True,batch_size=1)
    assert reconcile(source,target)['passed']
    source.write_text('''<log><trace><string key="concept:name" value="B"/><event><list key="nested"><string key="inside" value="x"/></list></event></trace></log>''')
    with pytest.raises(ValueError,match='Nested'):ingest(source,tmp_path/'nested.duckdb')


def test_demo_is_explicit_and_non_overwriting(tmp_path):
    result=create_demo(tmp_path/'demo')
    assert 'SYNTHETIC' in result['scope']
    assert reconcile(result['source'],result['database'])['passed']
    with pytest.raises(ValueError,match='already exist'):create_demo(tmp_path/'demo')


def test_discovery_does_not_drop_untimed_edges(tmp_path):
    source=tmp_path/'untimed.xes'
    write_xes(source,[('A','PO',None,[('Create',None,None),('Review',None,None)])])
    target=tmp_path/'untimed.duckdb';ingest(source,target,synthetic=True)
    with duckdb.connect(str(target),read_only=True) as con:
        assert con.execute('SELECT count(*) FROM transitions').fetchone()[0]==0
        assert discover(con)['edges']==[{'from':'Create','to':'Review','count':1}]


def test_blank_activities_are_missing_not_repeats(tmp_path):
    source=tmp_path/'blank.xes'
    write_xes(source,[('A','PO',None,[('',0,None),(' ',1,None)])])
    target=tmp_path/'blank.duckdb';ingest(source,target,synthetic=True)
    assert reconcile(source,target)['passed']
    with duckdb.connect(str(target),read_only=True) as con:
        assert con.execute('SELECT missing_activities,repeated_event_count FROM case_metrics').fetchone()==(2,0)
