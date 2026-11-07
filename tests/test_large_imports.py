"""Large-table imports: row and disk limits, streaming Excel, folders, header-less CSV."""
from collections import namedtuple
from datetime import datetime
import hashlib
import io

import duckdb
import openpyxl
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from insightpilot.universal import Workspace, disk_space, excel_stream, store
from insightpilot.universal.engine import Engine
from insightpilot.universal.excel_stream import ALL_SHEETS
from insightpilot.universal.file_readers import parse_column_names, scan_source
from insightpilot.universal.projects import copy_workspace

Usage = namedtuple('Usage', 'total used free')


@pytest.fixture
def workspace(tmp_path):
    return Workspace.create(tmp_path / 'workspace')


def write_csv(path, rows=3):
    path.write_text('x,y\n' + ''.join(f'{i},v{i}\n' for i in range(rows)))
    return path


def test_row_limit_is_100_million():
    assert store.MAX_ROWS == 100_000_000


def test_files_over_the_row_limit_are_rejected_not_sampled(workspace, tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'MAX_ROWS', 3)
    assert workspace.add_file(write_csv(tmp_path / 'fits.csv', 3), 'fits')['rows'] == 3
    with pytest.raises(ValueError, match='no silent sampling'):
        workspace.add_file(write_csv(tmp_path / 'over.csv', 4), 'over')
    assert set(workspace.tables()) == {'fits'}


def test_short_disk_is_refused_before_anything_is_copied(workspace, tmp_path, monkeypatch):
    source = write_csv(tmp_path / 'data.csv')
    monkeypatch.setattr(disk_space.shutil, 'disk_usage', lambda _: Usage(10 ** 12, 10 ** 12, 1024 ** 3))
    with pytest.raises(ValueError, match=r'Not enough free disk space.*1\.0 GB is free.*Nothing was imported'):
        workspace.add_file(source, 'data')
    assert workspace.tables() == {}
    assert not (workspace.directory / 'sources').exists()


def test_disk_estimate_counts_copy_table_and_margin(tmp_path):
    csv = tmp_path / 'a.csv'; csv.write_bytes(b'x' * 1000)
    parquet = tmp_path / 'a.parquet'; parquet.write_bytes(b'x' * 1000)
    assert disk_space.ensure_free_space(tmp_path, [csv])['required_bytes'] == 2000 + disk_space.SAFETY_MARGIN_BYTES
    assert disk_space.ensure_free_space(tmp_path, [parquet])['required_bytes'] == 4000 + disk_space.SAFETY_MARGIN_BYTES


def test_the_old_512_mib_file_cap_is_gone():
    assert not hasattr(store, 'MAX_BYTES')


def write_workbook(path, sheets):
    workbook = openpyxl.Workbook(write_only=True)
    for title, rows in sheets.items():
        sheet = workbook.create_sheet(title)
        for row in rows:
            sheet.append(row)
    workbook.save(path)
    return path


RETAIL = {
    'Year 2009-2010': [['Invoice', 'Quantity', 'Price'], ['A1', 1, 2.5], ['A2', 2, 3.0], ['SHARED', 5, 1.5], ['SHARED', 5, 1.5]],
    'Year 2010-2011': [['Invoice', 'Quantity', 'Price'], ['SHARED', 5, 1.5], ['B1', 7, 9.25], ['B1', 7, 9.25]],
}


def rows_of(workspace, name, *columns):
    with workspace.connect() as con:
        return con.execute(f'SELECT {",".join(columns)} FROM "{workspace.physical(name)}" ORDER BY _row_id').fetchall()


def test_sheet_names_read_only_the_workbook_index(tmp_path):
    path = write_workbook(tmp_path / 'book.xlsx', RETAIL)
    assert excel_stream.sheet_names(path) == ['Year 2009-2010', 'Year 2010-2011']
    assert excel_stream.sheet_names(io.BytesIO(path.read_bytes())) == ['Year 2009-2010', 'Year 2010-2011']
    bad = tmp_path / 'bad.xlsx'; bad.write_text('not a workbook')
    with pytest.raises(ValueError, match='not a readable'):
        excel_stream.sheet_names(bad)


