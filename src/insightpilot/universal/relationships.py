"""Explicit relationships, cardinality receipts and safe fact-to-dimension queries."""
from collections import OrderedDict
from copy import deepcopy
from dataclasses import asdict
import json
import threading

from ..datasets import quote

# These checks read every row of both tables. Table versions never change, so one
# answer stays correct for those versions and page reruns can reuse it.
_REMEMBERED_ENTRIES = 64
_remembered = OrderedDict()
_remembered_lock = threading.Lock()


def _remember(key, calculate):
    with _remembered_lock:
        if key in _remembered:
            _remembered.move_to_end(key)
            return deepcopy(_remembered[key])
    value = calculate()
    with _remembered_lock:
        _remembered[key] = deepcopy(value)
        while len(_remembered) > _REMEMBERED_ENTRIES:
            _remembered.popitem(last=False)
    return value


def inspect_relationship(workspace, left, left_key, right, right_key):
    if left == right: raise ValueError('Choose two different tables.')
    l,r=workspace.info(left),workspace.info(right)
    if left_key not in l['columns'] or right_key not in r['columns']: raise ValueError('Choose existing key columns.')
    return _remember((str(workspace.database),'inspect',left,l['current'],left_key,right,r['current'],right_key),
                     lambda:_inspect(workspace,l,left,left_key,r,right,right_key))


def _inspect(workspace, l, left, left_key, r, right, right_key):
    lt,rt,lk,rk=map(quote,[l['current'],r['current'],left_key,right_key])
    with workspace.connect() as con:
        stats=[]
        for table,key in ((lt,lk),(rt,rk)):
            stats.append(con.execute(f'''SELECT count(*),count(*)-count({key}),count({key})-count(DISTINCT {key})
                FROM {table}''').fetchone())
        unmatched_left=con.execute(f'SELECT count(*) FROM {lt} l WHERE NOT EXISTS (SELECT 1 FROM {rt} r WHERE CAST(l.{lk} AS VARCHAR)=CAST(r.{rk} AS VARCHAR))').fetchone()[0]
        unmatched_right=con.execute(f'SELECT count(*) FROM {rt} r WHERE NOT EXISTS (SELECT 1 FROM {lt} l WHERE CAST(l.{lk} AS VARCHAR)=CAST(r.{rk} AS VARCHAR))').fetchone()[0]
        pairs=con.execute(f'''SELECT coalesce(sum(l.n*r.n),0) FROM
            (SELECT CAST({lk} AS VARCHAR) k,count(*) n FROM {lt} WHERE {lk} IS NOT NULL GROUP BY 1) l
            JOIN (SELECT CAST({rk} AS VARCHAR) k,count(*) n FROM {rt} WHERE {rk} IS NOT NULL GROUP BY 1) r USING(k)''').fetchone()[0]
    cardinality=('many' if stats[0][2] else 'one')+'-to-'+('many' if stats[1][2] else 'one')
    return {'left':left,'left_key':left_key,'right':right,'right_key':right_key,
            'left_version':l['current'],'right_version':r['current'], 'cardinality':cardinality,
            'left_rows':stats[0][0],'right_rows':stats[1][0],'left_null_keys':stats[0][1], 'right_null_keys':stats[1][1],
            'left_duplicate_keys':stats[0][2],'right_duplicate_keys':stats[1][2],
            'left_unmatched_rows':unmatched_left,'right_unmatched_rows':unmatched_right,
            'inner_join_rows':int(pairs),'left_join_rows':int(pairs)+unmatched_left,
            'key_policy':'Exact text equality, no numeric coercion or trimming. NULL never matches NULL.'}


def save_relationship(workspace, name, relation, *, allow_multiplication=False):
    current=inspect_relationship(workspace,relation['left'],relation['left_key'],relation['right'],relation['right_key'])
    if current['left_join_rows']>current['left_rows'] and not allow_multiplication:
        raise ValueError('Join multiplies left-side rows. Inspect duplicate keys and explicitly acknowledge multiplication.')
    current['multiplication_acknowledged']=allow_multiplication
    with workspace.connect(write=True) as con:
        con.execute('INSERT OR REPLACE INTO _relationships VALUES (?,?)',[name,json.dumps(current)])
    return current


def load_relationships(workspace):
    with workspace.connect() as con:
        return {name:json.loads(data) for name,data in con.execute('SELECT name,definition FROM _relationships ORDER BY name').fetchall()}


