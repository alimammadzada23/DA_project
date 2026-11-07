"""The one DuckDB resource policy for InsightPilot, sized to the machine it runs on.

Defaults: about 55% of physical RAM, one thread per CPU core, and a private spill
directory so large sorts, joins and aggregations use disk instead of failing.
Spill files live outside project folders so they can never enter a saved snapshot.

Environment overrides (read on every connection):
  INSIGHTPILOT_DUCKDB_MEMORY   e.g. 6GB, 4096MB, 12GiB
  INSIGHTPILOT_DUCKDB_THREADS  a positive integer
  INSIGHTPILOT_DUCKDB_TEMP     directory for spill files
"""
import hashlib
import os
from pathlib import Path
import re
import tempfile

MEMORY_ENV = 'INSIGHTPILOT_DUCKDB_MEMORY'
THREADS_ENV = 'INSIGHTPILOT_DUCKDB_THREADS'
TEMP_ENV = 'INSIGHTPILOT_DUCKDB_TEMP'

MEMORY_SHARE = 0.55
MINIMUM_MEMORY_MB = 1024
FALLBACK_MEMORY_BYTES = 8 * 1024 ** 3

# Query deadlines suit tables up to this size. Larger tables get proportionally
# longer deadlines, because a slow valid answer is better than a timeout.
DEADLINE_BASE_ROWS = 2_000_000
DEADLINE_BASE_SECONDS = 20.0
DEADLINE_MAXIMUM_SECONDS = 900.0


def physical_memory():
    try:
        return os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
    except (AttributeError, OSError, ValueError):
        return FALLBACK_MEMORY_BYTES


def memory_limit():
    configured = os.environ.get(MEMORY_ENV, '').strip()
    if configured:
        if not re.fullmatch(r'\d+(\.\d+)?\s*(MB|GB|MiB|GiB)', configured, re.IGNORECASE):
            raise ValueError(f'{MEMORY_ENV} must look like 6GB or 4096MB; found {configured!r}.')
        return configured.replace(' ', '')
    return f'{max(MINIMUM_MEMORY_MB, int(physical_memory() * MEMORY_SHARE / 1024 ** 2))}MB'


def thread_count():
    configured = os.environ.get(THREADS_ENV, '').strip()
    if configured:
        if not configured.isdigit() or int(configured) < 1:
            raise ValueError(f'{THREADS_ENV} must be a positive whole number; found {configured!r}.')
        return int(configured)
    return os.cpu_count() or 2


def resources():
    """Memory and thread settings, for connections that manage no spill directory."""
    return {'threads': str(thread_count()), 'memory_limit': memory_limit()}


def spill_root():
    configured = os.environ.get(TEMP_ENV, '').strip()
    root = Path(configured).expanduser() if configured else Path(tempfile.gettempdir()) / 'insightpilot-duckdb'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def spill_directory(database):
    """A separate directory per database: DuckDB clears its spill files when it closes."""
    key = hashlib.sha256(str(Path(database).resolve()).encode()).hexdigest()[:24]
    return spill_root() / key


def connection_config(database):
    """Full configuration for a connection to one database file.

    DuckDB refuses `temp_directory` next to `enable_external_access=false`, so a
    restricted connection opens with this config and then calls `restrict`.
    """
    return resources() | {'temp_directory': str(spill_directory(database))}


def restrict(connection):
    """Irreversibly disable file and network access for this database instance."""
    connection.execute('SET enable_external_access=false')


def query_deadline(seconds, rows):
    """Interrupt deadline: the requested budget, scaled up only for large tables."""
    budget = min(DEADLINE_BASE_SECONDS, max(.01, seconds))
    return min(DEADLINE_MAXIMUM_SECONDS, budget * max(1.0, (rows or 0) / DEADLINE_BASE_ROWS))
