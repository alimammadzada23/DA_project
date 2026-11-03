from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from insightpilot.datasets import ingest_table
from insightpilot.workspace_ui import COMPLAINT_PAGES, WORKSPACES
from test_complaints import complaints

APP = str(Path(__file__).parents[1] / 'app.py')


def open_complaints(complaints, monkeypatch):
    monkeypatch.setenv('INSIGHTPILOT_CFPB_DATABASE', str(complaints[1]))
    monkeypatch.delenv('INSIGHTPILOT_DATABASE', raising=False)
    app = AppTest.from_file(APP).run(timeout=30)
    app.selectbox(key='dataset_workspace').select(WORKSPACES[2]).run(timeout=30)
    return app


def test_fresh_install_opens_dataset_selection(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path); monkeypatch.delenv('INSIGHTPILOT_DATABASE', raising=False)
    app = AppTest.from_file(APP).run(timeout=30)
    assert not app.exception and app.title[0].value == 'InsightPilot — AI Business Intelligence'
    assert app.selectbox(key='dataset_workspace').options == WORKSPACES


@pytest.mark.parametrize('page', COMPLAINT_PAGES)
def test_all_complaint_views_render_filter_and_disclose_synthetic_scope(complaints, monkeypatch, page):
    app = open_complaints(complaints, monkeypatch)
    assert not app.exception and not app.error
    assert any('SYNTHETIC DATA' in warning.value for warning in app.warning)
    assert any('Selected cohort · 8 of 8' in caption.value for caption in app.caption)
    app.radio(key='complaint-page').set_value(page).run(timeout=30)
    company = next(widget for widget in app.multiselect if widget.label == 'Company')
    company.set_value(['SYNTHETIC Alpha']).run(timeout=30)
    assert not app.exception and not app.error
    assert any('Selected cohort · 3 of 8' in caption.value for caption in app.caption)
    assert not any(metric.label == 'PO items' for metric in app.metric)


@pytest.mark.parametrize('page', COMPLAINT_PAGES)
def test_empty_complaint_cohorts_are_usable(complaints, monkeypatch, page):
    app = open_complaints(complaints, monkeypatch)
    app.radio(key='complaint-page').set_value(page).run(timeout=30)
    next(w for w in app.multiselect if w.label == 'Company').set_value(['SYNTHETIC Gamma']).run(timeout=30)
    next(w for w in app.multiselect if w.label == 'Complaint issue').set_value(['A']).run(timeout=30)
    assert not app.exception
    assert any('No complaints match' in info.value for info in app.info)


def test_workspace_and_filter_switching_clear_current_answers(complaints, monkeypatch, tmp_path):
    custom = tmp_path / 'sales.csv'; custom.write_text('date,product,amount\n2025-01-01,P,10\n2025-01-02,Q,20\n')
    target = tmp_path / 'sales.duckdb'
    ingest_table(custom, target, {'date': 'date', 'product': 'product', 'measure': 'amount'}, title='Private test upload')
    monkeypatch.setenv('INSIGHTPILOT_CUSTOM_DATABASE', str(target))
    app = open_complaints(complaints, monkeypatch)
    app.radio(key='complaint-page').set_value('AI Business Analyst').run(timeout=30)
    next(b for b in app.button if b.label == 'Run business investigation').click().run(timeout=30)
    assert app.session_state['tabular_history'][0]['result']['evidence'][0]['rows'][0][1] == 3
    next(w for w in app.multiselect if w.label == 'Company').set_value(['SYNTHETIC Alpha']).run(timeout=30)
    assert app.session_state['tabular_history'] == []
    app.selectbox(key='dataset_workspace').select(WORKSPACES[3]).run(timeout=30)
    assert not app.exception and not app.error
    assert any(metric.label == 'Selected records' and metric.value == '2' for metric in app.metric)
    assert not any('complaint' in metric.label.lower() for metric in app.metric)
    app.selectbox(key='dataset_workspace').select(WORKSPACES[2]).run(timeout=30)
    assert not app.exception and app.session_state['tabular_history'] == []


def test_custom_unmapped_kpis_disabled_and_user_upload_labeled(monkeypatch, tmp_path):
    source = tmp_path / 'records.csv'; source.write_text('name,comment\nA,Hello\nB,World\n')
    database = tmp_path / 'custom.duckdb'; ingest_table(source, database, {})
    monkeypatch.setenv('INSIGHTPILOT_CUSTOM_DATABASE', str(database))
    monkeypatch.delenv('INSIGHTPILOT_DATABASE', raising=False)
    app = AppTest.from_file(APP).run(timeout=30)
    app.selectbox(key='dataset_workspace').select(WORKSPACES[3]).run(timeout=30)
    assert not app.exception
    assert any('USER-UPLOADED DATA' in info.value for info in app.info)
    assert any('Numeric KPIs are disabled' in info.value for info in app.info)
    assert any('Time trends are disabled' in info.value for info in app.info)