def suggest_relationships(workspace,max_checks=50):
    """Conservative same-name key hypotheses; identity still needs user review."""
    tables=workspace.tables()
    versions=tuple(sorted((name,info['current']) for name,info in tables.items()))
    return _remember((str(workspace.database),'suggest',versions,max_checks),lambda:_suggest(workspace,tables,max_checks))


def _suggest(workspace,tables,max_checks):
    suggestions=[]; checks=0
    excluded={'rownames','index','year','month','day','date','amount','value','price','quantity'}
    for left,li in tables.items():
        for right,ri in tables.items():
            if left==right: continue
            matching={c.casefold():c for c in ri['columns'] if c.casefold() not in excluded}
            for lk in li['columns']:
                if lk.casefold() not in matching: continue
                rk=matching[lk.casefold()]
                if checks>=max_checks: return suggestions
                checks+=1
                # Test the candidate dimension key before a more expensive full join receipt.
                with workspace.connect() as con:
                    n,distinct,nonnull=con.execute(f'SELECT count(*),count(DISTINCT {quote(rk)}),count({quote(rk)}) FROM {quote(ri["current"])}').fetchone()
                if not n or n!=distinct or n!=nonnull: continue
                relation=inspect_relationship(workspace,left,lk,right,rk)
                if relation['left_unmatched_rows']<relation['left_rows']/2:
                    suggestions.append(relation|{'basis':'Same column name, unique nonmissing right key and majority left-key overlap. Entity meaning remains unverified.'})
                if len(suggestions)>=20: return suggestions
    return suggestions


def join_preview(workspace, relation, how='left'):
    if how not in ('left','inner','full'): raise ValueError('Choose left, inner or full join.')
    current=inspect_relationship(workspace,relation['left'],relation['left_key'],relation['right'],relation['right_key'])
    if any(current[k]!=relation[k] for k in ('left_version','right_version')): raise ValueError('Relationship versions changed; validate and save it again.')
    l,r,lk,rk=map(quote,[current['left_version'],current['right_version'],current['left_key'],current['right_key']])
    fields=[]
    for alias,name in [('l',current['left']),('r',current['right'])]:
        for column in ['_row_id',*workspace.columns(name)]:
            fields.append(f'{alias}.{quote(column)} AS {quote(name+"."+column)}')
    with workspace.connect() as con:
        return con.execute(f'''SELECT {','.join(fields)} FROM {l} l {how.upper()} JOIN {r} r
            ON CAST(l.{lk} AS VARCHAR)=CAST(r.{rk} AS VARCHAR) ORDER BY l._row_id,r._row_id LIMIT 100''').fetchdf()


def related_groups(engine, relation, dimension, metric):
    current=inspect_relationship(engine.workspace,relation['left'],relation['left_key'],relation['right'],relation['right_key'])
    if engine.table!=current['left']: raise ValueError('The metric must belong to the left fact table.')
    if any(current[k]!=relation[k] for k in ('left_version','right_version')): raise ValueError('Revalidate the relationship after changing table versions.')
    if current['right_duplicate_keys']: raise ValueError('Analytical joins require a unique right key to prevent double counting.')
    right=engine.workspace.info(current['right'])
    if dimension not in right['columns']: raise ValueError('Choose an available right-table dimension.')
    expr,required=metric.compile(engine.info['columns'],engine.info['roles'])
    # Aggregate in a projected relation containing fact columns only plus a uniquely named dimension.
    if '__dimension' in engine.info['columns']: raise ValueError('Reserved analytical join column.')
    l,r,lk,rk=map(quote,[current['left_version'],current['right_version'],current['left_key'],current['right_key']])
    result=engine.run(f'''SELECT __dimension AS category,{expr} AS value,count(*) AS records FROM
        (SELECT f.*,d.{quote(dimension)} AS __dimension FROM selected f LEFT JOIN {r} d
         ON CAST(f.{lk} AS VARCHAR)=CAST(d.{rk} AS VARCHAR)) GROUP BY 1 ORDER BY value DESC NULLS LAST''',
        method={'metric':asdict(metric),'relationship':current,'scope':'Fact filters apply; unmatched fact rows retained in NULL dimension.'})
    result['related_source_sha256']=right['source']['sha256']
    return result
