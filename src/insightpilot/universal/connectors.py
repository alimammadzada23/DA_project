"""Explicit read-only source connectors. Credentials never enter saved receipts."""
import csv
from contextlib import closing
import importlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import uuid
from urllib.parse import urlsplit

import duckdb
import httpx

from ..datasets import digest, quote
from .store import MAX_ROWS, now


def database_tables(path):
    path=Path(path).resolve()
    if not path.is_file(): raise ValueError('Choose an existing database file.')
    if path.suffix.lower() in ('.sqlite','.sqlite3','.db'):
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as con:
            return [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    if path.suffix.lower()=='.duckdb':
        with duckdb.connect(str(path),read_only=True,config={'enable_external_access':'false'}) as con:
            return [r[0] for r in con.execute('SHOW TABLES').fetchall()]
    raise ValueError('Use a SQLite .sqlite/.db or DuckDB .duckdb file.')


def import_database(workspace,path,source_table,name):
    path=Path(path).resolve()
    if source_table not in database_tables(path): raise ValueError('Choose a source table from the database catalog.')
    if path==workspace.database: raise ValueError('Import from a separate source database.')
    if path.suffix.lower()=='.duckdb':
        source=duckdb.connect(str(path),read_only=True,config={'enable_external_access':'false'})
    else:
        source=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
        source.execute('PRAGMA query_only=ON')
    try:
        cursor=source.execute(f'SELECT * FROM {quote(source_table)} LIMIT {MAX_ROWS+1}')
        return _import_cursor(workspace,cursor,name,{'origin':'database_extract',
            'source_database_sha256':digest(path),'source_table':source_table,'retrieved_at':now()})
    finally: source.close()


def _import_cursor(workspace,cursor,name,provenance):
    with tempfile.TemporaryDirectory(prefix='insightpilot-connector-') as directory:
        target=Path(directory)/'extract.csv'
        with target.open('w',newline='',encoding='utf-8') as stream:
            writer=csv.writer(stream); writer.writerow([c[0] for c in cursor.description])
            count=0
            while True:
                rows=cursor.fetchmany(10000)
                if not rows: break
                count+=len(rows)
                if count>MAX_ROWS: raise ValueError('Source exceeds the row limit; select a bounded source table or view.')
                writer.writerows(rows)
        return workspace.add_file(target,name,provenance=provenance)


def import_postgres(workspace,schema,source_table,name,*,dsn_variable='INSIGHTPILOT_POSTGRES_DSN'):
    """Optional psycopg connector: named cursor and enforced read-only transaction."""
    dsn=os.environ.get(dsn_variable)
    if not dsn: raise ValueError(f'Configure {dsn_variable} securely before using PostgreSQL.')
    try: psycopg=importlib.import_module('psycopg')
    except ImportError as exc: raise ValueError('Install the postgres extra: uv sync --extra postgres') from exc
    from psycopg import sql
    with psycopg.connect(dsn,options='-c default_transaction_read_only=on -c statement_timeout=20000') as con:
        with con.cursor(name='insightpilot_read_only') as cursor:
            cursor.execute(sql.SQL('SELECT * FROM {}.{} LIMIT {}').format(
                sql.Identifier(schema),sql.Identifier(source_table),sql.Literal(MAX_ROWS+1)))
            # A server cursor populates description after the first fetch.
            first=cursor.fetchmany(10000)
            class Cursor:
                description=cursor.description
                def __init__(self): self.first=first
                def fetchmany(self,size):
                    if self.first is not None:
                        rows,self.first=self.first,None; return rows
                    return cursor.fetchmany(size)
            return _import_cursor(workspace,Cursor(),name,{'origin':'postgres_extract','schema':schema,
                'source_table':source_table,'retrieved_at':now(),'credential_variable':dsn_variable})


def import_api(workspace,url,name,*,records_key=None,page_parameter='page',size_parameter='per_page',
               page_size=100,token_variable=None,max_pages=50,delay=.2):
    """Bounded page-number JSON API; success requires an observed empty terminal page.

    No redirects, credentials in URLs, arbitrary next links, retries or raw-data
    transmission. The user must configure the source's actual pagination contract.
    """
    parsed=urlsplit(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('API base URL must be HTTPS without credentials, query parameters or fragments.')
    if not 1<=page_size<=1000 or not 1<=max_pages<=50 or not .1<=delay<=5:
        raise ValueError('Use 1–1,000 records/page, 1–50 pages and 0.1–5 seconds between requests.')
    headers={'Accept':'application/json'}
    if token_variable:
        token=os.environ.get(token_variable)
        if not token: raise ValueError('The selected token environment variable is not configured.')
        headers['Authorization']='Bearer '+token
    records=[]; requests=0; raw_pages=[]
    archive=workspace.directory/'sources'/('api_'+uuid.uuid4().hex); archive.mkdir(parents=True)
    with httpx.Client(timeout=30,follow_redirects=False,headers=headers) as client:
        for page in range(1,max_pages+1):
            with client.stream('GET',url,params={page_parameter:page,size_parameter:page_size}) as response:
                if response.status_code==429: raise ValueError('API rate limit reached; no partial dataset committed. Review Retry-After before a later attempt.')
                response.raise_for_status()
                content=bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content)>10*1024*1024: raise ValueError('API page exceeds 10 MiB; no partial dataset committed.')
            raw=archive/f'page-{page:04}.json'; raw.write_bytes(content)
            receipt={'page':page,'url':url,'status':response.status_code,'sha256':digest(raw),
                     'bytes':len(content),'retained':str(raw.relative_to(workspace.directory)),'retrieved_at':now()}
            raw.with_suffix('.receipt.json').write_text(json.dumps(receipt,indent=2))
            raw_pages.append(receipt)
            value=json.loads(content); requests+=1
            batch=value.get(records_key) if records_key and isinstance(value,dict) else value
            if not isinstance(batch,list) or any(not isinstance(r,dict) for r in batch):
                raise ValueError('Expected a flat record array; configure its top-level records key.')
            if not batch: break
            if any(isinstance(v,(dict,list)) for r in batch for v in r.values()): raise ValueError('Flatten nested API records before import.')
            records.extend(batch)
            if len(records)>100000: raise ValueError('API import exceeds 100,000 records; no partial dataset committed.')
            if page==max_pages: raise ValueError('Pagination limit reached without an empty terminal page; completeness unverified, no dataset committed.')
            time.sleep(delay)
    if not records: raise ValueError('The API returned no records.')
    with tempfile.TemporaryDirectory(prefix='insightpilot-api-') as directory:
        target=Path(directory)/'api.json'
        target.write_text(json.dumps(records,ensure_ascii=False))
        return workspace.add_file(target,name,provenance={'origin':'api_extract','url':url,'retrieved_at':now(),
            'requests':requests,'raw_pages':raw_pages,'extract_policy':'Combined flat record arrays; original successful HTTP response bytes and page hashes retained separately.',
            'pagination':{'page_parameter':page_parameter,'size_parameter':size_parameter,
            'page_size':page_size,'terminal_empty_page':True},
            'completeness':'An empty terminal page was observed. Source-side ordering, filtering and snapshot stability still require review.'})
