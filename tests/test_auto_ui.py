"""Automatic analysis through the real Streamlit app, and the unchanged manual paths."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from insightpilot.universal import Workspace
from insightpilot.universal import auto_analysis as aa
from insightpilot.universal.projects import ProjectSession

APP = str(Path(__file__).parents[1] / 'app.py')
FIXTURE = Path(__file__).parent / 'fixtures' / 'SYNTHETIC-auto-binary.csv'


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = Workspace.create(tmp_path / 'source')
    source.add_file(FIXTURE, 'customers', provenance={'origin': 'synthetic'})
    root = tmp_path / 'catalog'
    session = ProjectSession.start(root, source.directory)
    monkeypatch.setenv('INSIGHTPILOT_WORKSPACE_ROOT', str(root))
    monkeypatch.setenv('INSIGHTPILOT_UNIVERSAL_WORKSPACE', str(session.directory))
    return session


def ok(app):
    assert not app.exception, [e.value for e in app.exception]
    return app


def by_label(items, label):
    return next(item for item in items if item.label == label)


def open_page(page):
    app = ok(AppTest.from_file(APP, default_timeout=60).run())
    return ok(app.radio(key='universal-page').set_value(page).run())


def stage_table(app):
    return next(frame.value for frame in app.dataframe if 'Stage' in frame.value.columns)


def test_reports_still_opens_on_the_report_builder(session):
    app = open_page('Reports')
    assert app.radio(key='reports-view').value == 'Report builder'
    assert any(h.value == 'Build a defensible business report' for h in app.subheader)


def test_dataset_page_offers_manual_or_automatic(session):
    app = open_page('Data Hub')
    ok(by_label(app.radio, 'Data Hub view').set_value('Dataset library').run())
    assert {'Analyze manually', 'Run automatic analysis', 'Explore data'} <= {b.label for b in app.button}
    ok(by_label(app.button, 'Run automatic analysis').click().run())
    assert app.radio(key='universal-page').value == 'Reports'
    assert app.radio(key='reports-view').value == 'Automatic analysis'
    assert by_label(app.selectbox, 'Target column').value == 'churn'
    assert by_label(app.selectbox, 'Positive class').value == 'Yes'


def test_analyze_manually_continues_the_existing_flow(session):
    app = open_page('Data Hub')
    ok(by_label(app.radio, 'Data Hub view').set_value('Dataset library').run())
    ok(by_label(app.button, 'Analyze manually').click().run())
    assert app.radio(key='universal-page').value == 'Quality & Preparation'


def test_run_shows_stages_report_and_downloads(session):
    app = open_page('Reports')
    ok(app.radio(key='reports-view').set_value('Automatic analysis').run())
    ok(by_label(app.selectbox, 'Time axis (optional)').set_value('signup').run())
    ok(by_label(app.button, 'Run automatic analysis').click().run())
    assert stage_table(app).Status.tolist() == ['done'] * 9
    assert {b.label for b in app.get('download_button')} >= {'Download HTML', 'Download Excel'}
    assert len(app.get('plotly_chart')) > 10
    runs = aa.list_runs(session.workspace)
    assert len(runs) == 1 and runs[0]['target'] == 'churn'
    blocks = [b for b in session.workspace.saved_objects('block').values() if b.get('automatic_analysis') == runs[0]['run_id']]
    info = session.workspace.info('customers')
    assert blocks and info['current'] == info['versions'][0]['table']
    # The saved report reopens from the Previous reports tab.
    app = open_page('Reports')
    ok(app.radio(key='reports-view').set_value('Automatic analysis').run())
    assert by_label(app.selectbox, 'Report').value == runs[0]['run_id']


def test_descriptive_run_without_target(session):
    app = open_page('Reports')
    ok(app.radio(key='reports-view').set_value('Automatic analysis').run())
    ok(by_label(app.selectbox, 'Target column').select_index(0).run())
    assert by_label(app.selectbox, 'Target column').value is None
    assert not any(s.label == 'Positive class' for s in app.selectbox)
    ok(by_label(app.button, 'Run automatic analysis').click().run())
    statuses = stage_table(app).set_index('Stage').Status
    assert statuses['Ranking'] == 'skipped' and statuses['Relationships between fields'] == 'done'


def test_fill_missing_from_the_preparation_page(session):
    workspace = session.workspace
    workspace.transform('customers', {'kind': 'batch', 'steps': [{'kind': 'numeric', 'column': 'balance'}, {'kind': 'numeric', 'column': 'tenure'}]})
    app = open_page('Quality & Preparation')
    ok(by_label(app.selectbox, 'Preparation operation').set_value('fill_missing').run())
    ok(app.selectbox(key='prepare-column-customers').set_value('balance').run())
    ok(by_label(app.text_input, 'Fill value').set_value('0').run())
    ok(app.selectbox(key='fill-condition-customers').set_value('tenure').run())
    ok(by_label(app.text_input, 'Value of tenure').set_value('0').run())
    app.checkbox(key='fill-indicator-customers').check()
    ok(by_label(app.button, 'Apply preparation to a new version').click().run())
    version = workspace.info('customers')['versions'][-1]
    assert version['operation']['kind'] == 'fill_missing' and version['filled'] == 6
    assert 'balance_was_missing' in workspace.columns('customers')
    assert any('filled 6 missing values in balance' in i.value for i in app.info)
