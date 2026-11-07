"""Streaming, atomic XES ingestion. Unknown source attributes remain available."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone

import duckdb
import pandas as pd
from defusedxml.ElementTree import iterparse
from .engine_settings import resources

EXPECTED = {"cases": 251734, "events": 1595923, "purchase_documents": 76349,
            "activities": 42, "users": 627}
# Attribute names confirmed in the official TU/e description; validate the original file on import.
FIELDS = {"purchase_document": "Purchasing Document", "item": "Item",
          "matching_category": "Item Category", "vendor": "Vendor",
          "spend_classification": "Spend classification text"}


def prepare_analytics(con):
    """Materialize derived outputs once; raw events/cases remain unchanged."""
    prepared=[]
    for name in ['case_metrics','transitions','variants','transition_performance','category_performance','sequence_signals']:
        kind=con.execute('SELECT table_type FROM information_schema.tables WHERE table_schema=\'main\' AND table_name=?',[name]).fetchone()
        if kind is None: raise ValueError(f'Missing analytical object: {name}')
        if kind[0]!='VIEW': continue
        temporary='_prepared_'+name
        con.execute(f'CREATE TABLE {temporary} AS SELECT * FROM {name}')
        con.execute(f'DROP VIEW {name}')
        con.execute(f'ALTER TABLE {temporary} RENAME TO {name}')
        prepared.append(name)
    return prepared


def tag(element):
    return element.tag.rsplit('}', 1)[-1]


def attributes(element):
    result = {}
    for child in element:
        if 'key' in child.attrib:
            if len(child):
                raise ValueError('Nested XES attributes require an explicit mapping; source is preserved')
            key = child.attrib['key']
            if key in result:
                raise ValueError(f'Duplicate XES attribute key: {key}')
            result[key] = child.attrib.get('value')
    return result


def timestamp(value):
    if value is None:
        return None
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Timestamp must have an explicit timezone')
    return dt.astimezone(timezone.utc)


def ingest(source, target, *, synthetic=False, mapping=None, batch_size=10000):
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target or target.exists():
        raise ValueError('Target must be a new file distinct from source')
    if batch_size < 1:
        raise ValueError('batch_size must be positive')
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with source.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    fields = FIELDS | (mapping or {})
    fd, temporary = tempfile.mkstemp(suffix='.duckdb', dir=target.parent)
    os.close(fd)
    Path(temporary).unlink()
    con = None
    try:
        con = duckdb.connect(temporary)
        con.execute("SET TimeZone='UTC'")
        for setting,value in resources().items():con.execute(f"SET {setting}='{value}'")
        con.execute('BEGIN TRANSACTION')
        con.execute(Path(__file__).with_name('model.sql').read_text())
        con.execute('CREATE TABLE provenance(source_path VARCHAR, sha256 VARCHAR, synthetic BOOLEAN, mapping JSON, imported_at TIMESTAMPTZ)')
        con.execute('INSERT INTO provenance VALUES (?,?,?,?,current_timestamp)',
                    [str(source), digest.hexdigest(), synthetic, json.dumps(fields)])
        opener = gzip.open if source.suffix == '.gz' else open
        batch, case_batch = [], []
        trace_defaults, event_defaults = {}, {}
        def flush_events():
            if batch:
                con.append('events',pd.DataFrame.from_records(batch,columns=[
                    'case_id','source_order','activity','event_time','resource','lifecycle','source_attributes']))
                batch.clear()
        def flush_cases():
            if case_batch:
                con.append('cases',pd.DataFrame.from_records(case_batch,columns=[
                    'case_id',*FIELDS,'source_attributes']))
                case_batch.clear()
        with opener(source, 'rb') as stream:
            root = None
            for action, element in iterparse(stream, events=('start','end')):
                if root is None:
                    root = element
                    if tag(root) != 'log':
                        raise ValueError('Expected XES log root')
                if action == 'end' and tag(element) == 'global':
                    defaults = attributes(element)
                    if element.attrib.get('scope') == 'trace': trace_defaults = defaults
                    if element.attrib.get('scope') == 'event': event_defaults = defaults
                if action != 'end' or tag(element) != 'trace':
                    continue
                attrs = trace_defaults | attributes(element)
                case_id = attrs.get('concept:name')
                if not case_id or not case_id.strip():
                    raise ValueError('Missing case identifier; import rejected without dropping records')
                case_batch.append([case_id] + [attrs.get(fields[k]) for k in FIELDS] + [json.dumps(attrs)])
                if len(case_batch) >= batch_size: flush_cases()
                count = 0
                for event in element:
                    if tag(event) != 'event':
                        continue
                    a = event_defaults | attributes(event)
                    activity = a.get('concept:name')
                    resource = a.get('org:resource')
                    if resource and resource.strip().upper()=='NONE': resource=None
                    batch.append((case_id, count, activity if activity and activity.strip() else None, timestamp(a.get('time:timestamp')),
                                  resource if resource and resource.strip() else None, a.get('lifecycle:transition'), json.dumps(a)))
                    count += 1
                    if len(batch) >= batch_size:
                        flush_events()
                if not count:
                    raise ValueError(f'Empty trace: {case_id}')
                root.remove(element)
                element.clear()
        flush_events()
        flush_cases()
        if con.execute('SELECT count(*) FROM cases').fetchone()[0] == 0:
            raise ValueError('No traces in source')
        prepare_analytics(con)
        con.execute('COMMIT')
        con.execute('CHECKPOINT')
        con.close()
        con = None
        os.replace(temporary, target)
    finally:
        if con is not None:
            con.close()
        for p in (Path(temporary), Path(temporary+'.wal')):
            p.unlink(missing_ok=True)
    return target


def quality(con):
    counts = {}
    for key, query in {
        'cases': 'SELECT count(*) FROM cases', 'events': 'SELECT count(*) FROM events',
        'purchase_documents': 'SELECT count(DISTINCT purchase_document) FROM cases',
        'activities': 'SELECT count(DISTINCT activity) FROM events',
        'users': 'SELECT count(DISTINCT resource) FROM events',
    }.items():
        counts[key] = con.execute(query).fetchone()[0]
    issues = {}
    for key, query in {
        'missing_activities': 'SELECT count(*) FROM events WHERE activity IS NULL OR trim(activity)=\'\'',
        'missing_timestamps': 'SELECT count(*) FROM events WHERE event_time IS NULL',
        'missing_resources': 'SELECT count(*) FROM events WHERE resource IS NULL',
        'missing_purchase_documents': 'SELECT count(*) FROM cases WHERE purchase_document IS NULL',
        'source_order_reversals': '''SELECT count(*) FROM (SELECT event_time,
          lag(event_time) OVER (PARTITION BY case_id ORDER BY source_order) AS prev FROM events) WHERE event_time<prev''',
        'duplicate_signature_excess': '''SELECT coalesce(sum(n-1),0) FROM (SELECT count(*) n FROM events
          GROUP BY case_id,activity,event_time,resource,lifecycle HAVING count(*)>1)''',
        'tied_timestamp_excess': '''SELECT coalesce(sum(n-1),0) FROM (SELECT count(*) n FROM events
          WHERE event_time IS NOT NULL GROUP BY case_id,event_time HAVING count(*)>1)''',
        'outside_review_window': "SELECT count(*) FROM events WHERE event_time<'2000-01-01' OR event_time>current_timestamp",
    }.items():
        issues[key] = con.execute(query).fetchone()[0]
    provenance = con.execute('SELECT sha256,synthetic,mapping FROM provenance').fetchone()
    minimum, maximum = con.execute('SELECT min(event_time),max(event_time) FROM events').fetchone()
    return {'dataset': 'synthetic fixture' if provenance[1] else 'user-supplied, provenance requires source verification',
            'sha256': provenance[0], 'mapping': json.loads(provenance[2]), 'counts': counts,
            'published_count_comparison': {k: {'expected': v, 'actual': counts[k], 'matches': counts[k]==v} for k,v in EXPECTED.items()},
            'issues': issues, 'time_range': [str(minimum),str(maximum)],
            'filters': 'None; invalid IDs/dates reject the import atomically; duplicates retained',
            'completion': 'Unknown; spans are observed intervals, not verified completion times'}
