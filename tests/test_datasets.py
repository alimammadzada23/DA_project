import csv
import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from insightpilot.custom import CustomAnalytics
from insightpilot.datasets import TableFilters, date_values, ingest_table, inspect_table, metadata, read_table
from insightpilot.dashboard import Dashboard


@pytest.fixture
def sales(tmp_path):
    path = tmp_path / 'SYNTHETIC-sales.csv'
    path.write_text('date,product,customer,quantity,price,amount\n2025-01-01,A,X,2,10,20\n2025-01-02,B,X,3,5,15\ninvalid,A,Y,1,10,bad\n2025-02-01,A,Z,,10,\n')
    return path


@pytest.mark.parametrize('extension', ['csv', 'xlsx', 'parquet'])
def test_three_formats_preserve_values_and_inspect_schema(sales, tmp_path, extension):
    frame = pd.read_csv(sales, dtype=str, keep_default_na=False)
    path = tmp_path / ('sample.' + extension)
    if extension == 'csv': frame.to_csv(path, index=False)
    elif extension == 'xlsx': frame.to_excel(path, index=False)
    else: frame.to_parquet(path, index=False)
    report = inspect_table(path)
    assert report['rows'] == 4
    assert report['suggested_mapping']['date'] == 'date'
    assert report['suggested_mapping']['measure'] == 'amount'
    assert next(c for c in report['columns'] if c['column'] == 'date')['recognized_dates'] == 3
    assert read_table(path).iloc[2]['amount'] == 'bad'


def test_mapping_does_not_invent_revenue_or_dates(sales, tmp_path):
    database = tmp_path / 'sales.duckdb'
    info = ingest_table(sales, database, {'date': 'date', 'product': 'product', 'measure': 'amount'}, synthetic=True)
    assert info['origin'] == 'synthetic' and not info['revenue_confirmed']
    assert info['quality']['mapped_invalid_dates'] == 1
    assert info['quality']['numeric_exclusions']['measure'] == 1
    result = CustomAnalytics(database).investigate('summary')['rows'][0]
    assert result[:4] == (4, 3, 2, 35)
    assert CustomAnalytics(database).guided_answer('What is revenue?')['evidence'] == []
    assert 'Revenue is disabled' in CustomAnalytics(database).guided_answer('What is revenue?')['answer']
    assert 'trends' in info['capabilities']


def test_approved_derived_revenue_and_customer_segmentation(sales, tmp_path):
    database = tmp_path / 'derived.duckdb'
    mapping = {'date': 'date', 'product': 'product', 'customer': 'customer', 'quantity': 'quantity', 'unit_price': 'price'}
    info = ingest_table(sales, database, mapping, profile='transactions', revenue=True)
    assert 'revenue' in info['capabilities'] and info['origin'] == 'user_upload'
    result = CustomAnalytics(database).investigate('summary')['rows'][0]
    assert result[2:4] == (3, 45)
    assert CustomAnalytics(database).investigate('customers')['rows'][0] == ('X', 2, 2, 35)
    selected = CustomAnalytics(database, TableFilters(products=('B',)))
    assert selected.investigate('summary')['rows'][0][:4] == (1, 1, 1, 15)
    assert selected.records()['rows'][0][0] == 2


def test_unsupported_capabilities_disabled(sales, tmp_path):
    target = tmp_path / 'unmapped.duckdb'; info = ingest_table(sales, target, {})
    assert info['capabilities'] == ['records', 'quality']
    service = CustomAnalytics(target)
    assert set(service.catalog) == {'summary'}
    assert service.guided_answer('Show trends')['evidence'] == []
    assert service.investigate('summary')['rows'][0][3] is None


@pytest.mark.parametrize('mapping,profile,revenue', [({'date': 'missing'}, 'descriptive', False),
    ({'mystery': 'amount'}, 'descriptive', False), ({'date': 'date', 'category': 'date'}, 'descriptive', False),
    ({}, 'event_log', False), ({'measure': 'amount'}, 'descriptive', True), ({}, 'transactions', True)])
