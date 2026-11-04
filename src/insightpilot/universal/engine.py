"""Filtered full-table SQL calculations with evidence bound to immutable versions."""
from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
import math
import threading
import time

import pandas as pd

from ..datasets import quote
from .metrics import Metric
from .profile import date_expression, numeric_expression
from .context import AnalysisContext


@dataclass(frozen=True)
class Filter:
    column: str
    operator: str
    value: object


class Engine:
    def __init__(self, workspace, table, filters=(), *, version=None, context=None):
        self.workspace, self.table = workspace, table
        self.info = workspace.info(table)
        if version is not None:
            if version not in {v['table'] for v in self.info['versions']}: raise ValueError('The requested analysis version is not retained.')
            if version!=self.info['current']:
                self.info['roles']={}
                self.info['semantics']={'reviewed':False}
                self.info['current']=version
                with workspace.connect() as con:
                    self.info['columns']=[r[0] for r in con.execute(f'DESCRIBE {quote(version)}').fetchall() if r[0]!='_row_id']
                    self.info['rows']=con.execute(f'SELECT count(*) FROM {quote(version)}').fetchone()[0]
                restored=self.info.get('semantic_versions',{}).get(version,{})
                self.info.update(restored)
        self.context=context or AnalysisContext(date_basis=self.info.get('semantics',{}).get('date_basis'))
        self.context.validate(self.info)
        self.filters = tuple(filters)
        self.where, self.parameters = self._predicate()

    def column(self, name):
        if name not in self.info['columns']: raise ValueError('Choose an available column.')
        return quote(name)

    def _predicate(self):
        clauses, params = [], []
        for f in self.filters:
            col = self.column(f.column)
            if f.operator == 'eq':
                clauses.append(f'{col} IS NOT DISTINCT FROM ?'); params.append(f.value)
            elif f.operator in ('gt','gte','lt','lte','eq_numeric','ne_numeric'):
                operator={'gt':'>','gte':'>=','lt':'<','lte':'<=','eq_numeric':'=','ne_numeric':'<>'}[f.operator]
                if not isinstance(f.value,(int,float)) or isinstance(f.value,bool) or not math.isfinite(f.value):
                    raise ValueError('Numeric filters require a finite number.')
                clauses.append(f'{numeric_expression(f.column)} {operator} ?')
                params.append(float(f.value))
            elif f.operator=='ne':
                clauses.append(f'{col} IS DISTINCT FROM ?'); params.append(f.value)
            elif f.operator in ('is_missing','not_missing'):
                missing=f"({col} IS NULL OR trim(CAST({col} AS VARCHAR))='')"
                clauses.append(('NOT ' if f.operator=='not_missing' else '')+missing)
            elif f.operator in ('from','through'):
                clauses.append(f'{date_expression(f.column)} {">=" if f.operator=="from" else "<="} ?::DATE')
                params.append(date.fromisoformat(str(f.value)))
            else: raise ValueError('Unsupported filter operator.')
        return ' AND '.join(clauses) or 'true', params

    def run(self, sql, params=(), *, limit=1000, method=None, explain=False, timeout=20):
        if not 1 <= limit <= 50_001: raise ValueError('Result limit exceeded.')
        full = f'WITH selected AS (SELECT * FROM {quote(self.info["current"])} WHERE {self.where}) ' + sql
        if explain: full='EXPLAIN '+full
        started = time.perf_counter()
        with self.workspace.connect() as con:
            timer = threading.Timer(min(20,max(.01,timeout)), con.interrupt); timer.start()
            try:
                result = con.execute(full,[*self.parameters,*params])
                columns = [c[0] for c in result.description]
                rows = result.fetchmany(limit+1)
            finally:
                timer.cancel(); timer.join()
        signature = json.dumps({'table':self.table,'version':self.info['current'],'filters':[asdict(f) for f in self.filters],
                                'sql':full,'parameters':[*self.parameters,*params], 'context':self.context.dictionary()},default=str,sort_keys=True)
        return {'columns':columns,'rows':rows[:limit],'truncated':len(rows)>limit,
                'sql':full,'parameters':[*self.parameters,*params], 'filters':[asdict(f) for f in self.filters],
                'source_sha256':self.info['source']['sha256'],'table':self.table,'version':self.info['current'],
                'evidence_id':hashlib.sha256(signature.encode()).hexdigest(),
                'context':self.context.dictionary(), 'row_grain':self.info['grain'],
                'elapsed_seconds':round(time.perf_counter()-started,4), 'method':method or 'Full selected-table DuckDB calculation'}

    def count(self): return self.run('SELECT count(*) AS records FROM selected')['rows'][0][0]

    def records(self, page=0, size=100):
        if not 0 <= page <= 100_000 or not 1 <= size <= 1000: raise ValueError('Invalid record page.')
        return self.run('SELECT * FROM selected ORDER BY _row_id LIMIT ? OFFSET ?', [size,page*size],limit=size)

    def categories(self, column):
        col = self.column(column)
        return self.run(f'SELECT {col} AS category,count(*) AS records FROM selected GROUP BY 1 ORDER BY records DESC,category NULLS LAST',limit=1000)

    def metric(self, metric=Metric('Records')):
        expr, required = metric.compile(self.info['columns'],self.info['roles'])
        coverage = ''.join(f',count(NULLIF(trim(CAST({quote(c)} AS VARCHAR)),\'\')) AS {quote("present_"+c)},count({numeric_expression(c)}) AS {quote("finite_numeric_"+c)}' for c in required)
        for component,formula in metric.components.items():
            compiled,_=Metric(component,formula).compile(self.info['columns'],self.info['roles'],allow_scalar=True)
            coverage+=f',{compiled} AS {quote(component)}'
        return self.run(f'SELECT {expr} AS value,count(*) AS records{coverage} FROM selected',method=asdict(metric))

    def groups(self, dimension, metric=Metric('Records'), top=15):
        col = self.column(dimension)
        expr, _ = metric.compile(self.info['columns'],self.info['roles'])
        if not 1 <= top <= 100: raise ValueError('Choose 1–100 categories.')
        return self.run(f'''SELECT {col} AS category,{expr} AS value,count(*) AS records
            FROM selected GROUP BY 1 ORDER BY value DESC NULLS LAST,category NULLS LAST LIMIT ?''', [top],method=asdict(metric))

    def distribution(self, column, bins=25):
        self.column(column)
        if not 5 <= bins <= 100: raise ValueError('Choose 5–100 bins.')
        expr = numeric_expression(column)
        return self.run(f'''SELECT CASE WHEN hi=lo THEN lo ELSE lo+bin_index*width END AS bin_start,count(*) AS records FROM
            (SELECT *,CASE WHEN hi=lo THEN 0 WHEN x=hi THEN {bins}
                WHEN x<lo+candidate*width THEN candidate-1
                WHEN candidate<{bins} AND x>=lo+(candidate+1)*width THEN candidate+1
                ELSE candidate END AS bin_index FROM
                (SELECT *,floor((x-lo)/NULLIF(width,0)) AS candidate FROM
                    (SELECT *,(hi-lo)/{bins} AS width FROM
                        (SELECT {expr} x,min({expr}) OVER () lo,max({expr}) OVER () hi FROM selected))))
            WHERE x IS NOT NULL GROUP BY 1 ORDER BY 1''',method={'field':column,
                'units':self.info.get('semantics',{}).get('units',{}).get(column) or 'units unconfirmed',
                'rule':'Equal-width left-closed/right-open bins; width is calculated once and boundary roundoff is corrected. NULL/nonfinite excluded; exact maximum occupies a separate boundary bin.'})

    def numeric_summary(self, column):
        self.column(column)
        n = numeric_expression(column)
        return self.run(f'''SELECT count(*) AS records,count(x) AS valid,avg(x) AS mean,stddev_samp(x) AS sd,
            min(x) AS minimum,quantile_cont(x,.25) AS q1,median(x) AS median,quantile_cont(x,.75) AS q3,
            max(x) AS maximum FROM (SELECT {n} x FROM selected)''')

    def trend(self, date_column, metric=Metric('Records'), period='month'):
        self.column(date_column)
        if period not in ('day','week','month','quarter','year'): raise ValueError('Unsupported time grain.')
        expr,_ = metric.compile(self.info['columns'],self.info['roles'])
        d = date_expression(date_column)
        result = self.run(f'''SELECT date_trunc('{period}',{d})::DATE AS period,{expr} AS value,
            count(*) AS records,min({d}) AS first_observed,max({d}) AS last_observed
            FROM selected WHERE {d} IS NOT NULL GROUP BY 1 ORDER BY 1''',limit=10000,
            method=asdict(metric) | {'time_grain':period,'edge_period_policy':'First/last observed periods may be incomplete. Missing periods are not zero-filled.'})
        return result

    def compare_periods(self, dimension, metric, date_column, earlier, later):
        self.column(date_column); col=self.column(dimension)
        if not metric.additive: raise ValueError('Contribution decomposition needs a single sum/count metric, not an average, ratio or unique count.')
        a,b=map(date.fromisoformat,map(str,earlier)); c,d=map(date.fromisoformat,map(str,later))
        if not a<=b<c<=d or (b-a)!=(d-c): raise ValueError('Use nonoverlapping, equally long inclusive periods, earlier first.')
        expr,_=metric.compile(self.info['columns'],self.info['roles']); dt=date_expression(date_column)
        # Compute each full aggregation separately; ratios must not be averaged or decomposed.
        result=self.run(f'''SELECT {col} AS category,0 AS period_index,{expr} AS value,count(*) AS records FROM selected
            WHERE {dt} BETWEEN ?::DATE AND ?::DATE GROUP BY 1
            UNION ALL SELECT {col},1,{expr},count(*) FROM selected
            WHERE {dt} BETWEEN ?::DATE AND ?::DATE GROUP BY 1''', [a,b,c,d],limit=10000,
            method=asdict(metric)|{'earlier':[a,b],'later':[c,d],'causal_claim':False})
        if result['truncated']: raise ValueError('Too many segments; filter the dataset before decomposition.')
        frame=frame_of(result)
        # Fill absent segments with zero, but preserve NULL aggregates for present invalid rows.
        parts={}
        for row in frame.itertuples(index=False):
            key=None if pd.isna(row.category) else row.category
            parts.setdefault(key,[0.,0.])[row.period_index]=None if pd.isna(row.value) else float(row.value)
        rows=[]
        for key,(before,after) in parts.items():
            delta=None if before is None or after is None else after-before
            rows.append((key,before,after,delta,None if not before or after is None else (after-before)/abs(before)))
        result.update(columns=['category','earlier','later','change','relative_change'],
                      rows=sorted(rows,key=lambda r:abs(r[3]) if r[3] is not None else -1,reverse=True))
        return result

    def scatter(self, x, y):
        self.column(x); self.column(y)
        return self.run(f'SELECT _row_id,{numeric_expression(x)} AS x,{numeric_expression(y)} AS y FROM selected ORDER BY _row_id LIMIT 2000',
                        limit=2000,method='First 2,000 selected rows in source order, not a representative random sample. Summary statistics use full data.')


def frame_of(result): return pd.DataFrame(result['rows'],columns=result['columns'])