def test_missing_cfpb_does_not_silently_load_fabricated_findings(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('INSIGHTPILOT_CFPB_DATABASE', raising=False)
    monkeypatch.delenv('INSIGHTPILOT_DATABASE', raising=False)
    app = AppTest.from_file(APP).run(timeout=30)
    app.selectbox(key='dataset_workspace').select(WORKSPACES[2]).run(timeout=30)
    assert not app.exception and not app.metric
    assert any('No genuine complaint dataset is loaded' in info.value for info in app.info)


def test_live_llm_cannot_transmit_upload_without_explicit_consent(complaints, monkeypatch):
    app = open_complaints(complaints, monkeypatch)
    app.radio(key='complaint-page').set_value('AI Business Analyst').run(timeout=30)
    def forbidden(*args, **kwargs): raise AssertionError('No external call should occur')
    monkeypatch.setattr('insightpilot.agent.Analyst.chat', forbidden)
    next(w for w in app.radio if w.label == 'Analysis mode').set_value('Live LLM · optional').run(timeout=30)
    next(b for b in app.button if b.label == 'Run business investigation').click().run(timeout=30)
    assert not app.exception
    assert any('Authorize external transmission' in warning.value for warning in app.warning)
    assert app.session_state['tabular_history'] == []


def test_switching_complaint_snapshots_isolates_cache_filters_and_answers(complaints, monkeypatch, tmp_path):
    from insightpilot.complaints import ingest_complaints
    from test_complaints import HEADERS
    import csv
    directory = tmp_path / 'data/workspaces/second'; directory.mkdir(parents=True)
    source = directory / 'second.csv'
    with source.open('w', newline='') as stream:
        writer = csv.writer(stream); writer.writerow(HEADERS)
        writer.writerow(['100', '2025-10-01', 'Other', 'New issue', 'SYNTHETIC Different', 'Closed', 'No'])
    ingest_complaints(source, directory / 'analytics.duckdb', synthetic=True)
    monkeypatch.chdir(tmp_path)
    app = open_complaints(complaints, monkeypatch)
    app.radio(key='complaint-page').set_value('AI Business Analyst').run(timeout=30)
    next(b for b in app.button if b.label == 'Run business investigation').click().run(timeout=30)
    assert len(app.session_state['tabular_history']) == 1
    app.selectbox(key='complaints_dataset').select('data/workspaces/second/analytics.duckdb').run(timeout=30)
    assert not app.exception and app.session_state['tabular_history'] == []
    assert any('Selected cohort · 1 of 1' in caption.value for caption in app.caption)
    app.radio(key='complaint-page').set_value('Executive Overview').run(timeout=30)
    assert app.metric[0].value == '1' and app.metric[2].value == '0.0%'


def test_custom_process_view_filters_complete_histories_and_answers_questions(monkeypatch, tmp_path):
    source = tmp_path / 'events.csv'; source.write_text('case_id,activity,timestamp\nA,Open,2025-01-01T00:00:00Z\nA,Close,2025-01-03T00:00:00Z\nB,Open,2025-01-02T00:00:00Z\n')
    target = tmp_path / 'process.duckdb'
    ingest_table(source, target, {'case_id': 'case_id', 'activity': 'activity', 'date': 'timestamp'}, profile='event_log', synthetic=True)
    monkeypatch.setenv('INSIGHTPILOT_CUSTOM_DATABASE', str(target)); monkeypatch.delenv('INSIGHTPILOT_DATABASE', raising=False)
    app = AppTest.from_file(APP).run(timeout=30)
    app.selectbox(key='dataset_workspace').select(WORKSPACES[3]).run(timeout=30)
    assert not app.exception and app.metric[0].value == '2' and app.metric[1].value == '3'
    next(w for w in app.multiselect if w.label == 'Cases containing activity').set_value(['Close']).run(timeout=30)
    assert not app.exception and app.metric[0].value == '1' and app.metric[1].value == '2'
    next(w for w in app.radio if w.label == 'Mapped process analytics').set_value('AI Business Analyst').run(timeout=30)
    app.chat_input[0].set_value('How many cases?').run(timeout=30)
    assert not app.exception
    assert app.session_state['tabular_history'][0]['result']['evidence'][0]['rows'] == [(1,)]
