"""Local, isolated datasets: inspect first, explicitly map, then materialize.

Source files remain immutable. Uploaded values never become SQL identifiers
without quoting; analytical connections use the existing restricted SQL runner.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import uuid
import zipfile

import duckdb
import pandas as pd

from .engine_settings import resources

MAX_ROWS = 500_000
MAX_BYTES = 100 * 1024 * 1024
ROLES = ('date', 'category', 'product', 'customer', 'measure', 'quantity', 'unit_price',
         'company', 'issue', 'response', 'timely', 'case_id', 'activity', 'resource')


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def read_table(path, sheet=0):
    """Bounded local upload readers. No macros, URLs or formulas are executed."""
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('Upload exceeds the 100 MiB limit. Create a smaller extract.')
    suffix = path.suffix.lower()
    if suffix == '.csv':
        # Preserve literal NA/NULL and IDs, rather than guessing business semantics.
        import csv
        with path.open(encoding='utf-8-sig', newline='') as stream:
            header = next(csv.reader(stream), [])
        if not header or len(set(header)) != len(header):
            raise ValueError('CSV needs unique, nonempty column names.')
        frame = pd.read_csv(path, dtype=str, keep_default_na=False, nrows=MAX_ROWS + 1, encoding='utf-8-sig')
    elif suffix == '.xlsx':
        with zipfile.ZipFile(path) as archive:
            if sum(entry.file_size for entry in archive.infolist()) > 250 * 1024 * 1024:
                raise ValueError('Expanded Excel workbook exceeds 250 MiB.')
        frame = pd.read_excel(path, sheet_name=sheet, engine='openpyxl', dtype=str, keep_default_na=False, nrows=MAX_ROWS + 1)
    elif suffix == '.parquet':
        with duckdb.connect(config=resources()) as con:
            frame = con.execute('SELECT * FROM read_parquet(?) LIMIT ?', [str(path.resolve()), MAX_ROWS + 1]).fetchdf()
            # Nested objects require a deliberate flattening workflow.
            types = con.execute('DESCRIBE SELECT * FROM read_parquet(?)', [str(path.resolve())]).fetchall()
            if any(any(t in row[1] for t in ('STRUCT', 'MAP', '[]')) for row in types):
                raise ValueError('Flatten nested Parquet fields before importing.')
    else:
        raise ValueError('Choose UTF-8 CSV, .xlsx Excel or flat Parquet; XES has a separate importer.')
    if len(frame) > MAX_ROWS:
        raise ValueError(f'Upload exceeds {MAX_ROWS:,} rows. Create a bounded extract; no rows were silently sampled.')
    if frame.empty or len(frame.columns) > 150:
        raise ValueError('A dataset needs 1–150 columns and at least one record.')
    frame.columns = [str(name) for name in frame.columns]
    if len(set(frame.columns)) != len(frame.columns) or any(not c.strip() for c in frame.columns):
        raise ValueError('Use unique, nonempty column names.')
    return frame.map(lambda value: None if pd.isna(value) or not str(value).strip() else str(value))


def date_values(series, day_first=False):
    # Only documented ISO or explicit slash-date convention, never locale guessing.
    def parse(value):
        if value is None:
            return None
        text = str(value).strip()
        try:
            if re.match(r'^\d{4}-\d{2}-\d{2}(?:$|[ T])', text):
                return datetime.fromisoformat(text.replace('Z', '+00:00')).date()
            return datetime.strptime(text, '%d/%m/%Y' if day_first else '%m/%d/%Y').date()
        except (ValueError, OverflowError):
            return None
    return series.map(parse)


def inspect_table(path, sheet=0):
    frame = read_table(path, sheet)
    columns = []
    suggestions = {}
    aliases = {
        'date': ['date', 'order_date', 'date_received', 'timestamp', 'event_time'],
        'category': ['category', 'segment'], 'product': ['product', 'product_name'],
        'customer': ['customer', 'customer_id'], 'measure': ['revenue', 'sales', 'amount', 'value'],
        'quantity': ['quantity', 'qty'], 'unit_price': ['unit_price', 'price'],
        'company': ['company'], 'issue': ['issue'], 'response': ['company_response', 'response'],
        'timely': ['timely_response', 'timely'], 'case_id': ['case_id', 'case', 'case_identifier'],
        'activity': ['activity', 'event'], 'resource': ['resource', 'department'],
    }
    for name in frame.columns:
        series = frame[name]
        numeric = pd.to_numeric(series, errors='coerce')
        parsed = date_values(series)
        present = int(series.notna().sum())
        columns.append({'column': name, 'records': len(frame), 'missing': int(series.isna().sum()),
                        'distinct': int(series.nunique()), 'numeric_values': int(numeric.notna().sum()),
                        'recognized_dates': int(parsed.notna().sum()),
                        'inferred_type': 'numeric' if present and numeric.notna().sum() == present else
                                         'date' if present and parsed.notna().sum() == present else 'text'})
        normalized = re.sub(r'\W+', '_', name.lower()).strip('_')
        for role, names in aliases.items():
            if normalized in names and role not in suggestions:
                suggestions[role] = name
    return {'rows': len(frame), 'columns': columns, 'sample': frame.head(8).to_dict('records'),
            'exact_duplicate_rows': int(frame.duplicated().sum()), 'suggested_mapping': suggestions,
            'source_sha256': digest(path), 'sheet': sheet,
            'notice': 'Suggestions are unapproved. Duplicate rows may be legitimate; none are dropped.'}


def validate_mapping(frame, mapping, profile, *, revenue=False):
    if set(mapping) - set(ROLES) or any(v not in frame.columns for v in mapping.values()):
        raise ValueError('Mapping references an unknown role or column.')
    if len(set(mapping.values())) != len(mapping):
        raise ValueError('Map each column to a single role.')
    if profile not in ('descriptive', 'transactions', 'event_log', 'complaints'):
        raise ValueError('Unsupported analytical profile.')
    if profile == 'event_log' and not {'case_id', 'activity', 'date'} <= set(mapping):
        raise ValueError('Process analysis needs approved case ID, activity and timestamp fields.')
    if profile == 'complaints' and not {'company', 'product', 'issue', 'date'} <= set(mapping):
        raise ValueError('Complaint analysis needs company, product, issue and date roles; each row must represent one complaint.')
    if revenue and profile != 'transactions':
        raise ValueError('Revenue semantics require the transactional profile.')
    if revenue and not ('measure' in mapping or {'quantity', 'unit_price'} <= set(mapping)):
        raise ValueError('Revenue needs a confirmed revenue amount or quantity and unit price.')


def write_metadata(con, metadata):
    con.execute('CREATE TABLE dataset_metadata (metadata JSON)')
    con.execute('INSERT INTO dataset_metadata VALUES (?)', [json.dumps(metadata, default=str)])


def metadata(database):
    from .agent import SQL_CONFIG
    with duckdb.connect(str(Path(database).resolve()), read_only=True, config=SQL_CONFIG) as con:
        return json.loads(con.execute('SELECT metadata FROM dataset_metadata').fetchone()[0])


def ingest_table(source, database, mapping, *, profile='descriptive', title='My business dataset',
                 revenue=False, day_first=False, sheet=0, synthetic=False, assume_utc=False):
    source, database = Path(source), Path(database)
    if database.exists():
        raise ValueError('Use a new dataset database; existing workspaces are preserved.')
    frame = read_table(source, sheet)
    validate_mapping(frame, mapping, profile, revenue=revenue)
    report = inspect_table(source, sheet)
    dates = date_values(frame[mapping['date']], day_first) if 'date' in mapping else pd.Series([None] * len(frame))
    report['mapped_invalid_dates'] = int((frame[mapping['date']].notna() & dates.isna()).sum()) if 'date' in mapping else None
    report['numeric_exclusions'] = {}
    records = pd.DataFrame({'record_id': range(1, len(frame) + 1), 'record_date': dates})
    for role in ROLES:
        if role == 'date':
            continue
        if role in ('measure', 'quantity', 'unit_price'):
            values = pd.to_numeric(frame[mapping[role]], errors='coerce') if role in mapping else pd.Series([None] * len(frame), dtype='float64')
            finite = values.notna() & ~values.isin([float('inf'), float('-inf')])
            records[role] = values.where(finite)
            if role in mapping:
                report['numeric_exclusions'][role] = int((frame[mapping[role]].notna() & ~finite).sum())
        else:
            records[role] = frame[mapping[role]] if role in mapping else None
    if revenue and 'measure' not in mapping:
        records['measure'] = records.quantity * records.unit_price
        records['measure'] = records['measure'].where(~records['measure'].isin([float('inf'), float('-inf')]))
    capabilities = ['records', 'quality']
    if 'date' in mapping: capabilities.append('trends')
    if any(r in mapping for r in ('category', 'product', 'company')): capabilities.append('groups')
    if 'measure' in mapping or revenue: capabilities.append('numeric_distribution')
    if revenue: capabilities.append('revenue')
    if 'customer' in mapping: capabilities.append('customer_segments')
    info = {'dataset_id': str(uuid.uuid4()), 'title': title[:120], 'module': 'custom', 'profile': profile,
            'origin': 'synthetic' if synthetic else 'user_upload', 'synthetic': synthetic,
            'source_name': source.name, 'source_sha256': digest(source), 'downloaded_at': None,
            'imported_at': datetime.now(timezone.utc).isoformat(), 'rows': len(frame), 'mapping': mapping,
            'date_convention': 'day/month/year' if day_first else 'month/day/year; ISO accepted',
            'revenue_confirmed': revenue, 'capabilities': capabilities, 'quality': report,
            'usage': 'User supplies provenance, rights and field definitions. No source authentication is implied.'}
    database.parent.mkdir(parents=True, exist_ok=True)
    temporary = database.with_name(database.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        if profile == 'event_log':
            from .ingest import ingest
            from xml.etree.ElementTree import Element, SubElement, ElementTree
            root = Element('log')
            for case, group in frame.groupby(mapping['case_id'], sort=False, dropna=False):
                if case is None or pd.isna(case): raise ValueError('Event log contains a missing case identifier.')
                trace = SubElement(root, 'trace'); SubElement(trace, 'string', key='concept:name', value=str(case))
                for _, row in group.iterrows():
                    activity, timestamp = row[mapping['activity']], row[mapping['date']]
                    if not activity or not timestamp: raise ValueError('Event log has missing activity/timestamp; correct the mapping or source.')
                    try: instant = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    except ValueError as exc: raise ValueError('Event timestamps need ISO 8601 format.') from exc
                    if instant.tzinfo is None:
                        if not assume_utc: raise ValueError('Offset-free event timestamps require explicit UTC confirmation.')
                        instant = instant.replace(tzinfo=timezone.utc)
                    event = SubElement(trace, 'event')
                    SubElement(event, 'string', key='concept:name', value=activity)
                    SubElement(event, 'date', key='time:timestamp', value=instant.isoformat())
                    if 'resource' in mapping and row[mapping['resource']]: SubElement(event, 'string', key='org:resource', value=row[mapping['resource']])
            derived = database.parent / ('mapped-' + uuid.uuid4().hex + '.xes')
            ElementTree(root).write(derived, encoding='utf-8', xml_declaration=True)
            ingest(derived, temporary, synthetic=synthetic)
            info['derived_event_log'] = str(derived)
            info['analytical_source_sha256'] = digest(derived)
            info['capabilities'] = ['records', 'quality', 'process_variants', 'spans', 'repeated_activities', 'waiting_intervals']
        if profile == 'complaints':
            from .complaints import ingest_complaints
            converted = pd.DataFrame({'Complaint ID': records.record_id, 'Date received': dates.map(lambda value: value.isoformat() if value else ''),
                'Product': records['product'], 'Issue': records.issue, 'Company': records.company,
                'Company response to consumer': records.response, 'Timely response?': records.timely})
            derived = database.parent / ('mapped-' + uuid.uuid4().hex + '.csv'); converted.to_csv(derived, index=False)
            complaint_info = ingest_complaints(derived, temporary, synthetic=synthetic)
            info['coverage'] = complaint_info['coverage']; info['quality']['complaint_validation'] = complaint_info['quality']
            info['analytical_source_sha256'] = digest(derived)
            info['grain'] = 'One uploaded row per complaint, explicitly chosen by the user; technical row IDs do not authenticate complaint identities.'
            info['capabilities'] = ['records', 'quality', 'trends', 'company_comparison', 'issue_concentration', 'reported_timeliness']
        with duckdb.connect(str(temporary), config=resources()) as con:
            if profile == 'complaints': con.execute('DROP TABLE dataset_metadata')
            con.register('_raw', frame); con.execute('CREATE TABLE raw_data AS SELECT * FROM _raw')
            con.register('_records', records)
            casts = ','.join(f'{quote(column)} AS {quote(column)}' if column == 'record_id' else
                f'CAST({quote(column)} AS {"DATE" if column == "record_date" else "DOUBLE" if column in ("measure", "quantity", "unit_price") else "VARCHAR"}) AS {quote(column)}' for column in records.columns)
            con.execute('CREATE TABLE records AS SELECT ' + casts + ' FROM _records')
            if profile not in ('event_log', 'complaints'):
                con.execute('CREATE TABLE provenance (sha256 VARCHAR, synthetic BOOLEAN)')
                con.execute('INSERT INTO provenance VALUES (?,?)', [info['source_sha256'], synthetic])
            write_metadata(con, info)
        temporary.replace(database)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return info


@dataclass(frozen=True)
class TableFilters:
    start_date: object = None
    end_date: object = None  # inclusive UI date; connector coverage is explicitly half-open
    companies: tuple = ()
    products: tuple = ()
    issues: tuple = ()
    states: tuple = ()
    responses: tuple = ()
    timely: tuple = ()
    categories: tuple = ()


def predicate(filters, columns):
    if filters.start_date and filters.end_date and filters.start_date > filters.end_date:
        raise ValueError('Start date must precede end date.')
    clauses, parameters = [], []
    for key, column in columns.items():
        values = getattr(filters, key)
        if not values: continue
        known = [value for value in values if value is not None]
        parts = []
        if known:
            parts.append(f'{quote(column)} IN ({",".join("?" for _ in known)})'); parameters.extend(known)
        if None in values: parts.append(f'{quote(column)} IS NULL')
        clauses.append('(' + ' OR '.join(parts) + ')')
    if filters.start_date:
        clauses.append('record_date >= CAST(? AS DATE)'); parameters.append(filters.start_date)
    if filters.end_date:
        clauses.append('record_date <= CAST(? AS DATE)'); parameters.append(filters.end_date)
    return ' AND '.join(clauses) or 'TRUE', parameters


class ScopedAnalytics:
    table = 'records'
    dimensions = {'companies': 'company', 'products': 'product', 'categories': 'category'}
    catalog = {}
    limitations = 'Descriptive associations only. Uploaded field definitions and population coverage require user review.'

    def __init__(self, database, filters=TableFilters()):
        self.database, self.filters = Path(database), filters
        self.metadata = metadata(database)

    def run(self, sql, parameters=(), limit=1000):
        from .agent import Analyst
        where, values = predicate(self.filters, self.dimensions)
        if re.match(r'^\s*WITH\b', sql, re.IGNORECASE): sql = 'SELECT * FROM (' + sql + ') AS investigation'
        result = Analyst(self.database, limitations=self.limitations, domain=self.metadata['module'],
                         data_tables={self.table}).query(f'WITH selected AS (SELECT * FROM {self.table} WHERE {where}) ' + sql,
                                                       limit=limit, parameters=[*values, *parameters])
        result.update(filters=asdict(self.filters), dataset_id=self.metadata['dataset_id'])
        return result

    def investigate(self, question_id):
        if question_id not in self.catalog: raise ValueError('Choose a supported analytical question.')
        title, sql = self.catalog[question_id]
        return self.run(sql) | {'question': title, 'mode': 'Guided SQL analyst; no LLM used'}

    def options(self):
        # Options use this dataset alone, before filtering. Bounded cardinality is disclosed.
        service = self.__class__(self.database)
        result = {}
        for name, column in self.dimensions.items():
            evidence = service.run(f'SELECT {quote(column)} AS value, count(*) AS records FROM selected GROUP BY 1 ORDER BY records DESC, value NULLS LAST LIMIT 1001')
            result[name] = {'values': [row[0] for row in evidence['rows'][:1000]], 'truncated': evidence['truncated']}
        bounds = service.run('SELECT min(record_date), max(record_date) FROM selected')['rows'][0]
        result['dates'] = bounds
        return result

    def records(self, page=0, *, dimension=None, value=None):
        if not 0 <= page <= 100_000: raise ValueError('Invalid page.')
        clause, values = '', []
        if dimension:
            if dimension not in self.dimensions.values(): raise ValueError('Unknown drill-down dimension.')
            clause = f' WHERE {quote(dimension)} IS NOT DISTINCT FROM ?'; values.append(value)
        return self.run(f'SELECT * FROM selected{clause} ORDER BY record_date DESC NULLS LAST, record_id LIMIT 100 OFFSET ?', [*values, page * 100], limit=100)
