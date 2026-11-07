"""Read-only SQL Lab: parsed SELECTs over the active, filtered workspace only.

The AST allowlist is separate from the execution barrier: read-only DuckDB,
external access disabled, memory/thread budgets, interruption and result bounds.
No provider-generated text is accepted by this service.
"""
from dataclasses import asdict
import json

import duckdb

from ..datasets import quote
from ..engine_settings import memory_limit, query_deadline
from .metrics import Metric
from .profile import date_expression
from .relationships import inspect_relationship, load_relationships

# Unknown functions fail closed. In particular, no filesystem, network, settings,
# secrets, extension loading, dynamic SQL, sequence or Python UDF functions.
FUNCTIONS=set('''count count_star sum avg mean median min max quantile_cont quantile_disc
    stddev_samp stddev_pop variance var_samp var_pop corr covar_samp count_if skewness
    abs round floor ceil ceiling sqrt pow power exp ln log log10 sign greatest least
    coalesce nullif if ifnull typeof isfinite isnan isinf
    lower upper trim ltrim rtrim length char_length substring substr left right
    concat concat_ws replace contains starts_with ends_with regexp_matches
    date_trunc date_part year month day dayofweek strftime strptime try_strptime
    datediff date_diff date_add date_sub last_day make_date epoch
    rank dense_rank row_number lag lead first_value last_value nth_value ntile
    cume_dist percent_rank sum_no_overflow count_distinct
    + - * / // % = != <> > >= < <= ~~ !~~ ~~* !~~* !__postfix
    constant_or_null'''.split())