@pytest.mark.parametrize('choice,expected', [(0, 4), ('Year 2010-2011', 3), (1, 3)])
def test_import_one_chosen_sheet(workspace, tmp_path, choice, expected):
    info = workspace.add_file(write_workbook(tmp_path / 'book.xlsx', RETAIL), 'orders', sheet=choice)
    assert info['rows'] == expected and info['columns'] == ['Invoice', 'Quantity', 'Price']
    assert 'sheet_overlap' not in info


def test_unknown_sheet_names_the_available_ones(workspace, tmp_path):
    with pytest.raises(ValueError, match='Year 2009-2010, Year 2010-2011'):
        workspace.add_file(write_workbook(tmp_path / 'book.xlsx', RETAIL), 'orders', sheet='Nope')
    assert workspace.tables() == {}


def test_all_sheets_keep_every_row_and_report_overlap(workspace, tmp_path):
    info = workspace.add_file(write_workbook(tmp_path / 'book.xlsx', RETAIL), 'orders', sheet=ALL_SHEETS)
    assert info['rows'] == 7 and info['columns'] == ['sheet', 'Invoice', 'Quantity', 'Price']
    assert info['sheets'] == [{'name': 'Year 2009-2010', 'rows': 4}, {'name': 'Year 2010-2011', 'rows': 3}]
    overlap = info['sheet_overlap']
    assert overlap['distinct_rows_in_several_sheets'] == 1
    assert overlap['rows_involved'] == 3 and overlap['extra_copies'] == 2
    assert overlap['duplicates_within_one_sheet'] == 1  # B1 repeats inside one sheet only
    assert overlap['examples'][0]['Invoice'] == 'SHARED'
    assert overlap['examples'][0]['sheets'] == ['Year 2009-2010', 'Year 2010-2011']
    assert any('Sheets overlap' in warning and 'All rows were kept' in warning for warning in info['import_warnings'])
    rows = rows_of(workspace, 'orders', '_row_id', 'sheet', 'Invoice')
    assert [row[0] for row in rows] == list(range(1, 8))
    assert [row[2] for row in rows] == ['A1', 'A2', 'SHARED', 'SHARED', 'SHARED', 'B1', 'B1']
    assert Engine(workspace, 'orders').count() == 7


def test_all_sheets_without_overlap_does_not_warn(workspace, tmp_path):
    sheets = {'A': [['x'], [1]], 'B': [['x'], [2]]}
    info = workspace.add_file(write_workbook(tmp_path / 'book.xlsx', sheets), 'orders', sheet=ALL_SHEETS)
    assert info['sheet_overlap']['distinct_rows_in_several_sheets'] == 0 and not info['import_warnings']


def test_all_sheets_align_columns_by_name(workspace, tmp_path):
    sheets = {'A': [['x', 'y'], [1, 'a']], 'B': [['y', 'x'], ['b', 2]]}
    workspace.add_file(write_workbook(tmp_path / 'book.xlsx', sheets), 'orders', sheet=ALL_SHEETS)
    assert rows_of(workspace, 'orders', 'x', 'y') == [('1', 'a'), ('2', 'b')]


def test_all_sheets_refuse_different_columns_atomically(workspace, tmp_path):
    sheets = {'A': [['x'], [1]], 'B': [['z'], [2]]}
    with pytest.raises(ValueError, match='different columns'):
        workspace.add_file(write_workbook(tmp_path / 'book.xlsx', sheets), 'orders', sheet=ALL_SHEETS)
    assert workspace.tables() == {}
    with workspace.connect() as con:
        assert [row[0] for row in con.execute('SHOW TABLES').fetchall() if row[0].startswith('t_')] == []