def test_invalid_mappings_rejected_atomically(sales, tmp_path, mapping, profile, revenue):
    target = tmp_path / 'bad.duckdb'
    with pytest.raises(ValueError): ingest_table(sales, target, mapping, profile=profile, revenue=revenue)
    assert not target.exists()


def test_explicit_slash_date_convention():
    values = pd.Series(['02/03/2025', '2025-04-05', 'not a date', None])
    assert str(date_values(values)[0]) == '2025-02-03'
    assert str(date_values(values, True)[0]) == '2025-03-02'
    assert date_values(values)[2] is None


def test_mapped_event_log_preserves_existing_process_engine(tmp_path):
    source = tmp_path / 'events.csv'; source.write_text('case_id,activity,timestamp\nA,Open,2025-01-01T00:00:00Z\nA,Close,2025-01-03T00:00:00Z\nB,Open,2025-01-02T00:00:00Z\n')
    database = tmp_path / 'process.duckdb'
    info = ingest_table(source, database, {'case_id': 'case_id', 'activity': 'activity', 'date': 'timestamp'}, profile='event_log')
    assert 'process_variants' in info['capabilities'] and 'revenue' not in info['capabilities']
    assert Dashboard(database).overview()[0]['item_count'] == 2
    assert Dashboard(database).overview()[0]['median_span_days'] == 1
    assert metadata(database)['source_sha256'] == info['source_sha256']
    from insightpilot.custom import CustomProcessAnalytics
    process = CustomProcessAnalytics(database)
    assert process.guided_answer('How many cases?')['evidence'][0]['rows'] == [(2,)]
    assert '1.00 days' in process.guided_answer('What is the observed span?')['answer']
    assert process.guided_answer('Which process variants are common?')['evidence']
    assert process.guided_answer('What are the longest waiting intervals?')['evidence']
    assert process.guided_answer('Which matching compliance rules fail?')['evidence'] == []


def test_custom_complaint_profile_uses_only_approved_fields_and_retains_upload_origin(tmp_path):
    from insightpilot.complaints import Complaints
    source = tmp_path / 'my-data.csv'
    source.write_text('when,firm,service,problem,on_time\n2025-01-01,A,Card,Fees,Yes\n2025-01-02,A,Card,Fees,\n2025-01-03,B,Loan,Billing,No\n')
    target = tmp_path / 'mapped-complaints.duckdb'
    info = ingest_table(source, target, {'date': 'when', 'company': 'firm', 'product': 'service', 'issue': 'problem', 'timely': 'on_time'}, profile='complaints')
    assert info['origin'] == 'user_upload' and info['module'] == 'custom'
    assert Complaints(target).overview()[0]['timely_share'] == .5
    assert Complaints(target).overview()[0]['complaints'] == 3


def test_invalid_event_times_and_missing_cases_are_not_silently_dropped(tmp_path):
    source = tmp_path / 'events.csv'; source.write_text('case_id,activity,timestamp\nA,Open,2025-01-01T00:00:00\n')
    mapping = {'case_id': 'case_id', 'activity': 'activity', 'date': 'timestamp'}
    with pytest.raises(ValueError, match='UTC'): ingest_table(source, tmp_path / 'bad.duckdb', mapping, profile='event_log')
    assert not (tmp_path / 'bad.duckdb').exists()
    ingest_table(source, tmp_path / 'good.duckdb', mapping, profile='event_log', assume_utc=True)


def test_duplicate_columns_and_empty_files_rejected(tmp_path):
    file = tmp_path / 'bad.csv'; file.write_text('a,a\n1,2\n')
    with pytest.raises(ValueError): inspect_table(file)
    file.write_text('a,b\n')
    with pytest.raises(ValueError): inspect_table(file)


def test_existing_database_and_source_are_preserved(sales, tmp_path):
    target = tmp_path / 'data.duckdb'; original = sales.read_bytes()
    ingest_table(sales, target, {})
    with pytest.raises(ValueError, match='preserved'): ingest_table(sales, target, {})
    assert sales.read_bytes() == original
