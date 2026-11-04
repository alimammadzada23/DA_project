"""Format-neutral event sequence analysis, sharing the active context and evidence."""
from .profile import date_expression


def analyze_events(engine,entity,activity):
    if not engine.context.date_basis: raise ValueError('Review an event timestamp and select it as the shared date basis.')
    key,event=engine.column(entity),engine.column(activity)
    timestamp=engine.column(engine.context.date_basis)
    if engine.count()>50_000: raise ValueError('Event sequence analysis is bounded to 50,000 selected rows. Narrow the shared filters first.')
    source=f'''SELECT _row_id,CAST({key} AS VARCHAR) entity,CAST({event} AS VARCHAR) activity,
        CASE WHEN {date_expression(engine.context.date_basis)} IS NOT NULL THEN try_cast({timestamp} AS TIMESTAMPTZ) END ts FROM selected'''
    coverage=engine.run(f'''SELECT count(*) AS records,count(entity) AS entity_present,count(activity) AS activity_present,
        count(ts) AS timestamp_valid,count(DISTINCT entity) AS entities FROM ({source}) s''')
    transitions=engine.run(f'''SELECT activity AS from_activity,next_activity AS to_activity,count(*) AS occurrences,
        median(date_diff('second',ts,next_ts))/3600.0 AS median_elapsed_hours FROM
        (SELECT *,lead(activity) OVER (PARTITION BY entity ORDER BY ts,_row_id) next_activity,
            lead(ts) OVER (PARTITION BY entity ORDER BY ts,_row_id) next_ts
         FROM ({source}) s WHERE entity IS NOT NULL AND activity IS NOT NULL AND ts IS NOT NULL) ordered
        WHERE next_activity IS NOT NULL GROUP BY 1,2 ORDER BY occurrences DESC LIMIT 100''',
        method='Direct-follow event transitions. Timestamp ties use stable source row order, which is not a verified process order. Durations are elapsed UTC hours, not business hours.')
    variants=engine.run(f'''SELECT sequence,count(*) AS entities FROM
        (SELECT entity,string_agg(activity,' → ' ORDER BY ts,_row_id) AS sequence
         FROM ({source}) s WHERE entity IS NOT NULL AND activity IS NOT NULL AND ts IS NOT NULL GROUP BY entity)
         GROUP BY sequence ORDER BY entities DESC LIMIT 30''',method='Top 30 observed activity sequences; entities with invalid timestamps have incomplete sequences. This is not conformance checking or verified completion.')
    return {'coverage':coverage,'transitions':transitions,'variants':variants}


def import_xes(workspace,source,prefix):
    """Retain source + parser DB; extract structural cases/events into the catalog."""
    from pathlib import Path
    import re
    import shutil
    import uuid
    from ..ingest import ingest
    from .connectors import import_database, _import_cursor
    from ..datasets import digest
    import duckdb
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,35}',prefix): raise ValueError('Choose a short table prefix starting with a letter.')
    if any(prefix+'_'+n in workspace.tables() for n in ('cases','events')): raise ValueError('Use a new event-log prefix; existing tables are retained.')
    directory=workspace.directory/'event_sources'/uuid.uuid4().hex; directory.mkdir(parents=True)
    source=Path(source); retained=directory/source.name; shutil.copyfile(source,retained)
    database=directory/'parsed.duckdb'
    ingest(retained,database)
    results=[]
    for table in ('cases','events'):
        if table=='cases':
            # The compatibility parser also creates normalized domain columns.
            # Generic case import exposes only structural identity + the complete
            # original attribute dictionary, rather than manufacturing empty fields.
            with duckdb.connect(str(database),read_only=True,config={'enable_external_access':'false'}) as con:
                cursor=con.execute('SELECT case_id,source_attributes FROM cases')
                info=_import_cursor(workspace,cursor,prefix+'_cases',{'origin':'database_extract',
                    'source_database_sha256':digest(database),'source_table':'cases',
                    'projection':'Case identity and complete raw case attributes; no domain mapping applied.'})
        else: info=import_database(workspace,database,table,prefix+'_'+table)
        info['source'].update(original_xes_sha256=digest(retained),original_xes=str(retained.relative_to(workspace.directory)))
        with workspace.connect(write=True) as con: workspace._save_info(con,info['name'],info)
        results.append(info)
    return results