def test_all_sheets_refuse_an_existing_sheet_column(workspace, tmp_path):
    with pytest.raises(ValueError, match='already has a column named "sheet"'):
        workspace.add_file(write_workbook(tmp_path / 'book.xlsx', {'A': [['Sheet'], [1]]}), 'orders', sheet=ALL_SHEETS)


def test_empty_sheets_are_reported_not_fatal_in_all_sheets(workspace, tmp_path):
    info = workspace.add_file(write_workbook(tmp_path / 'book.xlsx', {'A': [['x'], [1]], 'Notes': []}), 'orders', sheet=ALL_SHEETS)
    assert info['rows'] == 1 and any('Notes' in warning for warning in info['import_warnings'])
    with pytest.raises(ValueError, match='empty'):
        workspace.add_file(tmp_path / 'book.xlsx', 'notes', sheet='Notes')


def test_cell_text_matches_the_previous_importer(workspace, tmp_path):
    when = datetime(2010, 12, 1, 8, 26)
    rows = [['id', 'count', 'whole', 'price', 'when', 'flag', 'blank'], ['001', 6, 6.0, 2.95, when, True, '   ']]
    workspace.add_file(write_workbook(tmp_path / 'book.xlsx', {'A': rows}), 'typed')
    assert rows_of(workspace, 'typed', 'id', 'count', 'whole', 'price', '"when"', 'flag', 'blank') == [
        ('001', '6', '6', '2.95', '2010-12-01 08:26:00', 'True', None)]


def test_blank_and_repeated_headers_are_named_and_reported(workspace, tmp_path):
    rows = [['a', 'a', None, 'b'], [1, 2, 3, 4]]
    info = workspace.add_file(write_workbook(tmp_path / 'book.xlsx', {'A': rows}), 'named')
    assert info['columns'] == ['a', 'a.1', 'Unnamed: 2', 'b']
    assert any('adjusted' in warning for warning in info['import_warnings'])


def test_values_beyond_the_header_are_never_dropped(workspace, tmp_path):
    with pytest.raises(ValueError, match='row 2 has values beyond the last named column'):
        workspace.add_file(write_workbook(tmp_path / 'book.xlsx', {'A': [['a'], [1, 2]]}), 'wide')
    assert workspace.tables() == {}


def test_empty_rows_are_counted_when_skipped(workspace, tmp_path):
    rows = [['a', 'b'], [1, 2], [None, None], [3, 4]]
    info = workspace.add_file(write_workbook(tmp_path / 'book.xlsx', {'A': rows}), 'gaps')
    assert info['rows'] == 2 and any('1 completely empty rows' in warning for warning in info['import_warnings'])


def test_workbooks_over_500k_rows_and_250_mib_limits_are_gone(workspace, tmp_path, monkeypatch):
    # Batches across a boundary keep every row, and the old pandas path is never used.
    monkeypatch.setattr(excel_stream, 'BATCH_ROWS', 2)
    monkeypatch.setattr(excel_stream.read_sheet, '__defaults__', (2,))
    monkeypatch.setattr('insightpilot.datasets.read_table', lambda *_: pytest.fail('pandas reader used'))
    rows = [['a'], *([index] for index in range(9))]
    assert workspace.add_file(write_workbook(tmp_path / 'book.xlsx', {'A': rows}), 'many')['rows'] == 9
    assert [row[0] for row in rows_of(workspace, 'many', 'a')] == [str(index) for index in range(9)]


def test_excel_row_limit_is_enforced(workspace, tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'MAX_ROWS', 2)
    with pytest.raises(ValueError, match='no silent sampling'):
        workspace.add_file(write_workbook(tmp_path / 'book.xlsx', {'A': [['a'], [1], [2], [3]]}), 'many')
    assert workspace.tables() == {}