class SQLLab:
    def __init__(self, engine):
        self.engine=engine
        self.sources={'selected':engine.table, engine.table.casefold():engine.table}
        self.relationships={}
        for name,rel in load_relationships(engine.workspace).items():
            if rel['left']!=engine.table: continue
            current=inspect_relationship(engine.workspace,rel['left'],rel['left_key'],rel['right'],rel['right_key'])
            if (current['right_duplicate_keys'] or current['left_version']!=engine.info['current'] or any(current[k]!=rel[k] for k in ('left_version','right_version'))):
                continue
            if current['right'].casefold()=='selected': continue
            self.relationships[name]=current
            self.sources[current['right'].casefold()]=current['right']

    def catalog(self):
        result={}
        for alias,table in self.sources.items():
            info=self.engine.info if table==self.engine.table else self.engine.workspace.info(table)
            result[alias]={'table':table,'columns':info['columns'],'version':info['current'],
                'source_sha256':info['source']['sha256'],'row_grain':info['grain'],
                'scope':'active filters' if table==self.engine.table else 'approved dimension; not filtered independently'}
        return result

    def validate(self, sql):
        if not isinstance(sql,str) or not sql.strip() or len(sql)>20_000:
            raise ValueError('Enter one SELECT/CTE query, at most 20,000 characters.')
        with duckdb.connect(config={'enable_external_access':'false'}) as con:
            try:
                statements=con.extract_statements(sql)
                if len(statements)!=1 or statements[0].type!=duckdb.StatementType.SELECT:
                    raise ValueError('Only one read-only SELECT or nonrecursive CTE is allowed. Writes, commands and multiple statements are blocked.')
                tree=json.loads(con.execute('SELECT json_serialize_sql(?)',[sql]).fetchone()[0])
            except duckdb.Error as exc: raise ValueError('SQL could not be parsed. Check its syntax.') from exc
        if tree.get('error'): raise ValueError('SQL could not be parsed.')
        scans=set(); node_count=0

        def base(node):
            return node.get('table_name','').casefold() if node.get('type')=='BASE_TABLE' else None

        def column(node):
            if node.get('class')=='CAST':
                if node.get('cast_type',{}).get('id')!='VARCHAR': return None
                return column(node['child'])
            if node.get('class')=='COLUMN_REF' and len(node.get('column_names',[]))==2:
                return tuple(node['column_names'])
            return None

        def join(node):
            # Deliberately limited: explicit fact-to-unique-dimension equality.
            # CTE/subquery joins and CROSS/ASOF/USING are not silently trusted.
            left,right=node.get('left',{}),node.get('right',{})
            l,r=base(left),base(right)
            cond=node.get('condition') or {}
            if (node.get('join_type') not in ('LEFT','INNER') or node.get('ref_type')!='REGULAR' or
                not l or not r or node.get('using_columns') or cond.get('type')!='COMPARE_EQUAL'):
                raise ValueError('SQL Lab joins require a direct fact LEFT/INNER JOIN to one validated unique-key dimension with an explicit key equality. Use a CTE after this join for further analysis.')
            la,ra=left.get('alias') or left['table_name'],right.get('alias') or right['table_name']
            pair=(column(cond.get('left',{})),column(cond.get('right',{})))
            valid=False
            for rel in self.relationships.values():
                expected=((la,rel['left_key']),(ra,rel['right_key']))
                if self.sources.get(l)==rel['left'] and self.sources.get(r)==rel['right'] and (pair==expected or pair==expected[::-1]):
                    valid=True
            if not valid: raise ValueError('Join keys or cardinality are unapproved. Validate a unique-right-key relationship in Data Hub first; multiplication is blocked.')

        def walk(value, visible=frozenset()):
            nonlocal node_count
            if isinstance(value,list):
                for child in value: walk(child,visible)
            elif isinstance(value,dict):
                node_count+=1
                if node_count>4000: raise ValueError('SQL expression is too complex.')
                kind=value.get('type')
                if kind in ('TABLE_FUNCTION','RECURSIVE_CTE_NODE','PIVOT','UNPIVOT'):
                    raise ValueError('Table functions, recursive queries and dynamic relations are unavailable in SQL Lab.')
                if kind=='BASE_TABLE':
                    name=value['table_name'].casefold()
                    if value.get('schema_name') or value.get('catalog_name') or name not in set(self.sources)|set(visible):
                        raise ValueError('Query only the listed workspace sources or prior CTEs. Catalogs, raw tables, internal metadata and other workspaces are blocked.')
                    if name in self.sources: scans.add(name)
                    if value.get('sample') or value.get('at_clause'): raise ValueError('Sampling and historical relation modifiers are unavailable.')
                if value.get('function_name'):
                    if value.get('schema') not in ('',None,'main') or value.get('catalog') or value['function_name'].lower() not in FUNCTIONS:
                        raise ValueError('Function is outside the SQL Lab analytical allowlist: '+value['function_name'])
                if kind=='JOIN': join(value)
                scope=set(visible)
                for entry in value.get('cte_map',{}).get('map',[]):
                    alias=entry['key'].casefold()
                    if alias in self.sources or alias in scope or alias.startswith('__'):
                        raise ValueError('CTE names must be unique and cannot shadow workspace sources.')
                    walk(entry['value'],scope)
                    scope.add(alias)
                for key,child in value.items():
                    if key!='cte_map': walk(child,scope)
        walk(tree)
        if not scans: raise ValueError('The query must use a listed workspace source.')
        # Strip only a trailing terminator; parsing above already handled comments.
        return sql.strip().removesuffix(';'), sorted(scans)

    def _prefix(self):
        entries=[]
        for alias,table in self.sources.items():
            if alias=='selected': continue
            body='SELECT * FROM selected' if table==self.engine.table else 'SELECT * FROM '+quote(self.engine.workspace.physical(table))
            entries.append(quote(alias)+' AS ('+body+')')
        return ','.join(entries)

    def execute(self, sql, *, page=0, size=100, explain=False, timeout=10, parameters=()):
        sql,scans=self.validate(sql)
        if not 0<=page<=1000 or not 1<=size<=5000: raise ValueError('Page or result-size limit exceeded.')
        prefix=self._prefix()
        # Engine already opens WITH selected. Add trusted logical source CTEs
        # inside a nested query, keeping every user reference in the validated scope.
        body=f'WITH {prefix} SELECT * FROM ({sql}) AS __user_query' if prefix else sql
        query=f'SELECT * FROM ({body}) AS __sql_result LIMIT {size+1} OFFSET {page*size}'
        try:
            result=self.engine.run(query,parameters,limit=size,explain=explain,timeout=timeout,method={
                'mode':'SQL Lab: validated read-only SELECT', 'user_sql':sql,'scanned_sources':scans,
                'scope':self.catalog(),'relationships':self.relationships,'page':page,'page_size':size,
                'time_limit_seconds':round(query_deadline(timeout,self.engine.info['rows']),2),
                'memory_limit':memory_limit(),'external_access':False})
        except duckdb.InterruptException as exc: raise ValueError('Query exceeded its time budget. Narrow filters or simplify the query.') from exc
        except duckdb.OutOfMemoryException as exc: raise ValueError('Query exceeded its memory budget. Reduce cardinality or aggregate first.') from exc
        except duckdb.Error as exc: raise ValueError('SQL could not execute: '+str(exc).split('\nLINE')[0][:600]) from exc
        result['user_sql']=sql
        return result

    def guided(self, metric, dimensions=(), *, date=None, grain='month', relationship=None, top=100, descending=True):
        if not 1<=top<=5000: raise ValueError('Choose a limit between 1 and 5,000.')
        if len(dimensions)>3: raise ValueError('Choose at most three grouping dimensions.')
        info=self.engine.info
        metric.compile(info['columns'],info['roles'])
        source=quote(self.engine.table)
        projection='*'; available=list(info['columns'])
        if relationship:
            if relationship not in self.relationships: raise ValueError('Choose an approved relationship with a unique right key.')
            rel=self.relationships[relationship]; cols=self.engine.workspace.columns(rel['right'])
            projection='f.*,'+','.join('d.'+quote(c)+' AS '+quote(rel['right']+'.'+c) for c in cols)
            available.extend(rel['right']+'.'+c for c in cols)
            source=(f'{source} f LEFT JOIN {quote(rel["right"])} d ON '
                    f'CAST(f.{quote(rel["left_key"])} AS VARCHAR)=CAST(d.{quote(rel["right_key"])} AS VARCHAR)')
        if set(dimensions)-set(available): raise ValueError('Unavailable grouping field.')
        groups=[quote(c) for c in dimensions]
        if date:
            if date!=self.engine.context.date_basis: raise ValueError('Use the globally reviewed date basis.')
            if grain not in ('day','week','month','quarter','year'): raise ValueError('Unsupported time grain.')
            groups.insert(0,f"date_trunc('{grain}',{date_expression(date)})::DATE AS period")
        expr,_=metric.compile(info['columns'],info['roles'])
        fields=','.join([*groups,expr+' AS value','count(*) AS records'])
        group_sql=' GROUP BY '+','.join(str(i+1) for i in range(len(groups))) if groups else ''
        date_where=' WHERE '+date_expression(date)+' IS NOT NULL' if date else ''
        sql=f'SELECT {fields} FROM (SELECT {projection} FROM {source}) AS scoped{date_where}{group_sql} ORDER BY value '+('DESC' if descending else 'ASC')+f' NULLS LAST LIMIT {top}'
        # Joins live inside the generated subquery; the validator checks them too.
        self.validate(sql)
        return sql

    def save(self,name,sql,metric=None):
        sql,_=self.validate(sql)
        from .report import configuration
        payload={'sql':sql,'analysis':configuration(self.engine,metric or Metric('Records'))}
        self.engine.workspace.save_object('query',name,payload)

    def reopen(self,name):
        from .report import replay
        payload=self.engine.workspace.saved_objects('query').get(name)
        if not payload: raise ValueError('Saved query not found.')
        engine,_=replay(self.engine.workspace,payload['analysis'])
        lab=SQLLab(engine); lab.validate(payload['sql'])
        return lab,payload['sql']
