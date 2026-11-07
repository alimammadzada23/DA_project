"""Stream .xlsx sheets in batches, so workbook size does not decide memory use.

No macros, URLs or formulas are executed. Every value stays text, as it does for
CSV: formats such as leading zeros survive until the user reviews the field.
"""
from pathlib import Path
import zipfile

from defusedxml.ElementTree import fromstring
import openpyxl
import pyarrow as pa

from ..engine_settings import physical_memory

ALL_SHEETS = '*'  # Excel forbids this character in sheet names, so it cannot collide.
BATCH_ROWS = 50_000
# Real workbooks compress about 5–50 times. A far higher ratio means a zip bomb.
MAX_EXPANSION_RATIO = 1000
EXPANSION_CHECK_FROM_BYTES = 1024 ** 3


class EmptySheet(ValueError):
    pass


def expanded_bytes(path):
    """Uncompressed size of the workbook, after rejecting unsafe archives."""
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
    except zipfile.BadZipFile as exc:
        raise ValueError('This file is not a readable .xlsx workbook.') from exc
    total = sum(entry.file_size for entry in entries)
    compressed = max(1, path.stat().st_size)
    if total > EXPANSION_CHECK_FROM_BYTES and total / compressed > MAX_EXPANSION_RATIO:
        raise ValueError(f'This workbook expands {total / compressed:,.0f} times, which looks unsafe. It was not imported.')
    # openpyxl keeps the shared-string table in memory, even in read-only mode.
    strings = sum(entry.file_size for entry in entries if entry.filename.lower().endswith('sharedstrings.xml'))
    if strings > physical_memory() / 2:
        raise ValueError(f'This workbook has {strings / 1024 ** 3:.1f} GiB of shared text, too much for this Mac\'s '
                         'memory. Export it to CSV and import that instead.')
    return total


def _open(path):
    expanded_bytes(path)
    return openpyxl.load_workbook(path, read_only=True, data_only=True)


def sheet_names(source):
    """Sheet names in workbook order. Reads only the workbook index, so it is instant."""
    try:
        with zipfile.ZipFile(source) as archive:
            root = fromstring(archive.read('xl/workbook.xml'))
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ValueError('This file is not a readable .xlsx workbook.') from exc
    return [element.get('name') for element in root.iter() if element.tag.rsplit('}', 1)[-1] == 'sheet']


def cell_text(value):
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value)
    return text if text.strip() else None


def _column_names(cells, sheet):
    """First nonempty row is the header: blanks get a position name, repeats a suffix."""
    names, seen, renamed = [], {}, []
    while cells and cells[-1] is None:
        cells = cells[:-1]
    for index, cell in enumerate(cells):
        base = cell if cell is not None else f'Unnamed: {index}'
        count = seen.get(base, 0)
        seen[base] = count + 1
        name = base if not count else f'{base}.{count}'
        if name != cell:
            renamed.append(f'{cell if cell is not None else "(blank)"} → {name}')
        names.append(name)
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError(f'Sheet {sheet!r} has column names that differ only by capitalization. Rename them first.')
    return names, renamed


def read_sheet(workbook, sheet, batch_rows=BATCH_ROWS):
    """Yield the header summary, then Arrow batches of text columns in sheet order."""
    worksheet = workbook[sheet]
    # Some exporters store a wrong sheet size; never let it truncate rows.
    worksheet.reset_dimensions()
    rows = worksheet.iter_rows(values_only=True)
    names, skipped = None, 0
    for number, row in enumerate(rows, 1):
        cells = [cell_text(value) for value in row]
        if any(cell is not None for cell in cells):
            names, renamed = _column_names(cells, sheet)
            break
    if names is None:
        raise EmptySheet(f'Sheet {sheet!r} is empty.')
    width = len(names)
    yield {'columns': names, 'renamed': renamed}
    columns = [[] for _ in names]
    count = 0
    for number, row in enumerate(rows, number + 1):
        cells = [cell_text(value) for value in row]
        if not any(cell is not None for cell in cells):
            skipped += 1
            continue
        if any(cell is not None for cell in cells[width:]):
            raise ValueError(f'Sheet {sheet!r} row {number:,} has values beyond the last named column. '
                             'Add a header for them so no value is lost.')
        for index in range(width):
            columns[index].append(cells[index] if index < len(cells) else None)
        count += 1
        if count == batch_rows:
            yield pa.table({name: pa.array(values, pa.string()) for name, values in zip(names, columns)})
            columns = [[] for _ in names]
            count = 0
    if count:
        yield pa.table({name: pa.array(values, pa.string()) for name, values in zip(names, columns)})
    yield {'empty_rows_skipped': skipped}


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _selected_sheets(workbook, sheet):
    available = list(workbook.sheetnames)
    if sheet == ALL_SHEETS:
        return available
    if isinstance(sheet, int):
        if not 0 <= sheet < len(available):
            raise ValueError(f'This workbook has {len(available)} sheet(s); sheet number {sheet + 1} does not exist.')
        return [available[sheet]]
    if sheet not in available:
        raise ValueError(f'Sheet {sheet!r} was not found. This workbook has: {", ".join(available)}.')
    return [sheet]