def test_suspicious_expansion_is_refused(tmp_path, monkeypatch):
    path = write_workbook(tmp_path / 'book.xlsx', {'A': [['a'], [1]]})
    monkeypatch.setattr(excel_stream, 'EXPANSION_CHECK_FROM_BYTES', 1)
    monkeypatch.setattr(excel_stream, 'MAX_EXPANSION_RATIO', 0.001)
    with pytest.raises(ValueError, match='looks unsafe'):
        excel_stream.expanded_bytes(path)


def test_overlap_is_shown_on_the_dataset_page(workspace, tmp_path):
    workspace.add_file(write_workbook(tmp_path / 'book.xlsx', RETAIL), 'orders', sheet=ALL_SHEETS)

    def page(directory):
        from insightpilot.universal import Workspace
        from insightpilot.universal.ui_workflow import dataset_overview
        dataset_overview(Workspace(directory), 'orders')

    app = AppTest.from_function(page, args=(str(workspace.directory),)).run(timeout=30)
    assert not app.exception
    assert any('Sheets overlap' in item.value for item in app.warning)
    assert [item.value for item in app.metric] == ['1', '3', '2']
    assert any('Nothing was removed' in item.value and 'repeats that occur inside a single sheet' in item.value for item in app.caption)


LAND_REGISTRY = ['transaction_id', 'price', 'date', 'postcode', 'property_type', 'new_build', 'duration', 'paon', 'saon',
                 'street', 'locality', 'town', 'district', 'county', 'category', 'status']


def test_land_registry_style_csv_without_a_header(workspace, tmp_path):
    path = tmp_path / 'pp-2023.csv'
    path.write_bytes(b'"{A1B2-1}","250000","2023-01-05 00:00","AB1 2CD","T","N","F","12","","HIGH STREET, EAST","","LEEDS","LEEDS","WEST YORKSHIRE","A","A"\r\n'
                     b'"{A1B2-2}","99000","2023-02-07 00:00","","F","Y","L","FLAT 1","","","","YORK","YORK","YORK","B","D"\r\n')
    info = workspace.add_file(path, 'prices', column_names=LAND_REGISTRY)
    assert info['rows'] == 2 and info['columns'] == LAND_REGISTRY
    assert rows_of(workspace, 'prices', 'transaction_id', 'price', 'street', 'postcode') == [
        ('{A1B2-1}', '250000', 'HIGH STREET, EAST', 'AB1 2CD'), ('{A1B2-2}', '99000', None, None)]
    assert info['versions'][0]['operation']['supplied_column_names'] == LAND_REGISTRY


def test_headerless_values_keep_their_text_and_first_row(workspace, tmp_path):
    path = tmp_path / 'data.tsv'; path.write_text('001\tx\n002\ty\n')
    workspace.add_file(path, 'coded', column_names=['code', 'label'])
    assert rows_of(workspace, 'coded', 'code', 'label') == [('001', 'x'), ('002', 'y')]


@pytest.mark.parametrize('names,message', [(['a'], '2 columns on its first row, but 1 column names'),
                                           (['a', 'A'], 'unique and nonempty'), (['a', ' '], 'unique and nonempty'),
                                           (['x', 'y'], 'has a header row')])
def test_supplied_column_names_are_checked(workspace, tmp_path, names, message):
    path = tmp_path / 'data.csv'; path.write_text('x,y\n1,2\n')
    with pytest.raises(ValueError, match=message):
        workspace.add_file(path, 'bad', column_names=names)
    assert workspace.tables() == {}


def test_a_later_row_with_extra_columns_is_rejected(workspace, tmp_path):
    path = tmp_path / 'data.csv'; path.write_text('1,2\n3,4,5\n')
    with pytest.raises((ValueError, duckdb.Error)):
        workspace.add_file(path, 'bad', column_names=['a', 'b'])
    assert workspace.tables() == {}


def test_missing_header_error_points_to_the_option(workspace, tmp_path):
    path = tmp_path / 'data.csv'; path.write_text('A,A\n1,2\n')
    with pytest.raises(ValueError, match='no-header option'):
        workspace.add_file(path, 'bad')


