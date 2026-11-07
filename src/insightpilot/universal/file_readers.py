"""DuckDB source expressions for retained files, with their checks.

Text formats stay all-VARCHAR so identifiers keep their leading zeros until reviewed.
"""
import csv
from pathlib import Path
import re

from .import_preview import parsing_preview

DELIMITERS = (',', '\t', ';', '|')
JSON_SUFFIXES = ('.json', '.jsonl', '.ndjson')
SUPPORTED_SUFFIXES = ('.csv', '.tsv', '.parquet', '.xlsx', *JSON_SUFFIXES)


def parse_column_names(text):
    """Column names typed by the user, one comma-separated line."""
    rows = list(csv.reader([' '.join(text.split('\n'))], skipinitialspace=True))
    return [name.strip() for name in rows[0]] if rows else []


def check_column_names(names):
    if not names or any(not name.strip() for name in names) or len({name.casefold() for name in names}) != len(names):
        raise ValueError('Column names must be unique and nonempty (capitalization is ignored).')
    return names


def _first_row(path, delimiter):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return next(csv.reader(stream, delimiter=delimiter), [])


def delimited_columns(path, delimiter, column_names, warnings):
    """The file's column names, from its header row or from the names the user supplied."""
    first = _first_row(path, delimiter)
    if column_names is not None:
        check_column_names(column_names)
        if len(first) != len(column_names):
            raise ValueError(f'This file has {len(first)} columns on its first row, '
                             f'but {len(column_names)} column names were given.')
        if [value.strip().casefold() for value in first] == [name.casefold() for name in column_names]:
            raise ValueError('The first row already contains these column names, so this file has a header row. '
                             'Turn off the no-header option so that row is not imported as data.')
        return list(column_names)
    if len(first) == 1:
        with path.open('rb') as stream:
            preview = parsing_preview(stream.read(65536), delimiter)
        if preview['warning']:
            warnings.append(preview['warning'])
    if not first or any(not name.strip() for name in first) or len({name.casefold() for name in first}) != len(first):
        raise ValueError('Delimited files need unique nonempty column names (case insensitive). '
                         'If this file has no header row, turn on the no-header option and supply the names.')
    return first


def delimited_reader(path, delimiter, column_names=None):
    options = 'all_varchar=true,delim=?,strict_mode=true,null_padding=false,ignore_errors=false,parallel=false'
    if column_names is None:
        return f'read_csv(?,header=true,{options})', [str(path), delimiter]
    entries = ','.join("'" + name.replace("'", "''") + "':'VARCHAR'" for name in column_names)
    return (f'''read_csv(?,header=false,columns={{{entries}}},quote='"',escape='"',delim=?,strict_mode=true,
        null_padding=false,ignore_errors=false,parallel=false)''', [str(path), delimiter])


def file_reader(path, delimiter=None, column_names=None):
    """(FROM expression, parameters) for one retained non-Excel file."""
    suffix = path.suffix.lower()
    if suffix in ('.csv', '.tsv'):
        return delimited_reader(path, delimiter, column_names)
    if suffix == '.parquet':
        return 'read_parquet(?)', [str(path)]
    if suffix in JSON_SUFFIXES:
        # Flat structures only. DuckDB strict parsing preserves failures atomically.
        return ('read_json_auto(?,format=?,maximum_object_size=16777216,ignore_errors=false)',
                [str(path), 'newline_delimited' if suffix != '.json' else 'array'])
    raise ValueError('Supported files: CSV, TSV, XLSX, flat Parquet, JSON array and JSON Lines.')


def normalize_delimiter(path, delimiter):
    if path.suffix.lower() not in ('.csv', '.tsv'):
        return delimiter
    delimiter = delimiter or ('\t' if path.suffix.lower() == '.tsv' else ',')
    if delimiter not in DELIMITERS:
        raise ValueError('Choose comma, tab, semicolon or pipe delimiter.')
    return delimiter



def _natural_key(path):
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r'(\d+)', path.name)]


def scan_source(location):
    """Importable files at a path: the file itself, or a folder's top-level files in name order."""
    path = Path(str(location).strip()).expanduser()
    if not path.exists():
        raise ValueError(f'Nothing was found at {path}. Check the path; you can drag a file or folder from Finder into Terminal to copy its path.')
    if path.is_file():
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise ValueError(f'{path.name} is not a supported type. Use: {", ".join(SUPPORTED_SUFFIXES)}.')
        return [path.resolve()]
    found = sorted((item.resolve() for item in path.iterdir() if item.is_file() and item.suffix.lower() in SUPPORTED_SUFFIXES
                    and not item.name.startswith(('.', '~$'))), key=_natural_key)
    if not found:
        raise ValueError(f'{path} has no supported files at its top level. Use: {", ".join(SUPPORTED_SUFFIXES)}.')
    return found


def _difference_text(reference, other):
    missing = sorted(set(reference) - set(other))
    extra = sorted(set(other) - set(reference))
    return '; '.join(filter(None, ['missing ' + ', '.join(missing) if missing else '', 'extra ' + ', '.join(extra) if extra else '']))


def combined_columns(con, files, delimiter, column_names, warnings):
    """[(name, type)] shared by all files, as (display name, retained path) pairs.

    Names must match, ignoring capitalization and order. Compatible type differences
    are common in monthly exports, so they widen to a common type and are reported.
    """
    per_file = []
    for label, path in files:
        if path.suffix.lower() in ('.csv', '.tsv'):
            per_file.append((label, [(name, 'VARCHAR') for name in delimited_columns(path, delimiter, column_names, warnings)]))
        else:
            reader, parameters = file_reader(path)
            per_file.append((label, [row[:2] for row in con.execute(f'DESCRIBE SELECT * FROM {reader}', parameters).fetchall()]))
    first_label, first = per_file[0]
    reference = {name.casefold(): name for name, _ in first}
    for label, columns in per_file[1:]:
        names = [name.casefold() for name, _ in columns]
        if set(names) != set(reference):
            raise ValueError(f'{label} has different columns from {first_label} ({_difference_text(reference, names)}). '
                             'Only files with the same columns can share a table.')
    if {kind for _, columns in per_file for _, kind in columns} == {'VARCHAR'}:
        return first
    # DuckDB's own union picks the narrowest type that holds every file's values.
    paths = [str(path) for _, path in files]
    if files[0][1].suffix.lower() == '.parquet':
        unified = con.execute('DESCRIBE SELECT * FROM read_parquet(?,union_by_name=true)', [paths]).fetchall()
    else:
        unified = con.execute('DESCRIBE SELECT * FROM read_json_auto(?,format=?,union_by_name=true,maximum_object_size=16777216)',
                              [paths, 'newline_delimited' if files[0][1].suffix.lower() != '.json' else 'array']).fetchall()
    common = {row[0].casefold(): row[1] for row in unified}
    changed = []
    for name, _ in first:
        seen = {dict((n.casefold(), k) for n, k in columns)[name.casefold()] for _, columns in per_file}
        if len(seen) > 1:
            changed.append(f'{name} ({" / ".join(sorted(seen))} → {common[name.casefold()]})')
    if changed:
        warnings.append('Column types differ between files and were widened so no value is lost: ' + '; '.join(changed) + '.')
    return [(name, common[name.casefold()]) for name, _ in first]
