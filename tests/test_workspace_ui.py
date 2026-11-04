"""Replacement contracts for the deliberately retired industry-specific routes."""
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest
from insightpilot.universal import Workspace
from insightpilot.universal.connectors import import_database
from insightpilot.datasets import digest
from test_complaints import complaints

APP=str(Path(__file__).parents[1]/'app.py')


def test_fresh_launch_has_one_product_and_no_synthetic_substitution(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('INSIGHTPILOT_UNIVERSAL_WORKSPACE',raising=False)
    app=AppTest.from_file(APP).run(timeout=30)
    assert not app.exception and not app.error
    assert app.radio(key='universal-page').value=='Data Hub'
    assert not any(s.key=='dataset_workspace' for s in app.selectbox)
    assert not app.metric
    text=' '.join(m.value for m in app.markdown)
    assert 'Customer Experience' not in text and 'Procurement Intelligence' not in text
    assert any(w.label=='Dataset files' for w in app.get('file_uploader'))


def test_previous_databases_import_read_only_without_rewriting_source(database,tmp_path):
    before=digest(database)
    w=Workspace.create(tmp_path/'unified')
    import_database(w,database,'events','observations')
    assert w.info('observations')['rows']==10
    assert digest(database)==before
    assert w.info('observations')['source']['origin']=='database_extract'


def test_previous_complaint_database_remains_a_plain_catalog_source(complaints,tmp_path):
    _,complaints=complaints
    before=digest(complaints)
    w=Workspace.create(tmp_path/'unified')
    import_database(w,complaints,'complaints','observations')
    assert w.info('observations')['rows']==8
    assert digest(complaints)==before
    assert w.info('observations')['roles']=={}