def test_column_names_are_only_for_delimited_files(workspace, tmp_path):
    path = tmp_path / 'data.json'; path.write_text('[{"a":1}]')
    with pytest.raises(ValueError, match='only be supplied for CSV'):
        workspace.add_file(path, 'bad', column_names=['a'])


@pytest.mark.parametrize('text,names', [('a, b,c', ['a', 'b', 'c']), ('"x, y",z', ['x, y', 'z']), ('a,\nb', ['a', 'b'])])
def test_typed_column_names_are_parsed(text, names):
    assert parse_column_names(text) == names


def write_parquet(path, frame):
    frame.to_parquet(path, index=False)
    return path


@pytest.fixture
def folder(tmp_path):
    directory = tmp_path / 'trips'; directory.mkdir()
    return directory


def test_scan_lists_a_folder_in_natural_order_and_skips_other_files(folder):
    for name in ('m10.parquet', 'm2.parquet', 'm1.parquet', 'notes.txt', '.hidden.parquet', '~$open.xlsx'):
        (folder / name).write_bytes(b'x')
    (folder / 'nested').mkdir(); (folder / 'nested' / 'm3.parquet').write_bytes(b'x')
    assert [path.name for path in scan_source(folder)] == ['m1.parquet', 'm2.parquet', 'm10.parquet']


def test_scan_explains_bad_paths(tmp_path):
    with pytest.raises(ValueError, match='Nothing was found'):
        scan_source(tmp_path / 'missing')
    (tmp_path / 'a.txt').write_text('x')
    with pytest.raises(ValueError, match='not a supported type'):
        scan_source(tmp_path / 'a.txt')
    with pytest.raises(ValueError, match='no supported files'):
        scan_source(tmp_path)


def test_folder_of_parquet_becomes_one_table_with_provenance(workspace, folder):
    paths = [write_parquet(folder / f'm{month}.parquet', pd.DataFrame({'fare': [month * 1.5, month * 2.5], 'zone': ['a', None]}))
             for month in (1, 2, 3)]
    before = [path.read_bytes() for path in paths]
    info = workspace.add_files(scan_source(folder), 'trips')
    assert info['rows'] == 6 and info['columns'] == ['source_file', 'fare', 'zone']
    rows = rows_of(workspace, 'trips', '_row_id', 'source_file', 'fare', 'zone')
    assert [row[0] for row in rows] == list(range(1, 7))
    assert [row[1] for row in rows] == ['m1.parquet'] * 2 + ['m2.parquet'] * 2 + ['m3.parquet'] * 2
    assert rows[5][2:] == (7.5, None)
    source = info['source']
    assert source['filename'] == 'trips (3 files)' and source['bytes'] == sum(map(len, before))
    assert [item['filename'] for item in source['files']] == ['m1.parquet', 'm2.parquet', 'm3.parquet']
    for item, original in zip(source['files'], before):
        retained = workspace.directory / item['retained']
        assert retained.read_bytes() == original and item['sha256'] == hashlib.sha256(original).hexdigest() and item['bytes'] == len(original)
    assert [path.read_bytes() for path in paths] == before
    assert Engine(workspace, 'trips').run('SELECT source_file,sum(fare) FROM selected GROUP BY 1 ORDER BY 1')['rows'][0] == ('m1.parquet', 4.0)


def test_folder_import_hash_depends_on_contents(workspace, folder, tmp_path):
    write_csv(folder / 'a.csv', 2); write_csv(folder / 'b.csv', 3)
    first = workspace.add_files(scan_source(folder), 'one')['source']['sha256']
    assert first == workspace.add_files(scan_source(folder), 'two')['source']['sha256']
    write_csv(folder / 'b.csv', 4)
    assert first != workspace.add_files(scan_source(folder), 'three')['source']['sha256']


