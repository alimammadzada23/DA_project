"""Local multi-table, versioned analytics store with retained original source bytes.

No user SQL or model output is used for imports or transformations. Files are
never replaced, tables are immutable versions, and switching a version is undo.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
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
from .file_readers import combined_columns, delimited_columns, file_reader, normalize_delimiter
from .profile import date_expression, numeric_expression, profile

MAX_ROWS = 100_000_000
BATCH_KINDS = ('numeric','date','trim','nullify','nullify_many')
FILL_STRATEGIES = ('constant','median','mean','mode')
INTEGER_TYPES = {'TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT'}
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def literal(value):
    """A SQL string literal. Data is quoted, never interpreted as an expression."""
    return "'" + str(value).replace("'", "''") + "'"


def _numeric_type(dtype):
    return dtype in INTEGER_TYPES or dtype in ('FLOAT','DOUBLE','REAL') or dtype.startswith('DECIMAL')


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

    def _check_new_name(self, name):
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,62}', name):
            raise ValueError('Table names need 1–63 letters, digits or underscores, starting with a letter.')
        if name in self.tables():
            raise ValueError('Use a new table name; existing tables and source files are preserved.')

    def _commit_import(self, con, name, table, *, warnings, source, operation, extra=None):
        """Shared checks for every file import; raises before anything is committed."""
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
                'grain':'Unconfirmed; review what one row represents.', 'created_at':now(),'import_warnings':warnings,
                'source':source, **(extra or {}),
                'versions':[{'table':table,'parent':None,'operation':{'kind':'import',**operation},
                             'rows':count,'created_at':now(),'errors':0}]}
        self._save_info(con, name, info)
        con.execute('COMMIT')
        return info

    def add_file(self, source, name, *, sheet=0, provenance=None, delimiter=None, column_names=None, progress=None):
        source = Path(source).resolve()
        self._check_new_name(name)
        if not source.is_file():
            raise ValueError('Choose an existing local file.')
        ensure_free_space(self.directory, [source])
        key = uuid.uuid4().hex
        sources = self.directory / 'sources'; sources.mkdir(exist_ok=True)
        retained = sources / (key + source.suffix.lower())
        shutil.copyfile(source, retained)
        table = 't_' + key
        warnings=[]; workbook={}
        try:
            with self.connect(write=True, external=True) as con:
                con.execute('BEGIN')
                if con.execute('SELECT count(*) FROM _catalog WHERE name=?',[name]).fetchone()[0]:
                    raise ValueError('This table name was imported in another session. Choose a new name.')
                if retained.suffix == '.xlsx':
                    workbook = load_workbook_table(con, retained, table, sheet, progress)
                    warnings.extend(workbook['warnings'])
                else:
                    delimiter = normalize_delimiter(retained, delimiter)
                    if retained.suffix in ('.csv','.tsv'):
                        delimited_columns(retained, delimiter, column_names, warnings)
                    elif column_names is not None:
                        raise ValueError('Column names can only be supplied for CSV or TSV files.')
                    reader, parameters = file_reader(retained, delimiter, column_names)
                    con.execute(f'CREATE TABLE {quote(table)} AS SELECT row_number() OVER () AS _row_id,* FROM {reader} LIMIT {MAX_ROWS+1}', parameters)
                return self._commit_import(con, name, table, warnings=warnings,
                    source={'origin':'user_upload', **(provenance or {}),
                            'filename':(provenance or {}).get('filename',source.name),
                            'retained':str(retained.relative_to(self.directory)),
                            'sha256':digest(retained),'bytes':retained.stat().st_size},
                    operation={'sheet':sheet,'delimiter':delimiter,'encoding':'UTF-8',
                               **({'supplied_column_names':column_names} if column_names else {})},
                    extra={**({'sheets':workbook['sheets']} if workbook else {}),
                           **({'sheet_overlap':workbook['overlap']} if workbook.get('overlap') else {})})
        except Exception as exc:
            # Keep rejected source bytes plus an explicit receipt; no partial table is committed.
            (sources / (key + '.rejected.json')).write_text(json.dumps({'source':retained.name,
                'sha256':digest(retained),'status':'rejected; no table committed','at':now(),
                'error_type':type(exc).__name__,'parsing_error':str(exc)[:3000]}))
            raise

    def add_files(self, paths, name, *, provenance=None, delimiter=None, column_names=None, progress=None):
        """Several files with the same columns become one table with a source_file column."""
        paths = [Path(path).resolve() for path in paths]
        self._check_new_name(name)
        if len(paths) < 2:
            raise ValueError('Choose at least two files, or import a single file directly.')
        if any(not path.is_file() for path in paths):
            raise ValueError('Every selected path must be an existing local file.')
        suffixes = {path.suffix.lower() for path in paths}
        if len(suffixes) > 1:
            raise ValueError('Files in one table must share a type; found ' + ', '.join(sorted(suffixes)) + '.')
        suffix = suffixes.pop()
        if suffix == '.xlsx':
            raise ValueError('Excel workbooks import one at a time. To combine sheets, choose all sheets for a workbook.')
        ensure_free_space(self.directory, paths)
        key = uuid.uuid4().hex
        folder = self.directory / 'sources' / key; folder.mkdir(parents=True)
        files = []
        for index, path in enumerate(paths):
            retained = folder / f'{index:05d}{suffix}'
            if progress: progress(f'Copying {path.name} ({index + 1} of {len(paths)})')
            shutil.copyfile(path, retained)
            files.append({'filename':path.name, 'retained':str(retained.relative_to(self.directory)),
                          'sha256':digest(retained), 'bytes':retained.stat().st_size})
        table = 't_' + key
        warnings = []
        names_by_hash = {}
        for item in files: names_by_hash.setdefault(item['sha256'], []).append(item['filename'])
        for names in names_by_hash.values():
            if len(names) > 1:
                warnings.append('These files have identical contents, and all their rows were kept: ' + ', '.join(names) + '.')
        combined = hashlib.sha256(''.join(f'{item["filename"]}\0{item["sha256"]}\n' for item in files).encode()).hexdigest()
        retained_paths = [(item['filename'], self.directory / item['retained']) for item in files]
        try:
            delimiter = normalize_delimiter(retained_paths[0][1], delimiter)
            if column_names is not None and suffix not in ('.csv', '.tsv'):
                raise ValueError('Column names can only be supplied for CSV or TSV files.')
            with self.connect(write=True, external=True) as con:
                con.execute('BEGIN')
                if con.execute('SELECT count(*) FROM _catalog WHERE name=?',[name]).fetchone()[0]:
                    raise ValueError('This table name was imported in another session. Choose a new name.')
                columns = combined_columns(con, retained_paths, delimiter, column_names, warnings)
                lowered = {column.casefold() for column, _ in columns}
                if any(column.startswith('_row_id') for column in lowered):
                    raise ValueError('_row_id is reserved for stable source-row references.')
                if 'source_file' in lowered:
                    raise ValueError('These files already have a column named "source_file", which this import adds. Rename that column first.')
                definitions = ','.join(quote(column) + ' ' + kind for column, kind in columns)
                con.execute(f'CREATE TABLE {quote(table)} (_row_id BIGINT,source_file VARCHAR,{definitions})')
                selection = ','.join(f'CAST({quote(column)} AS {kind})' for column, kind in columns)
                written = 0
                for label, path in retained_paths:
                    reader, parameters = file_reader(path, delimiter, column_names)
                    # Stop just past the limit; _commit_import then rejects the whole import.
                    written += con.execute(f'''INSERT INTO {quote(table)} SELECT {written}+row_number() OVER (),?,{selection}
                        FROM {reader} LIMIT {MAX_ROWS + 1 - written}''', [label, *parameters]).fetchone()[0]
                    if progress: progress(f'{label}: {written:,} rows so far')
                    if written > MAX_ROWS: break
                return self._commit_import(con, name, table, warnings=warnings,
                    source={'origin':'local_files', **(provenance or {}),
                            'filename':(provenance or {}).get('filename',f'{paths[0].parent.name} ({len(paths)} files)'),
                            'retained':str(folder.relative_to(self.directory)), 'sha256':combined,
                            'bytes':sum(item['bytes'] for item in files), 'files':files},
                    operation={'delimiter':delimiter,'encoding':'UTF-8','files':len(files),
                               **({'supplied_column_names':column_names} if column_names else {})})
        except Exception as exc:
            (self.directory / 'sources' / (key + '.rejected.json')).write_text(json.dumps({'source':key,'files':files,
                'sha256':combined,'status':'rejected; no table committed','at':now(),
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

    def _step(self, operation, cols):
        """SQL for one preparation operation: (projection, error predicate, error value, suffix)."""
        kind = operation.get('kind'); column = operation.get('column')
        if column is not None and column not in cols: raise ValueError('Unknown transformation column.')
        expr = '*'; error_expr = None; error_original = None; suffix = ''
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
        if kind == 'deduplicate':
            suffix = ' QUALIFY row_number() OVER (PARTITION BY ' + ','.join(quote(c) for c in cols) + ' ORDER BY _row_id)=1'
        return expr, error_expr, error_original, suffix

    def transform(self, name, operation):
        info = self.info(name); parent = info['current']; cols = info['columns']
        kind = operation.get('kind')
        if kind == 'batch': return self._transform_batch(name, info, operation)
        if kind == 'fill_missing': return self._fill_missing(name, info, operation)
        expr, error_expr, error_original, suffix = self._step(operation, cols)
        target = 't_' + uuid.uuid4().hex
        with self.connect(write=True) as con:
            con.execute('BEGIN')
            actual=json.loads(con.execute('SELECT info FROM _catalog WHERE name=?',[name]).fetchone()[0])
            if actual['current']!=parent or len(actual['versions'])!=len(info['versions']):
                raise ValueError('Another session changed this table. Reload before preparing a new version.')
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

    def _begin_version(self, con, name, info, parent):
        con.execute('BEGIN')
        actual=json.loads(con.execute('SELECT info FROM _catalog WHERE name=?',[name]).fetchone()[0])
        if actual['current']!=parent or len(actual['versions'])!=len(info['versions']):
            raise ValueError('Another session changed this table. Reload before preparing a new version.')

    def _finish_version(self, con, name, info, version):
        version['rows'] = con.execute(f'SELECT count(*) FROM {quote(version["table"])}').fetchone()[0]
        version['created_at'] = now()
        info['versions'].append(version)
        self._activate(con, name, info, version['table'])
        con.execute('COMMIT')
        return version

    def _transform_batch(self, name, info, operation):
        """Several column-preserving operations as one new version, with one receipt per step."""
        steps = operation.get('steps'); parent = info['current']; cols = info['columns']
        if not isinstance(steps, list) or not 1 <= len(steps) <= 500:
            raise ValueError('A batch needs 1–500 preparation steps.')
        if any(not isinstance(step, dict) or step.get('kind') not in BATCH_KINDS for step in steps):
            raise ValueError('A batch can combine only numeric, date, trim and missing-marker operations.')
        target = 't_' + uuid.uuid4().hex
        ctes = [f'"__s0" AS (SELECT * FROM {quote(parent)})']; ledgers = []
        for index, step in enumerate(steps, 1):
            expr, error_expr, error_original, _ = self._step(step, cols)
            ctes.append(f'"__s{index}" AS (SELECT {expr} FROM "__s{index-1}")')
            if error_expr:
                ledgers.append(f'SELECT _row_id,{error_original} AS original_value,{literal(step["column"])} AS column_name,'
                               f'{index} AS step FROM "__s{index-1}" WHERE {error_expr}')
        with self.connect(write=True) as con:
            self._begin_version(con, name, info, parent)
            con.execute(f'CREATE TABLE {quote(target)} AS WITH {",".join(ctes)} SELECT * FROM "__s{len(steps)}"')
            step_errors = {}
            if ledgers:
                con.execute(f'CREATE TABLE {quote(target+"_errors")} AS WITH {",".join(ctes)} '
                            f'SELECT * FROM ({" UNION ALL ".join(ledgers)}) ORDER BY step,_row_id')
                step_errors = dict(con.execute(f'SELECT step,count(*) FROM {quote(target+"_errors")} GROUP BY 1').fetchall())
            receipt = dict(operation, steps=[dict(step, errors=step_errors.get(index, 0)) for index, step in enumerate(steps, 1)])
            return self._finish_version(con, name, info, {'table':target,'parent':parent,'operation':receipt,
                                                          'errors':sum(step_errors.values())})

    def _fill_missing(self, name, info, operation):
        """Fill missing values (NULL or blank text) by an explicit strategy; every filled row is ledgered."""
        parent = info['current']; cols = info['columns']; column = operation.get('column')
        strategy = operation.get('strategy')
        if column not in cols: raise ValueError('Choose an existing column to fill.')
        if strategy not in FILL_STRATEGIES: raise ValueError('Choose a fill strategy: constant, median, mean or mode.')
        col = quote(column)
        missing = f"({col} IS NULL OR trim(CAST({col} AS VARCHAR))='')"
        condition = operation.get('condition'); selected = missing; receipt = {}
        with self.connect() as con:
            types = {r[0]: r[1] for r in con.execute(f'DESCRIBE {quote(parent)}').fetchall()}
            if condition is not None:
                other = condition.get('column') if isinstance(condition, dict) else None
                value = condition.get('value') if isinstance(condition, dict) else None
                if other not in cols or other == column:
                    raise ValueError('The condition needs a different existing column.')
                if not isinstance(value, str) or not value.strip():
                    raise ValueError('The condition needs a value to compare with.')
                if _numeric_type(types[other]):
                    number = con.execute('SELECT try_cast(? AS DOUBLE)', [value.strip()]).fetchone()[0]
                    if number is None or not math.isfinite(number):
                        raise ValueError(f'{other} is numeric, so the condition value must be a number.')
                    selected += f' AND {quote(other)} = {float(number)!r}'
                else:
                    selected += f' AND trim(CAST({quote(other)} AS VARCHAR)) = {literal(value.strip())}'
            to_fill = con.execute(f'SELECT count(*) FROM {quote(parent)} WHERE {selected}').fetchone()[0]
            if not to_fill:
                raise ValueError(f'Nothing to fill: {column} has no missing values' + (' where the condition holds.' if condition else '.'))
            dtype = types[column]; numeric = _numeric_type(dtype)
            if strategy in ('median', 'mean'):
                if not numeric:
                    raise ValueError(f'{strategy.capitalize()} needs a numeric column; {column} is stored as {dtype}. Convert it to numeric first.')
                function = 'median' if strategy == 'median' else 'avg'
                fill = con.execute(f'SELECT {function}({col}) FROM {quote(parent)} WHERE NOT {missing}').fetchone()[0]
                if fill is None: raise ValueError(f'{column} has no values to compute a {strategy} from.')
                fill = float(fill)
            elif strategy == 'mode':
                rows = con.execute(f'SELECT CAST({col} AS VARCHAR) AS value,count(*) AS n FROM {quote(parent)} '
                                   f'WHERE NOT {missing} GROUP BY 1 ORDER BY n DESC,value LIMIT 2').fetchall()
                if not rows: raise ValueError(f'{column} has no values to compute a mode from.')
                # Ties are broken by the smallest value in text order, so the result is repeatable.
                receipt['mode_tied'] = len(rows) > 1 and rows[0][1] == rows[1][1]
                fill = rows[0][0]
            else:
                fill = operation.get('value')
                if not isinstance(fill, str) or not fill.strip(): raise ValueError('Enter the constant value to fill with.')
                fill = fill.strip()
                if dtype != 'VARCHAR' and con.execute(f'SELECT try_cast(? AS {dtype})', [fill]).fetchone()[0] is None:
                    raise ValueError(f'{fill!r} is not a valid value for {column}, which is stored as {dtype}.')
        projection = col
        if isinstance(fill, float) and not fill.is_integer() and dtype in INTEGER_TYPES:
            # A fractional mean/median cannot be stored in an integer column without rounding.
            projection = f'CAST({col} AS DOUBLE)'; receipt['type_change'] = f'{dtype} → DOUBLE'; dtype = 'DOUBLE'
        if dtype == 'VARCHAR': fill_sql = literal(fill)
        else: fill_sql = f'CAST({literal(repr(fill) if isinstance(fill, float) else fill)} AS {dtype})'
        extra = ''
        if operation.get('indicator'):
            indicator = column + '_was_missing'
            if indicator.casefold() in {c.casefold() for c in cols} | {'_row_id'}:
                raise ValueError(f'A column named {indicator} already exists.')
            extra = f',CASE WHEN {missing} THEN 1 ELSE 0 END AS {quote(indicator)}'
        target = 't_' + uuid.uuid4().hex
        with self.connect(write=True) as con:
            self._begin_version(con, name, info, parent)
            # The indicator and the ledger come from the parent, so they record values before filling.
            con.execute(f'CREATE TABLE {quote(target)} AS SELECT * REPLACE '
                        f'(CASE WHEN {selected} THEN {fill_sql} ELSE {projection} END AS {col}){extra} FROM {quote(parent)}')
            con.execute(f'CREATE TABLE {quote(target+"_filled")} AS SELECT _row_id FROM {quote(parent)} WHERE {selected} ORDER BY _row_id')
            receipt = dict(operation, fill_value=fill, filled=to_fill, **receipt)
            return self._finish_version(con, name, info, {'table':target,'parent':parent,'operation':receipt,
                                                          'errors':0,'filled':to_fill})

    def filled_rows(self, name, limit=1000):
        info = self.info(name)
        version = next(v for v in info['versions'] if v['table']==info['current'])
        if not version.get('filled'): return pd.DataFrame(columns=['_row_id'])
        with self.connect() as con:
            return con.execute(f'SELECT * FROM {quote(info["current"]+"_filled")} ORDER BY _row_id LIMIT ?',
                               [max(1,min(limit,1000))]).fetchdf()

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
