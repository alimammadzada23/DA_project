"""Large-table imports: row and disk limits, streaming Excel, folders, header-less CSV."""
from collections import namedtuple
from datetime import datetime
import io

import openpyxl
import pytest
from streamlit.testing.v1 import AppTest

from insightpilot.universal import Workspace, disk_space, excel_stream, store
from insightpilot.universal.engine import Engine
from insightpilot.universal.excel_stream import ALL_SHEETS

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
