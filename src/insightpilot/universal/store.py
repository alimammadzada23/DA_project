"""Local multi-table, versioned analytics store with retained original source bytes.

No user SQL or model output is used for imports or transformations. Files are
never replaced, tables are immutable versions, and switching a version is undo.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import threading
import uuid

import duckdb
import pandas as pd

from ..datasets import digest, quote
from ..engine_settings import connection_config, restrict
from .disk_space import ensure_free_space
from .excel_stream import load_table as load_workbook_table
from .profile import date_expression, numeric_expression, profile

MAX_ROWS = 100_000_000
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def now():
    return datetime.now(timezone.utc).isoformat()


class Workspace:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.database = self.directory / 'workspace.duckdb'

    @classmethod
    def create(cls, directory):
        store = cls(directory)
        if store.database.exists():
            raise ValueError('Workspace already exists; open it instead of replacing it.')
        store.directory.mkdir(parents=True, exist_ok=True)
        with store.connect(write=True) as con:
            con.execute('CREATE TABLE _catalog(name VARCHAR PRIMARY KEY, info JSON)')
            con.execute('CREATE TABLE _metrics(name VARCHAR PRIMARY KEY, definition JSON)')
            con.execute('CREATE TABLE _relationships(name VARCHAR PRIMARY KEY, definition JSON)')
        return store

    @contextmanager
    def connect(self, *, write=False, external=False):
        config = connection_config(self.database) | ({} if external else {'allow_unsigned_extensions':'false'})
        with _LOCKS_GUARD:
            lock=_LOCKS.setdefault(str(self.database),threading.RLock())
        # Streamlit sessions share a process. Serialize connections to avoid
        # read/write configuration collisions; immutable versions remain readable.
        with lock:
            con = duckdb.connect(str(self.database), read_only=not write, config=config)
            try:
                if not external: restrict(con)
                con.execute("SET TimeZone='UTC'")
                yield con
            finally:
                con.close()

    def tables(self):
        with self.connect() as con:
            return {name: json.loads(info) for name, info in con.execute('SELECT name,info FROM _catalog ORDER BY name').fetchall()}

    def info(self, name):
        tables = self.tables()
        if name not in tables:
            raise ValueError('Choose an imported table.')
        return tables[name]

    def columns(self, name):
        return self.info(name)['columns']

    def physical(self, name):
        return self.info(name)['current']

    def _save_info(self, con, name, info):
        con.execute('INSERT OR REPLACE INTO _catalog VALUES (?,?)', [name, json.dumps(info,default=str)])

    def add_file(self, source, name, *, sheet=0, provenance=None, delimiter=None, progress=None):
        source = Path(source).resolve()
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,62}', name):
            raise ValueError('Table names need 1–63 letters, digits or underscores, starting with a letter.')
        if name in self.tables():
            raise ValueError('Use a new table name; existing tables and source files are preserved.')
        if not source.is_file():
            raise ValueError('Choose an existing local file.')
        ensure_free_space(self.directory, [source])
        key = uuid.uuid4().hex
        sources = self.directory / 'sources'; sources.mkdir(exist_ok=True)
        retained = sources / (key + source.suffix.lower())
        shutil.copyfile(source, retained)
        table = 't_' + key
        parse_warnings=[]; workbook={}
        try:
            with self.connect(write=True, external=True) as con:
                con.execute('BEGIN')
                if con.execute('SELECT count(*) FROM _catalog WHERE name=?',[name]).fetchone()[0]:
                    raise ValueError('This table name was imported in another session. Choose a new name.')
                suffix = retained.suffix
                if suffix in ('.csv','.tsv'):
                    import csv
                    delimiter = delimiter or ('\t' if suffix == '.tsv' else ',')
                    if delimiter not in (',','\t',';','|'): raise ValueError('Choose comma, tab, semicolon or pipe delimiter.')
                    with retained.open(encoding='utf-8-sig', newline='') as stream:
                        header = next(csv.reader(stream, delimiter=delimiter), [])
                    if len(header)==1:
                        from .import_preview import parsing_preview
                        with retained.open('rb') as stream: preview=parsing_preview(stream.read(65536),delimiter)
                        if preview['warning']:parse_warnings.append(preview['warning'])
                    if not header or any(not n.strip() for n in header) or len({n.casefold() for n in header}) != len(header):
                        raise ValueError('Delimited files need unique nonempty column names (case insensitive).')
                    con.execute(f'''CREATE TABLE {quote(table)} AS SELECT row_number() OVER () AS _row_id,*
                        FROM read_csv(?,header=true,all_varchar=true,delim=?,strict_mode=true,
                        null_padding=false,ignore_errors=false,parallel=false)''', [str(retained), delimiter])
                elif suffix == '.parquet':
                    con.execute(f'CREATE TABLE {quote(table)} AS SELECT row_number() OVER () AS _row_id,* FROM read_parquet(?)', [str(retained)])
                elif suffix in ('.json','.jsonl','.ndjson'):
                    # Flat structures only. DuckDB strict parsing preserves failures atomically.
                    con.execute(f'''CREATE TABLE {quote(table)} AS SELECT row_number() OVER () AS _row_id,*
                        FROM read_json_auto(?,format=?,maximum_object_size=16777216,ignore_errors=false)''',
                        [str(retained), 'newline_delimited' if suffix != '.json' else 'array'])
                elif suffix == '.xlsx':
                    workbook = load_workbook_table(con, retained, table, sheet, progress)
                    parse_warnings.extend(workbook['warnings'])
                else:
                    raise ValueError('Supported files: CSV, TSV, XLSX, flat Parquet, JSON array and JSON Lines.')
                schema = con.execute(f'DESCRIBE {quote(table)}').fetchall()
                cols = [r[0] for r in schema if r[0] != '_row_id']
                # Reserved source names may otherwise be auto-renamed by DuckDB.
                if any(c.casefold().startswith('_row_id') for c in cols):
                    raise ValueError('_row_id is reserved for stable source-row references.')
                if len(cols) > 150 or any(not c.strip() for c in cols):
                    raise ValueError('Use 1–150 nonempty column names.')
                if any(any(s in r[1] for s in ('STRUCT','MAP','[]')) for r in schema):
                    raise ValueError('Flatten nested fields explicitly before import.')
                count = con.execute(f'SELECT count(*) FROM {quote(table)}').fetchone()[0]
                if not 1 <= count <= MAX_ROWS:
                    raise ValueError(f'Use 1–{MAX_ROWS:,} records; no silent sampling is performed.')
                info = {'name':name, 'current':table, 'columns':cols, 'rows':count, 'roles':{},
                        'grain':'Unconfirmed; review what one row represents.', 'created_at':now(),'import_warnings':parse_warnings,
                        'source':{'origin':'user_upload', **(provenance or {}),
                                  'filename':(provenance or {}).get('filename',source.name),
                                  'retained':str(retained.relative_to(self.directory)),
                                  'sha256':digest(retained),'bytes':retained.stat().st_size},
                        **({'sheets':workbook['sheets']} if workbook else {}),
                        **({'sheet_overlap':workbook['overlap']} if workbook.get('overlap') else {}),
                        'versions':[{'table':table,'parent':None,'operation':{'kind':'import','sheet':sheet,'delimiter':delimiter,'encoding':'UTF-8'},
                                     'rows':count,'created_at':now(),'errors':0}]}
                self._save_info(con, name, info)
                con.execute('COMMIT')
            return info
        except Exception as exc:
            # Keep rejected source bytes plus an explicit receipt; no partial table is committed.
            (sources / (key + '.rejected.json')).write_text(json.dumps({'source':retained.name,
                'sha256':digest(retained),'status':'rejected; no table committed','at':now(),
                'error_type':type(exc).__name__,'parsing_error':str(exc)[:3000]}))
            raise

    def profile(self, name):
        table = self.physical(name)
        with self.connect() as con:
            return profile(con, table)

    def approve_roles(self, name, roles, grain):
        info = self.info(name)
        allowed = {'date','identifier','customer','product','transaction','revenue','expense','quantity',
                   'price','geography','company','category','status','duration','text','measure','unassigned'}
        if set(roles) - set(info['columns']) or set(roles.values()) - allowed:
            raise ValueError('Unknown column or semantic role.')
        if not grain.strip(): raise ValueError('Describe what one row represents.')
        info.update(roles=roles, grain=grain[:500])
        with self.connect(write=True) as con: self._save_info(con,name,info)

    def approve_semantics(self, name, roles, grain, *, units=None, date_purposes=None, date_basis=None):
        """Explicit user review; names never silently establish a date basis or units."""
        info = self.info(name)
        units, date_purposes = units or {}, date_purposes or {}
        if (set(units) | set(date_purposes)) - set(info['columns']):
            raise ValueError('Definitions reference unavailable columns.')
        if date_basis is not None and (date_basis not in info['columns'] or roles.get(date_basis) != 'date'):
            raise ValueError('The date basis needs an approved date role.')
        if date_basis and not str(date_purposes.get(date_basis, '')).strip():
            raise ValueError('Describe what the selected date measures (for example, observation or completion).')
        if not grain.strip() or grain.startswith('Unconfirmed'):
            raise ValueError('Confirm what one row represents.')
        self.approve_roles(name, roles, grain)
        info = self.info(name)
        info['semantics'] = {'reviewed':True, 'units':units, 'date_purposes':date_purposes,
                             'date_basis':date_basis, 'reviewed_at':now()}
        info.setdefault('semantic_versions', {})[info['current']] = {
            'roles':info['roles'], 'grain':grain, 'semantics':info['semantics']}
        with self.connect(write=True) as con: self._save_info(con, name, info)

    def save_object(self, kind, name, payload):
        if kind not in ('query','view','block','layout') or not name.strip() or len(name)>100:
            raise ValueError('Choose a saved-object type and a name of 1–100 characters.')
        encoded=json.dumps(payload, default=str)
        if len(encoded)>5_000_000: raise ValueError('Saved object exceeds 5 MB; narrow the result first.')
        with self.connect(write=True) as con:
            con.execute('CREATE TABLE IF NOT EXISTS _saved_objects(kind VARCHAR,name VARCHAR,payload JSON,PRIMARY KEY(kind,name))')
            con.execute('INSERT OR REPLACE INTO _saved_objects VALUES (?,?,?)',[kind,name,encoded])

    def save_analysis_defaults(self, name, context, metric_name):
        """Remember explicit analytical intent for this immutable data version."""
        from .context import AnalysisContext
        info=self.info(name)
        AnalysisContext(**context).validate(info)
        preferences={'version':info['current'],'context':context,'metric_name':metric_name}
        if info.get('analysis_defaults')!=preferences:
            info['analysis_defaults']=preferences
            with self.connect(write=True) as con: self._save_info(con,name,info)

    def saved_objects(self, kind):
        with self.connect() as con:
            if not con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name='_saved_objects'").fetchone()[0]:
                return {}
            return {n:json.loads(p) for n,p in con.execute('SELECT name,payload FROM _saved_objects WHERE kind=? ORDER BY name',[kind]).fetchall()}

    def transform(self, name, operation):
        info = self.info(name); parent = info['current']; cols = info['columns']
        kind = operation.get('kind'); column = operation.get('column')
        if column is not None and column not in cols: raise ValueError('Unknown transformation column.')
        target = 't_' + uuid.uuid4().hex
        expr = '*'; error_expr = None; error_original = None
        if kind in ('numeric','date','trim','nullify'):
            if column is None: raise ValueError('Choose a transformation column.')
            col = quote(column)
            if kind == 'numeric':
                converted = numeric_expression(column, operation.get('decimal','.'),operation.get('thousands'))
            elif kind == 'date':
                converted = date_expression(column, operation.get('format'))
            elif kind == 'trim': converted = f'trim(CAST({col} AS VARCHAR))'
            else:
                if not isinstance(operation.get('value'), str): raise ValueError('Specify the literal missing-value marker.')
                # Literal data is quoted, never interpreted as an expression.
                marker = operation['value'].replace("'", "''")
                converted = f"nullif(CAST({col} AS VARCHAR),'{marker}')"
            expr = f'* REPLACE ({converted} AS {col})'
            if kind in ('numeric','date'):
                error_expr = f"{col} IS NOT NULL AND trim(CAST({col} AS VARCHAR))<>'' AND ({converted}) IS NULL"
                error_original = f'CAST({col} AS VARCHAR)'
        elif kind == 'nullify_many':
            keep=operation.get('columns',cols); markers=operation.get('markers',[])
            if not keep or set(keep)-set(cols) or not markers or len(markers)>20 or any(not isinstance(m,str) for m in markers):
                raise ValueError('Choose existing columns and 1–20 literal missing-value markers.')
            values=','.join("'"+m.replace("'","''")+"'" for m in markers)
            expr='* REPLACE ('+','.join(f'CASE WHEN CAST({quote(c)} AS VARCHAR) IN ({values}) THEN NULL ELSE {quote(c)} END AS {quote(c)}' for c in keep)+')'
        elif kind == 'date_parts':
            parts=[operation.get(k) for k in ('year','month','day')]
            if any(c not in cols for c in parts): raise ValueError('Choose existing year, month and day columns.')
            new=operation.get('new_name','')
            if not new.strip() or new.casefold() in {c.casefold() for c in cols}|{'_row_id'}:
                raise ValueError('Choose a new, nonempty date column name.')
            year,month,day=map(quote,parts)
            text=f"CAST({year} AS VARCHAR)||'-'||lpad(CAST({month} AS VARCHAR),2,'0')||'-'||lpad(CAST({day} AS VARCHAR),2,'0')"
            converted=f'try_cast(({text}) AS DATE)'
            expr=f'*,{converted} AS {quote(new)}'
            error_expr=f'({text}) IS NOT NULL AND ({converted}) IS NULL'
            error_original=text
        elif kind == 'select':
            keep = operation.get('columns', [])
            if not keep or len(set(keep)) != len(keep) or set(keep)-set(cols): raise ValueError('Select known unique columns.')
            expr = '_row_id,' + ','.join(quote(c) for c in keep)
        elif kind == 'rename':
            new = operation.get('new_name','')
            if not new.strip() or new.casefold() in {c.casefold() for c in cols} | {'_row_id'}:
                raise ValueError('Choose a new, nonempty column name.')
            if column is None: raise ValueError('Choose a column to rename.')
            expr = f'* RENAME ({quote(column)} AS {quote(new)})'
        elif kind == 'deduplicate':
            expr = '_row_id,' + ','.join(quote(c) for c in cols)
        else: raise ValueError('Choose a supported preparation operation.')
        with self.connect(write=True) as con:
            con.execute('BEGIN')
            actual=json.loads(con.execute('SELECT info FROM _catalog WHERE name=?',[name]).fetchone()[0])
            if actual['current']!=parent or len(actual['versions'])!=len(info['versions']):
                raise ValueError('Another session changed this table. Reload before preparing a new version.')
            suffix = ''
            if kind == 'deduplicate':
                suffix = ' QUALIFY row_number() OVER (PARTITION BY ' + ','.join(quote(c) for c in cols) + ' ORDER BY _row_id)=1'
            con.execute(f'CREATE TABLE {quote(target)} AS SELECT {expr} FROM {quote(parent)}{suffix}')
            errors = 0
            if error_expr:
                con.execute(f'''CREATE TABLE {quote(target+'_errors')} AS SELECT _row_id,
                    {error_original} AS original_value FROM {quote(parent)} WHERE {error_expr}''')
                errors = con.execute(f'SELECT count(*) FROM {quote(target+"_errors")}').fetchone()[0]
            count = con.execute(f'SELECT count(*) FROM {quote(target)}').fetchone()[0]
            version = {'table':target,'parent':parent,'operation':operation,'rows':count,'created_at':now(),'errors':errors}
            info['versions'].append(version)
            self._activate(con, name, info, target)
            con.execute('COMMIT')
        return version

    def _activate(self, con, name, info, table):
        info['current'] = table
        info['columns'] = [r[0] for r in con.execute(f'DESCRIBE {quote(table)}').fetchall() if r[0] != '_row_id']
        info['rows'] = con.execute(f'SELECT count(*) FROM {quote(table)}').fetchone()[0]
        # A saved review belongs to its immutable version. New preparation needs a
        # fresh review, while undo restores the original approved definitions.
        restored = info.get('semantic_versions', {}).get(table)
        info['roles'] = restored['roles'] if restored else {}
        info['semantics'] = restored['semantics'] if restored else {'reviewed':False}
        if restored: info['grain'] = restored['grain']
        self._save_info(con,name,info)

    def activate(self, name, version):
        info = self.info(name)
        if version not in {v['table'] for v in info['versions']}:
            raise ValueError('Unknown version for this table.')
        with self.connect(write=True) as con: self._activate(con,name,info,version)

    def conversion_errors(self, name, limit=1000):
        info = self.info(name)
        version = next(v for v in info['versions'] if v['table']==info['current'])
        if not version['errors']: return pd.DataFrame(columns=['_row_id','original_value'])
        with self.connect() as con:
            return con.execute(f'SELECT * FROM {quote(info["current"]+"_errors")} ORDER BY _row_id LIMIT ?', [max(1,min(limit,1000))]).fetchdf()