def _check_reserved(columns, combining):
    lowered = {name.casefold() for name in columns}
    if any(name.startswith('_row_id') for name in lowered):
        raise ValueError('_row_id is reserved for stable source-row references.')
    if combining and 'sheet' in lowered:
        raise ValueError('This workbook already has a column named "sheet", which all-sheets import adds. '
                         'Rename that column, or import one sheet at a time.')


def load_table(con, path, table, sheet=0, progress=None):
    """Create `table` from one sheet, or all sheets with a `sheet` column. Nothing is deduplicated."""
    workbook = _open(path)
    combining = sheet == ALL_SHEETS
    try:
        names = _selected_sheets(workbook, sheet)
        columns, written, warnings, sheets = None, 0, [], []
        for current in names:
            reader = read_sheet(workbook, current)
            try:
                head = next(reader)
            except EmptySheet:
                if not combining:
                    raise
                warnings.append(f'Sheet {current!r} is empty and added no rows.')
                continue
            if columns is None:
                columns = head['columns']
                _check_reserved(columns, combining)
                definitions = ['_row_id BIGINT', *(['sheet VARCHAR'] if combining else []),
                               *(_quote(name) + ' VARCHAR' for name in columns)]
                con.execute(f'CREATE TABLE {_quote(table)} ({",".join(definitions)})')
            elif set(head['columns']) != set(columns):
                difference = sorted(set(head['columns']) ^ set(columns))
                raise ValueError(f'Sheet {current!r} has different columns from {sheets[0]["name"]!r} '
                                 f'({", ".join(difference)}). Import these sheets separately.')
            if head['renamed']:
                warnings.append(f'Sheet {current!r} column names were adjusted: ' + ', '.join(head['renamed']) + '.')
            selection = ','.join(['_row_id', *(['?'] if combining else []), *map(_quote, columns)])
            parameters = [current] if combining else []
            first = written
            for item in reader:
                if isinstance(item, dict):
                    if item['empty_rows_skipped']:
                        warnings.append(f'Sheet {current!r}: {item["empty_rows_skipped"]:,} completely empty rows '
                                        'were not imported.')
                    continue
                ids = pa.array(range(written + 1, written + item.num_rows + 1), pa.int64())
                con.register('_excel_batch', item.add_column(0, '_row_id', ids))
                con.execute(f'INSERT INTO {_quote(table)} SELECT {selection} FROM _excel_batch', parameters)
                con.unregister('_excel_batch')
                written += item.num_rows
                if progress:
                    progress(f'{current}: {written:,} rows read')
            sheets.append({'name': current, 'rows': written - first})
        if columns is None:
            raise ValueError('Every sheet in this workbook is empty.')
    finally:
        workbook.close()
    overlap = sheet_overlap(con, table, columns) if combining and len(sheets) > 1 else None
    if overlap and overlap['distinct_rows_in_several_sheets']:
        warnings.append(
            f'Sheets overlap: {overlap["distinct_rows_in_several_sheets"]:,} different rows appear in more than one '
            f'sheet, accounting for {overlap["rows_involved"]:,} imported rows ({overlap["extra_copies"]:,} more than '
            'one copy of each). All rows were kept, so totals count them more than once until you decide what to do.')
    return {'sheets': sheets, 'warnings': warnings, 'overlap': overlap}


def sheet_overlap(con, table, columns, examples=20):
    """Exact counts of identical rows across sheets (a row's own sheet is not compared)."""
    fields = ','.join(map(_quote, columns))
    con.execute(f'''CREATE TEMP TABLE _sheet_rows AS SELECT {fields},count(*) AS copies,
        count(DISTINCT sheet) AS sheet_count,list(DISTINCT sheet ORDER BY sheet) AS sheets
        FROM {_quote(table)} GROUP BY ALL''')
    try:
        several, involved, extra, within = con.execute('''SELECT count(*) FILTER (WHERE sheet_count>1),
            coalesce(sum(copies) FILTER (WHERE sheet_count>1),0),
            coalesce(sum(copies-1) FILTER (WHERE sheet_count>1),0),
            coalesce(sum(copies-1) FILTER (WHERE sheet_count=1),0) FROM _sheet_rows''').fetchone()
        cursor = con.execute(f'''SELECT sheets,copies,{fields} FROM _sheet_rows
            WHERE sheet_count>1 ORDER BY {fields} LIMIT {int(examples)}''')
        labels = [item[0] for item in cursor.description]
        rows = [dict(zip(labels, row)) for row in cursor.fetchall()]
    finally:
        con.execute('DROP TABLE _sheet_rows')
    return {'distinct_rows_in_several_sheets': int(several), 'rows_involved': int(involved), 'extra_copies': int(extra),
            'duplicates_within_one_sheet': int(within), 'examples': rows}
