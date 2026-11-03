import csv
from datetime import date
import io
import json
from pathlib import Path

import duckdb
import httpx
import pytest

from insightpilot import business_charts
from insightpilot.agent import Analyst
from insightpilot.complaints import API, Complaints, download_extract, export_complaints, ingest_complaints, management_summary, reconcile_complaints
from insightpilot.datasets import TableFilters, metadata

HEADERS = ['Complaint ID', 'Date received', 'Product', 'Issue', 'Company', 'Company response to consumer', 'Timely response?']


def complaint_csv():
    """Hand-calculated synthetic reference. No company is a genuine CFPB observation."""
    rows = [
        ['1', '2025-09-01', 'Credit', 'A', 'SYNTHETIC Alpha', 'Closed', 'Yes'],
        ['2', '2025-09-01', 'Loan', 'B', 'SYNTHETIC Alpha', 'Closed', 'No'],
        ['3', '2025-09-02', 'Credit', 'A', 'SYNTHETIC Beta', 'Closed', 'Yes'],
        ['4', '2025-09-04', 'Credit', 'C', 'SYNTHETIC Beta', 'In progress', ''],
        ['5', '2025-09-05', 'Credit', 'C', 'SYNTHETIC Alpha', 'Closed', 'Yes'],
        ['6', '2025-09-06', 'Loan', 'C', 'SYNTHETIC Beta', 'In progress', 'Unknown'],
        ['7', 'invalid', 'Credit', 'A', '', 'Closed', 'Yes'],
        ['8', '2025-09-06', 'Loan', 'C', 'SYNTHETIC Gamma', 'Closed', 'No'],
    ]
    stream = io.StringIO(); writer = csv.writer(stream); writer.writerow(HEADERS)
    writer.writerows(rows + [rows[2], ['bad', '2025-09-01', '', '', '', '', '']])
    return stream.getvalue()


@pytest.fixture
def complaints(tmp_path):
    source = tmp_path / 'SYNTHETIC-complaints.csv'; source.write_text(complaint_csv())
    database = tmp_path / 'complaints.duckdb'; ingest_complaints(source, database, synthetic=True)
    return source, database


def test_independent_metric_reference_and_source_reconciliation(complaints):
    source, database = complaints; service = Complaints(database)
    values, _ = service.overview()
    assert values == {'complaints': 8, 'companies': 3, 'known_timely': 6, 'timely_yes': 4,
        'timely_no': 2, 'missing_dates': 1, 'timely_share': pytest.approx(2/3)}
    assert service.investigate('products')['rows'] == [('Credit', 5, .625), ('Loan', 3, .375)]
    assert dict((r[0], r[1]) for r in service.investigate('issues')['rows']) == {'A': 3, 'B': 1, 'C': 4}
    assert reconcile_complaints(source, database)['passed']
    assert all(reconcile_complaints(source, database)['checks'].values())


def test_quality_counts_all_exclusions_without_treating_missing_as_zero(complaints):
    _, database = complaints; info = metadata(database); qa = info['quality']
    assert qa['source_rows'] == 10 and qa['imported_rows'] == 8 and qa['excluded_rows'] == 2
    assert qa['duplicate_ids'] == 1 and qa['conflicting_duplicate_ids'] == 0
    assert qa['invalid_received_dates'] == 1 and qa['unrecognized_timely_values'] == 1 and qa['missing_timely'] == 1
    assert info['origin'] == 'synthetic'
    with duckdb.connect(str(database), read_only=True) as con:
        assert con.execute('SELECT count(*) FROM raw_complaints').fetchone()[0] == 10
        assert con.execute('SELECT count(*) FROM complaints WHERE timely IS NULL').fetchone()[0] == 2


def test_company_filters_all_views_and_drilldown_share_one_cohort(complaints):
    _, database = complaints; service = Complaints(database, TableFilters(companies=('SYNTHETIC Alpha',)))
    assert service.overview()[0]['complaints'] == 3
    assert service.overview()[0]['timely_share'] == pytest.approx(2/3)
    assert sum(r[1] for r in service.investigate('donut')['rows']) == 3
    assert sum(r[1] for r in service.investigate('daily')['rows']) == 3
    assert sum(r[2] for r in service.investigate('company_product')['rows']) == 3
    assert service.records(dimension='company', value='SYNTHETIC Beta')['rows'] == []
    assert len(service.records()['rows']) == 3
    assert service.investigate('companies')['filters']['companies'] == ('SYNTHETIC Alpha',)
    assert Complaints(database, TableFilters(companies=("' OR 1=1 --",))).overview()[0]['complaints'] == 0


