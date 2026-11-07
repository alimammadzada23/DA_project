"""Check free disk space before an import starts copying anything."""
from pathlib import Path
import shutil

from .excel_stream import expanded_bytes

GIB = 1024 ** 3
# Room for DuckDB spill files and everything else on this Mac during the import.
SAFETY_MARGIN_BYTES = 2 * GIB
# Parquet is compressed, so its table can be larger than the file. Text formats
# shrink or stay about the same once stored in DuckDB.
TABLE_SIZE_FACTOR = {'.parquet': 3.0}


def _table_estimate(path):
    if path.suffix.lower() == '.xlsx':
        return expanded_bytes(path)
    return int(path.stat().st_size * TABLE_SIZE_FACTOR.get(path.suffix.lower(), 1.0))


def size_text(value):
    if value >= GIB / 10:
        return f'{value / GIB:,.1f} GB'
    return f'{value / 1024 ** 2:,.0f} MB' if value >= 1024 ** 2 else f'{max(1, round(value / 1024)):,} KB'


def ensure_free_space(directory, sources):
    """Raise a clear error unless the retained copy, table and margin all fit."""
    sources = [Path(source) for source in sources]
    copies = sum(source.stat().st_size for source in sources)
    table = sum(_table_estimate(source) for source in sources)
    required = copies + table + SAFETY_MARGIN_BYTES
    free = shutil.disk_usage(directory).free
    if free < required:
        raise ValueError(
            f'Not enough free disk space for this import: it needs about {size_text(required)} '
            f'({size_text(copies)} source copy, {size_text(table)} table, {size_text(SAFETY_MARGIN_BYTES)} working margin) '
            f'and {size_text(free)} is free. Free up space and try again. Nothing was imported or copied.')
    return {'required_bytes': required, 'free_bytes': free}