def test_monthly_type_and_capitalization_drift_is_widened_and_reported(workspace, folder):
    write_parquet(folder / 'm1.parquet', pd.DataFrame({'passengers': [1, 2], 'Airport_fee': [1.25, 0.0]}))
    write_parquet(folder / 'm2.parquet', pd.DataFrame({'airport_fee': [2.5], 'passengers': [1.5]}))
    info = workspace.add_files(scan_source(folder), 'trips')
    assert info['columns'] == ['source_file', 'passengers', 'Airport_fee']
    assert rows_of(workspace, 'trips', 'passengers', 'Airport_fee') == [(1.0, 1.25), (2.0, 0.0), (1.5, 2.5)]
    assert any('widened' in warning and 'passengers' in warning for warning in info['import_warnings'])


def test_mismatched_files_are_refused_atomically_with_a_receipt(workspace, folder):
    (folder / 'a.csv').write_text('x,y\n1,2\n'); (folder / 'b.csv').write_text('x,z\n1,2\n')
    with pytest.raises(ValueError, match=r'b\.csv has different columns from a\.csv \(missing y; extra z\)'):
        workspace.add_files(scan_source(folder), 'mixed')
    assert workspace.tables() == {}
    assert list((workspace.directory / 'sources').glob('*.rejected.json'))


def test_a_bad_row_in_a_later_file_rejects_the_whole_import(workspace, folder):
    (folder / 'a.csv').write_text('x,y\n1,2\n'); (folder / 'b.csv').write_text('x,y\n1,2,3\n')
    with pytest.raises((ValueError, duckdb.Error)):
        workspace.add_files(scan_source(folder), 'bad')
    assert workspace.tables() == {}
    with workspace.connect() as con:
        assert not [row for row in con.execute('SHOW TABLES').fetchall() if row[0].startswith('t_')]


def test_folder_row_limit_is_enforced_across_files(workspace, folder, monkeypatch):
    monkeypatch.setattr(store, 'MAX_ROWS', 4)
    write_csv(folder / 'a.csv', 3); write_csv(folder / 'b.csv', 3)
    with pytest.raises(ValueError, match='no silent sampling'):
        workspace.add_files(scan_source(folder), 'over')
    assert workspace.tables() == {}


def test_exactly_the_row_limit_is_allowed(workspace, folder, monkeypatch):
    monkeypatch.setattr(store, 'MAX_ROWS', 6)
    write_csv(folder / 'a.csv', 3); write_csv(folder / 'b.csv', 3)
    assert workspace.add_files(scan_source(folder), 'fits')['rows'] == 6


def test_folder_disk_check_counts_all_files(workspace, folder, monkeypatch):
    write_csv(folder / 'a.csv'); write_csv(folder / 'b.csv')
    monkeypatch.setattr(disk_space.shutil, 'disk_usage', lambda _: Usage(1, 1, 1))
    with pytest.raises(ValueError, match='Not enough free disk space'):
        workspace.add_files(scan_source(folder), 'short')
    assert not (workspace.directory / 'sources').exists()


def test_headerless_folder_of_csv(workspace, folder):
    (folder / 'pp-2022.csv').write_text('"{1}","100"\n'); (folder / 'pp-2023.csv').write_text('"{2}","200"\n')
    info = workspace.add_files(scan_source(folder), 'prices', column_names=['id', 'price'])
    assert rows_of(workspace, 'prices', 'source_file', 'id', 'price') == [('pp-2022.csv', '{1}', '100'), ('pp-2023.csv', '{2}', '200')]
    assert info['columns'] == ['source_file', 'id', 'price']


def test_identical_files_are_kept_and_reported(workspace, folder):
    write_csv(folder / 'a.csv'); write_csv(folder / 'copy.csv')
    info = workspace.add_files(scan_source(folder), 'twice')
    assert info['rows'] == 6 and any('identical contents' in warning and 'copy.csv' in warning for warning in info['import_warnings'])