def test_dates_are_inclusive_and_null_category_is_filterable(complaints):
    _, database = complaints
    assert Complaints(database, TableFilters(start_date=date(2025,9,1), end_date=date(2025,9,1))).overview()[0]['complaints'] == 2
    assert Complaints(database, TableFilters(companies=(None,))).overview()[0]['complaints'] == 1
    assert Complaints(database, TableFilters(timely=(None,))).overview()[0]['known_timely'] == 0


def test_equal_period_changes_and_zero_baseline_are_not_fabricated(complaints):
    _, database = complaints; service = Complaints(database)
    result = service.issue_changes()
    assert result['comparison_periods']['days_per_period'] == 3
    assert result['rows'][0] == ('C', 0, 4, None)
    answer = service.guided_answer('Which issues appear to be increasing?')
    assert 'undefined' in answer['answer'] and answer['evidence']


@pytest.mark.parametrize('question', [
    'Which companies have the highest reported complaint volumes?', 'Which products account for the largest share of complaints?',
    'How have complaints changed over time?', 'Compare the complaint categories of two selected companies.',
    'Which issues appear to be increasing?', 'What should a customer-experience manager investigate next?',
])
def test_business_questions_execute_actual_filtered_sql(complaints, question):
    _, database = complaints; result = Complaints(database, TableFilters(companies=('SYNTHETIC Alpha', 'SYNTHETIC Beta'))).guided_answer(question)
    assert result['evidence'] and result['answer']
    assert all(item['source_sha256'] and item['dataset_id'] for item in result['evidence'])
    assert all(item['filters']['companies'] == ('SYNTHETIC Alpha', 'SYNTHETIC Beta') for item in result['evidence'])
    assert 'no LLM' in result['mode']


def test_donut_and_chart_series_reconcile_to_the_selected_totals(complaints):
    _, database = complaints; service = Complaints(database)
    donut = business_charts.donut(service.investigate('donut'))
    assert sum(donut.data[0].values) == 8
    assert dict(zip(donut.data[0].labels, donut.data[0].values)) == {'Credit': 5, 'Loan': 3}
    assert sum(business_charts.trend(service.investigate('daily')).data[0].y) == 7
    assert sum(sum(trace.y) for trace in business_charts.stacked(service.investigate('company_product')).data) == 8
    assert sum(business_charts.histogram(service.investigate('company_distribution')).data[0].y) == 3


def test_empty_selection_has_null_percentages_and_usable_charts(complaints):
    _, database = complaints; service = Complaints(database, TableFilters(products=('absent',)))
    assert service.overview()[0]['complaints'] == 0 and service.overview()[0]['timely_share'] is None
    for key in service.catalog: service.investigate(key)
    assert service.issue_changes()['rows'] == []
    business_charts.donut(service.investigate('donut')); business_charts.heatmap(service.investigate('company_product'))


def test_no_exposure_denominators_or_causal_claims_are_invented(complaints):
    service = Complaints(complaints[1])
    for question in ['Which company is worst?', 'What is the root cause?', 'Estimate savings and ROI']:
        assert service.guided_answer(question)['evidence'] == []
    assert 'exposure' in management_summary(service)


def test_import_failures_leave_no_partial_database_and_manual_source_is_unverified(tmp_path):
    file = tmp_path / 'not-cfpb.csv'; file.write_text('company,amount\nA,100\n')
    database = tmp_path / 'bad.duckdb'
    with pytest.raises(ValueError, match='schema mismatch'): ingest_complaints(file, database)
    assert not database.exists()
    file.write_text(complaint_csv()); info = ingest_complaints(file, database)
    assert info['origin'] == 'user_upload' and not info['usage_verified'] and info['downloaded_at'] is None


def test_export_contains_actual_analytical_rows_provenance_and_query_evidence(complaints, tmp_path):
    _, database = complaints; directory = tmp_path / 'export'; result = export_complaints(database, directory)
    assert result['rows'] == 8
    assert pd_count(directory / 'Complaints.parquet') == 8
    assert json.loads((directory / 'manifest.json').read_text())['origin'] == 'synthetic'
    assert (directory / 'management-summary.md').is_file() and (directory / 'query-evidence.json').is_file()


def pd_count(path):
    with duckdb.connect() as con: return con.execute('SELECT count(*) FROM read_parquet(?)', [str(path)]).fetchone()[0]


def test_connector_handles_denied_access_without_claiming_data(tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(403, text='denied')))
    with pytest.raises(httpx.HTTPStatusError): download_extract(tmp_path / 'blocked', '2025-09-01', '2025-09-08', client=client)
    assert not (tmp_path / 'blocked/analytics.duckdb').exists()
    assert (tmp_path / 'blocked/download-failure.json').is_file()


