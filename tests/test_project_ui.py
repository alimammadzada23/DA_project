"""Explicit project decisions through the actual Streamlit application."""
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest
from insightpilot.universal.projects import ProjectSession,digest
from insightpilot.universal.workspace_labels import catalog,create_named,metadata
from insightpilot.universal.engine import Engine
from test_universal_ui import APP


def widget(app,kind,label):return next(w for w in getattr(app,kind) if w.label==label)


@pytest.fixture
def session(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    root=tmp_path/'projects';s=ProjectSession.start(root)
    monkeypatch.setenv('INSIGHTPILOT_WORKSPACE_ROOT',str(root))
    monkeypatch.setenv('INSIGHTPILOT_UNIVERSAL_WORKSPACE',str(s.directory))
    return s


def seed(session,tmp_path):
    p=tmp_path/'facts.csv';p.write_text('region,amount\nNorth,10\nSouth,20\n')
    session.workspace.add_file(p,'facts')
    e=Engine(session.workspace,'facts')
    from insightpilot.universal.metrics import Metric
    from insightpilot.universal.report import configuration
    session.workspace.save_object('block','Region counts',{'analysis':configuration(e,Metric('Records')),'evidence':e.groups('region')})


def save(app,name):
    widget(app,'button','Save Project').click().run(timeout=30)
    widget(app,'text_input','Project name').set_value(name)
    widget(app,'button','Save Project').click().run(timeout=30)
    return app


def test_initial_session_is_empty_and_not_a_saved_project(session):
    app=AppTest.from_file(APP).run(timeout=30)
    assert catalog(session.root)==[]
    assert widget(app,'button','Save Project') and widget(app,'button','Discard Project')
    assert not any(s.label=='Analysis table' for s in app.selectbox)
    assert not app.exception and not app.error


def test_save_then_edit_requires_save_changes(session,tmp_path):
    seed(session,tmp_path)
    app=AppTest.from_file(APP).run(timeout=30);save(app,'Keep this')
    saved=catalog(session.root)[0];before=digest(saved.directory)
    session.workspace.save_object('view','Unsaved edit',{'scope':'temporary'})
    app.run(timeout=30)
    assert digest(saved.directory)==before
    assert any(c.value=='UNSAVED CHANGES' for c in app.caption)
    widget(app,'button','Save Changes').click().run(timeout=30)
    assert 'Unsaved edit' in saved.saved_objects('view')
    assert not app.exception


def test_cancel_leave_preserves_report_text_then_discard_starts_empty(session,tmp_path):
    seed(session,tmp_path)
    app=AppTest.from_file(APP).run(timeout=30)
    app.radio(key='universal-page').set_value('Reports').run(timeout=30)
    widget(app,'text_input','Business report title').set_value('Editable draft')
    widget(app,'text_area','Executive summary').set_value('Keep these unsaved notes.').run(timeout=30)
    widget(app,'button','＋ New project').click().run(timeout=30)
    assert widget(app,'button','Save Project') and widget(app,'button','Discard Project')
    widget(app,'button','Cancel').click().run(timeout=30)
    assert widget(app,'text_area','Executive summary').value=='Keep these unsaved notes.'
    widget(app,'button','＋ New project').click().run(timeout=30)
    widget(app,'button','Discard Project').click().run(timeout=30)
    assert not catalog(session.root)
    assert not any(s.label=='Analysis table' for s in app.selectbox)
    assert not app.exception


def test_guard_save_publishes_before_opening_new_empty_project(session,tmp_path):
    seed(session,tmp_path)
    app=AppTest.from_file(APP).run(timeout=30)
    widget(app,'button','＋ New project').click().run(timeout=30)
    widget(app,'button','Save Project').click().run(timeout=30)
    widget(app,'text_input','Project name').set_value('Saved on leaving')
    widget(app,'button','Save Project').click().run(timeout=30)
    assert metadata(catalog(session.root)[0])['name']=='Saved on leaving'
    assert catalog(session.root)[0].info('facts')['rows']==2
    assert not any(s.label=='Analysis table' for s in app.selectbox)
    assert not app.exception


def test_confirmed_delete_preserves_unrelated_project(session,tmp_path):
    seed(session,tmp_path);saved=session.save('Delete only this')
    unrelated=create_named(session.root,'Preserve me');before=digest(unrelated.directory)
    app=AppTest.from_file(APP).run(timeout=30)
    app.button(key='delete-active-project').click().run(timeout=30)
    widget(app,'button','Cancel').click().run(timeout=30)
    assert saved.database.exists()
    app.button(key='delete-active-project').click().run(timeout=30)
    widget(app,'button','Confirm Delete').click().run(timeout=30)
    assert not saved.database.exists()
    assert digest(unrelated.directory)==before
    assert [metadata(w)['name'] for w in catalog(session.root)]==['Preserve me']
    assert not app.exception and not app.error


def test_save_project_includes_current_report_editor_without_separate_draft_save(session,tmp_path):
    seed(session,tmp_path)
    app=AppTest.from_file(APP).run(timeout=30)
    app.radio(key='universal-page').set_value('Reports').run(timeout=30)
    widget(app,'text_input','Business report title').set_value('Project-owned report')
    widget(app,'multiselect','Choose saved findings').set_value(['Region counts'])
    widget(app,'text_area','Executive summary').set_value('Analyst supplied text.').run(timeout=30)
    save(app,'Report project')
    report=catalog(session.root)[0].saved_objects('layout')['ux.report:Project-owned report']
    assert report['sections']['executive_summary']=='Analyst supplied text.'
    assert report['evidence_order']==['Region counts']
    assert not app.exception


def test_navigation_alone_does_not_mark_a_saved_project_dirty(session,tmp_path):
    seed(session,tmp_path)
    app=AppTest.from_file(APP).run(timeout=30)
    app.radio(key='universal-page').set_value('Explore').run(timeout=30)
    widget(app,'selectbox','Group by').select('region').run(timeout=30)
    save(app,'Clean navigation')
    app.radio(key='universal-page').set_value('Workspace Home').run(timeout=30)
    app.run(timeout=30)
    assert any(c.value=='ALL CHANGES SAVED' for c in app.caption)
    widget(app,'button','＋ New project').click().run(timeout=30)
    assert not app.get('dialog')
    assert not any(s.label=='Analysis table' for s in app.selectbox)
    assert not app.exception
