"""Hand-derived references for the reported ISO date and crowded-donut defects.

None of these records is from the user's unavailable 81,915-row source file.
"""
import csv
from datetime import date
import json
from pathlib import Path

import duckdb
import pytest

from insightpilot.business_charts import donut
from insightpilot.complaints import Complaints, UnusableComplaintDates, _date, ingest_complaints, reconcile_complaints
from insightpilot.datasets import TableFilters, metadata

FIXTURE = Path(__file__).parent / 'fixtures/SYNTHETIC-cfpb-timestamps.csv'


@pytest.mark.parametrize('value,expected', [
    ('2026-09-02T19:36:07.000Z', '2026-09-02'),  # exact user-reported format
    ('2026-09-02T19:36:07Z', '2026-09-02'),
    ('2026-09-02T19:36:07.123456Z', '2026-09-02'),
    ('2026-09-02T19:36:07.000+00:00', '2026-09-02'),
    ('2026-09-02T00:30:00+02:00', '2026-09-01'),
    ('2026-09-02T23:30:00-05:00', '2026-09-03'),
    ('2026-09-02T00:15:00+0530', '2026-09-01'),
    ('2026-09-02T19:36:07', '2026-09-02'),
    ('2026-09-02 19:36:07+00:00', '2026-09-02'),
    (' 2026-09-02T19:36:07.000Z ', '2026-09-02'),
    ('2026-09-02', '2026-09-02'), ('09/02/2026', '2026-09-02'),
    ('2024-02-29T23:00:00Z', '2024-02-29'),
    ('2026-02-30T19:36:07.000Z', None), ('2026-09-02T25:00:00Z', None),
    ('not-a-date', None), ('', None), ('  ', None), (None, None),
])
def test_dates_use_calendar_validation_and_utc_boundaries(value, expected):
    actual = _date(value)
    assert actual == (date.fromisoformat(expected) if expected else None)


@pytest.fixture
def timestamp_database(tmp_path):
    database = tmp_path / 'timestamps.duckdb'
    ingest_complaints(FIXTURE, database, synthetic=True)
    return database


def test_real_timestamp_format_yields_nonempty_verified_daily_data(timestamp_database):
    service = Complaints(timestamp_database)
    expected = [(date(2026,9,1),2), (date(2026,9,2),1), (date(2026,9,3),1),
                (date(2026,9,4),1), (date(2026,9,5),1), (date(2026,9,6),1)]
    assert service.investigate('daily')['rows'] == expected
    totals, _ = service.overview()
    assert totals == {'complaints': 10, 'companies': 2, 'known_timely': 8, 'timely_yes': 5,
                      'timely_no': 3, 'missing_dates': 3, 'timely_share': .625}
    info = metadata(timestamp_database)
    assert info['date_parser_version'] == 2
    assert info['quality']['missing_received_dates'] == 1
    assert info['quality']['invalid_received_dates'] == 2
    assert info['quality']['missing']['record_date'] == 3
    assert info['quality']['missing_sent_dates'] == 4
    assert info['quality']['invalid_sent_dates'] == 1
    assert info['quality']['source_rows'] == 12 and info['rows'] == 10
    with duckdb.connect(str(timestamp_database), read_only=True) as con:
        assert con.execute("SELECT record_date,date_sent FROM complaints WHERE complaint_id='2'").fetchone() == (date(2026,9,2), date(2026,9,2))
        assert con.execute("SELECT record_date,date_sent FROM complaints WHERE complaint_id='3'").fetchone() == (date(2026,9,1), date(2026,9,2))


