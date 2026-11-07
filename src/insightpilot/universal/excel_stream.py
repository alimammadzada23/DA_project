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
        raise ValueError(f'Sheet {sheet!r} is empty.')
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