@pytest.mark.parametrize('boundary', [False, True])
def test_official_connector_protocol_with_mock_transport_and_explicit_local_boundaries(tmp_path, boundary):
    # Mocked protocol only; these are NOT live CFPB data or source acceptance.
    stream = io.StringIO(); writer = csv.writer(stream); writer.writerow(HEADERS)
    rows = [['1', '2025-09-01', 'Credit', 'A', 'SYNTHETIC Alpha', 'Closed', 'Yes']]
    if boundary: rows.append(['2', '2025-09-08', 'Loan', 'B', 'SYNTHETIC Beta', 'Closed', 'No'])
    writer.writerows(rows)
    def respond(request):
        if request.url.params.get('format') == 'csv': return httpx.Response(200, text=stream.getvalue())
        if request.url.path.endswith('/api/v1/'):
            return httpx.Response(200, json={'hits': {'total': {'value': len(rows), 'relation': 'eq'}}, '_meta': {'is_data_stale': False}})
        return httpx.Response(200, text='SYNTHETIC MOCK publisher document')
    client = httpx.Client(transport=httpx.MockTransport(respond))
    directory = tmp_path / 'mock-protocol'
    info = download_extract(directory, '2025-09-01', '2025-09-08', client=client)
    assert info['rows'] == 1 and info['quality']['outside_requested_coverage'] == int(boundary)
    assert info['download_receipt']['expected_record_count'] == len(rows)
    assert info['download_receipt']['boundary_date_records'] == int(boundary)
    assert reconcile_complaints(directory / 'complaints.csv', directory / 'analytics.duckdb')['passed']


def test_connector_refuses_a_csv_that_does_not_match_the_exact_api_count(tmp_path):
    stream = io.StringIO(); writer = csv.writer(stream); writer.writerow(HEADERS)
    writer.writerow(['1', '2025-09-01', 'Credit', 'A', 'SYNTHETIC Alpha', 'Closed', 'Yes'])
    def respond(request):
        if request.url.params.get('format') == 'csv': return httpx.Response(200, text=stream.getvalue())
        return httpx.Response(200, json={'hits': {'total': {'value': 99, 'relation': 'eq'}}})
    with pytest.raises(ValueError, match='record count'):
        download_extract(tmp_path / 'mismatch', '2025-09-01', '2025-09-08', client=httpx.Client(transport=httpx.MockTransport(respond)))
    assert not (tmp_path / 'mismatch/analytics.duckdb').exists()


def test_sql_runner_stays_read_only_and_no_other_dataset_is_accessible(complaints):
    _, database = complaints; runner = Analyst(database, domain='complaints', data_tables={'complaints'})
    assert runner.query('SELECT count(*) FROM complaints')['rows'] == [(8,)]
    for sql in ["DELETE FROM complaints", "SELECT * FROM read_csv('/etc/passwd')", 'SELECT * FROM cases']:
        with pytest.raises((ValueError, duckdb.Error)): runner.query(sql)


def test_metadata_and_export_coexist_with_a_live_read_only_analytical_connection(complaints, tmp_path):
    from insightpilot.agent import SQL_CONFIG
    with duckdb.connect(str(complaints[1].resolve()), read_only=True, config=SQL_CONFIG) as active:
        assert active.execute('SELECT count(*) FROM complaints').fetchone()[0] == 8
        assert metadata(complaints[1])['rows'] == 8
        assert Complaints(complaints[1]).overview()[0]['complaints'] == 8
        assert export_complaints(complaints[1], tmp_path / 'concurrent-export')['rows'] == 8


def test_optional_live_protocol_uses_only_the_filtered_complaint_catalog(complaints, tmp_path, monkeypatch):
    from test_agent_app import mock_provider
    mock_provider(monkeypatch, [
        {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'scoped', 'type': 'function',
            'function': {'name': 'investigate', 'arguments': json.dumps({'question_id': 'summary'})}}]},
        {'role': 'assistant', 'content': 'The selected synthetic reference contains three reported complaints.'},
    ])
    service = Complaints(complaints[1], TableFilters(companies=('SYNTHETIC Alpha',)))
    result = Analyst(complaints[1], domain='complaints', limitations=service.limitations, data_tables={'complaints'}).chat(
        'Summarize this company', dashboard=service, audit_path=tmp_path / 'mock-audit.json')
    assert result['evidence'][0]['rows'][0][0] == 3
    assert result['evidence'][0]['filters']['companies'] == ('SYNTHETIC Alpha',)
    assert 'exposure' in result['limitations']