def test_date_filters_company_comparisons_and_product_denominators(timestamp_database):
    service = Complaints(timestamp_database)
    assert service.investigate('products')['rows'] == [('Credit',5,.5), ('Loan',5,.5)]
    assert service.investigate('companies')['rows'] == [('SYNTHETIC Alpha',5,5,3,2,.6), ('SYNTHETIC Beta',5,3,2,1,pytest.approx(2/3))]
    selected = Complaints(timestamp_database, TableFilters(start_date=date(2026,9,2), end_date=date(2026,9,4)))
    assert selected.overview()[0]['complaints'] == 3
    assert selected.investigate('products')['rows'] == [('Credit',2,pytest.approx(2/3)), ('Loan',1,pytest.approx(1/3))]
    assert selected.investigate('company_product')['rows'] == [('SYNTHETIC Alpha','Credit',1,1,0),
        ('SYNTHETIC Alpha','Loan',1,1,0), ('SYNTHETIC Beta','Credit',1,1,1)]
    assert {row[0] for row in selected.records()['rows']} == {'2','4','5'}
    assert sum(row[1] for row in selected.investigate('daily')['rows']) == 3
    alpha = Complaints(timestamp_database, TableFilters(companies=('SYNTHETIC Alpha',), start_date=date(2026,9,2), end_date=date(2026,9,4)))
    assert alpha.overview()[0]['complaints'] == 2
    assert alpha.records(dimension='company', value='SYNTHETIC Beta')['rows'] == []
    assert Complaints(timestamp_database, TableFilters(start_date=date(2026,9,1),end_date=date(2026,9,1))).overview()[0]['complaints'] == 2


def test_equal_period_comparisons_are_hand_calculated_after_timestamp_parsing(timestamp_database):
    service = Complaints(timestamp_database); changes = service.issue_changes()
    assert changes['comparison_periods'] == {'earlier_start':date(2026,9,1), 'later_start':date(2026,9,4),
        'end_exclusive':date(2026,9,7), 'days_per_period':3, 'excluded_tail_days':0}
    assert {row[0]: row[1:] for row in changes['rows']} == {'A':(3,0,-1), 'B':(1,1,0), 'C':(0,2,None)}
    selected = Complaints(timestamp_database, TableFilters(start_date=date(2026,9,2),end_date=date(2026,9,6)))
    assert selected.issue_changes()['comparison_periods']['excluded_tail_days'] == 1
    assert {row[0]:row[1:] for row in selected.issue_changes()['rows']} == {'A':(2,0,-1), 'B':(0,1,None), 'C':(0,1,None)}


def test_independent_reader_does_not_reuse_production_parser(timestamp_database, monkeypatch):
    monkeypatch.setattr('insightpilot.complaints._date', lambda value: (_ for _ in ()).throw(AssertionError('Production parser must not run during reconciliation')))
    result = reconcile_complaints(FIXTURE, timestamp_database)
    assert result['passed'] and all(result['checks'].values())
    assert result['independent_daily_counts'] == {'2026-09-01':2,'2026-09-02':1,'2026-09-03':1,'2026-09-04':1,'2026-09-05':1,'2026-09-06':1}


@pytest.mark.parametrize('value', ['not-a-date', '2026-02-30T19:36:07.000Z', ''])
def test_wholly_unusable_received_dates_fail_atomically_with_actionable_error(tmp_path, value):
    source = tmp_path / 'unparseable.csv'
    with source.open('w', newline='') as stream:
        writer = csv.writer(stream); writer.writerow(['Complaint ID','Date received','Product','Issue','Company','Company response to consumer','Timely response?'])
        writer.writerow(['1',value,'Credit','A','SYNTHETIC Alpha','Closed','Yes'])
    target = tmp_path / 'bad.duckdb'
    with pytest.raises(UnusableComplaintDates, match='No usable received dates'):
        ingest_complaints(source, target, synthetic=True)
    assert not target.exists() and source.is_file()


def test_legacy_snapshot_with_all_null_dates_requires_reimport(timestamp_database):
    with duckdb.connect(str(timestamp_database)) as con:
        con.execute('UPDATE complaints SET record_date=NULL')
        info = json.loads(con.execute('SELECT metadata FROM dataset_metadata').fetchone()[0])
        info.pop('date_parser_version'); info['quality']['missing']['record_date'] = 10
        con.execute('UPDATE dataset_metadata SET metadata=?', [json.dumps(info)])
    with pytest.raises(UnusableComplaintDates, match='Reimport your original CSV'):
        Complaints(timestamp_database)