def test_multi_file_import_refusals(workspace, folder):
    a = write_csv(folder / 'a.csv'); b = folder / 'b.tsv'; b.write_text('x\ty\n1\t2\n')
    with pytest.raises(ValueError, match='share a type'):
        workspace.add_files([a, b], 'mixed')
    with pytest.raises(ValueError, match='at least two'):
        workspace.add_files([a], 'one')
    c = folder / 'c.xlsx'; c.write_bytes(b'x'); d = folder / 'd.xlsx'; d.write_bytes(b'x')
    with pytest.raises(ValueError, match='one at a time'):
        workspace.add_files([c, d], 'books')
    e = folder / 'e.csv'; e.write_text('source_file,y\n1,2\n'); f = folder / 'f.csv'; f.write_text('source_file,y\n1,2\n')
    with pytest.raises(ValueError, match='already have a column named "source_file"'):
        workspace.add_files([e, f], 'taken')


def test_large_project_snapshot_keeps_multi_file_sources(workspace, folder, tmp_path):
    write_csv(folder / 'a.csv'); write_csv(folder / 'b.csv')
    info = workspace.add_files(scan_source(folder), 'both')
    copy = copy_workspace(workspace, tmp_path / 'copy')
    assert all((copy.directory / item['retained']).is_file() for item in info['source']['files'])
    assert copy.info('both')['rows'] == 6


def path_page(directory):
    import streamlit as st
    from insightpilot.universal import Workspace
    from insightpilot.universal.ui_data import _import_datasets
    workspace = Workspace(directory)
    for level, message in st.session_state.get('messages', []):
        getattr(st, level)(message)
    _import_datasets(workspace, 'messages')
    st.write('Tables: ' + ','.join(sorted(workspace.tables())))


def path_app(workspace, location):
    app = AppTest.from_function(path_page, args=(str(workspace.directory),)).run(timeout=30)
    field = next(item for item in app.text_input if item.label == 'File or folder path')
    return field.set_value(str(location)).run(timeout=30)


def click_import(app):
    return next(item for item in app.button if item.label == 'Import from this Mac').click().run(timeout=30)


def test_path_tab_imports_a_folder(workspace, folder):
    write_csv(folder / 'a.csv'); write_csv(folder / 'b.csv')
    app = click_import(path_app(workspace, folder))
    assert not app.exception
    assert workspace.info('trips')['rows'] == 6
    assert any('trips: 6 rows imported' in item.value for item in app.success)


def test_path_tab_offers_sheets_for_a_workbook(workspace, tmp_path):
    app = path_app(workspace, write_workbook(tmp_path / 'book.xlsx', RETAIL))
    picker = next(item for item in app.selectbox if item.label.startswith('Sheet to import'))
    assert picker.options[:2] == list(RETAIL) and picker.options[2].startswith('All 2 sheets')
    picker.select(ALL_SHEETS).run(timeout=30)
    app = click_import(app)
    assert not app.exception and workspace.info('book')['rows'] == 7


def test_path_tab_supplies_column_names(workspace, tmp_path):
    path = tmp_path / 'prices.csv'; path.write_text('1,x\n2,y\n')
    app = path_app(workspace, path)
    next(item for item in app.checkbox if item.label.startswith('These CSV/TSV files')).check().run(timeout=30)
    app = click_import(app)
    assert any('Enter the column names' in item.value for item in app.warning) and not workspace.tables()
    next(item for item in app.text_area if item.label.startswith('Column names')).set_value('id, label').run(timeout=30)
    app = click_import(app)
    assert not app.exception
    assert rows_of(workspace, 'prices', 'id', 'label') == [('1', 'x'), ('2', 'y')]


def test_path_tab_explains_a_bad_path_and_failed_import(workspace, folder):
    app = path_app(workspace, folder / 'missing')
    assert any('Nothing was found' in item.value for item in app.warning)
    (folder / 'a.csv').write_text('x\n1\n'); (folder / 'b.csv').write_text('y\n1\n')
    app = click_import(path_app(workspace, folder))
    assert any('different columns' in item.value and 'No partial table' in item.value for item in app.error)
    assert not workspace.tables()