def test_dashboard_dates_and_source_quality_are_disclosed(timestamp_database, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('INSIGHTPILOT_CFPB_DATABASE', str(timestamp_database))
    monkeypatch.delenv('INSIGHTPILOT_DATABASE', raising=False)
    app = AppTest.from_file(str(Path(__file__).parents[1]/'app.py')).run(timeout=30)
    app.selectbox(key='dataset_workspace').select('Customer Experience & Risk Intelligence').run(timeout=30)
    assert not app.exception and app.metric[0].value == '10'
    assert any('1 missing · 2 invalid' in caption.value for caption in app.caption)
    next(box for box in app.checkbox if box.label == 'Filter by record date').check().run(timeout=30)
    next(field for field in app.date_input if field.label == 'Record date · inclusive bounds').set_value((date(2026,9,1),date(2026,9,1))).run(timeout=30)
    assert not app.exception and app.metric[0].value == '2'
    assert any('0 records excluded from date trends' in caption.value for caption in app.caption)


def test_invalid_legacy_snapshot_shows_visible_error_and_keeps_reimport_available(timestamp_database, monkeypatch):
    from streamlit.testing.v1 import AppTest
    with duckdb.connect(str(timestamp_database)) as con:
        info = json.loads(con.execute('SELECT metadata FROM dataset_metadata').fetchone()[0])
        info['quality']['missing']['record_date'] = info['rows']
        con.execute('UPDATE dataset_metadata SET metadata=?', [json.dumps(info)])
        con.execute('UPDATE complaints SET record_date=NULL')
    monkeypatch.setenv('INSIGHTPILOT_CFPB_DATABASE', str(timestamp_database))
    monkeypatch.delenv('INSIGHTPILOT_DATABASE', raising=False)
    app = AppTest.from_file(str(Path(__file__).parents[1]/'app.py')).run(timeout=30)
    app.selectbox(key='dataset_workspace').select('Customer Experience & Risk Intelligence').run(timeout=30)
    assert not app.exception and not app.metric
    assert any('Reimport your original CSV' in error.value for error in app.error)
    assert any(button.label == 'Import complaint CSV' for button in app.button)


def test_donut_groups_small_categories_without_changing_the_denominator(tmp_path):
    source = tmp_path / 'small-categories.csv'
    names = ['Credit reporting or other personal consumer reports', 'Checking or savings account', 'Debt collection',
        'Credit card or prepaid card', 'Mortgage', 'Money transfer service', 'Student loan', 'Vehicle loan', 'Other service']
    counts = [600,180,90,60,50,10,5,3,2]
    with source.open('w', newline='') as stream:
        writer = csv.writer(stream); writer.writerow(['Complaint ID','Date received','Product','Issue','Company','Company response to consumer','Timely response?'])
        number = 0
        for name, count in zip(names, counts):
            for _ in range(count):
                number += 1; writer.writerow([number,'2026-09-02T19:36:07.000Z',name,'A','SYNTHETIC Alpha','Closed','Yes'])
    target = tmp_path / 'mix.duckdb'; ingest_complaints(source, target, synthetic=True)
    result = Complaints(target).investigate('donut')
    assert result['rows'] == list(zip(names[:5],counts[:5])) + [('Other products (combined)',20)]
    figure = donut(result)
    assert sum(figure.data[0].values) == 1000
    assert [pair[1] for pair in figure.data[0].customdata] == [.6,.18,.09,.06,.05,.02]
    assert figure.data[0].textinfo == 'none'
    assert figure.layout.legend.orientation == 'v' and figure.layout.legend.y < 0
    assert figure.layout.margin.b >= 100 and '<br>' in figure.data[0].labels[0]
    # Filtering must recompute shares; it must not retain whole-source percentages.
    selected = Complaints(target, TableFilters(products=(names[1],names[-1])))
    values = selected.investigate('donut')
    assert values['rows'] == [(names[1],180),('Other products (combined)',2)]
    assert [pair[1] for pair in donut(values).data[0].customdata] == [180/182,2/182]
