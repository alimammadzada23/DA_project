"""Deterministic full-table profiling. Semantic labels are reviewable hypotheses."""
import re

from ..datasets import quote

NUMERIC_ROLES={'measure','quantity','duration','price','revenue','expense'}


def technical_numeric(item, rows, role=None):
    """Verified stored numbers, not approval of business meaning or units."""
    if role not in (None,'unassigned') and role not in NUMERIC_ROLES:return False
    if identifier_name(item['column']) or item['inferred_type']!='numeric':return False
    present=rows-item['missing']
    if not present or item['numeric_values']!=present:return False
    stats=item.get('statistics') or {}
    # Binary codes may be outcomes/categories; never turn them into implicit rates.
    if item['distinct']<=2 and stats.get('min') in (0,1) and stats.get('max') in (0,1):return False
    return True


def numeric_status(item, report, info):
    role=info.get('roles',{}).get(item['column'])
    if info.get('semantics',{}).get('reviewed') and role in NUMERIC_ROLES and item['numeric_values']:
        return 'reviewed_numeric'
    if technical_numeric(item,report['rows'],role):return 'technical_descriptive'
    return None


def safe_role_suggestions(report, info):
    """Generic editor defaults only. Nothing is approved or persisted here."""
    suggestions={}
    for item in report['columns']:
        if info.get('roles',{}).get(item['column']) not in (None,'unassigned'):continue
        if technical_numeric(item,report['rows']):suggestions[item['column']]='measure'
        elif item['inferred_type']=='category' and not item['numeric_values'] and not item['recognized_dates'] and not any(c['role']=='identifier' for c in item['candidates']):
            suggestions[item['column']]='category'
    return suggestions

ROLE_NAMES = {
    'date': {'date', 'timestamp', 'order_date', 'event_time', 'year','month','day','hour','minute'},
    'customer': {'customer', 'customer_id', 'user_id', 'client_id'},
    'product': {'product', 'product_id', 'product_name', 'sku'},
    'transaction': {'order_id', 'transaction_id', 'invoice_id'},
    'revenue': {'revenue'},
    'expense': {'cost', 'expense', 'expenses', 'cost_amount'},
    'quantity': {'quantity', 'qty', 'units'},
    'price': {'price', 'unit_price'},
    'geography': {'country', 'state', 'city', 'region', 'latitude', 'longitude'},
    'company': {'company', 'company_name', 'supplier', 'vendor'},
    'status': {'status', 'outcome', 'response'},
    'duration': {'duration', 'elapsed_time'},
    'text': {'description', 'comment', 'narrative', 'notes', 'text'},
    'activity': {'activity','event','event_name'},
}


def identifier_name(name):
    return bool(re.search(r'(^id$|_id$|^id_|identifier|^zip|^postal|^code$|^rownames$|^index$)',
                          re.sub(r'\W+', '_', name.lower())))


def date_expression(column, format=None):
    text = f'NULLIF(trim(CAST({quote(column)} AS VARCHAR)),\'\')'
    if format:
        if format not in ('%d/%m/%Y', '%m/%d/%Y', '%Y-%m-%d', '%Y'):
            raise ValueError('Choose a supported explicit date format.')
        return f"try_strptime({text}, '{format}')::DATE"
    # Never guess ambiguous slash-date conventions or parse numbers as dates.
    return (f"CASE WHEN regexp_matches({text}, '^\\d{{4}}-\\d{{2}}-\\d{{2}}($|[T ])') "
            f'THEN CAST(try_cast({text} AS TIMESTAMPTZ) AS DATE) END')


def numeric_expression(column, decimal='.', thousands=None):
    if decimal not in ('.', ',') or thousands not in (None, ',', '.', ' '):
        raise ValueError('Unsupported numeric separators.')
    if thousands == decimal:
        raise ValueError('Decimal and thousands separators must differ.')
    expr = f'NULLIF(trim(CAST({quote(column)} AS VARCHAR)),\'\')'
    original=expr; grouping=None
    if thousands:
        sep=re.escape(thousands); dec=re.escape(decimal)
        pattern=rf'^[+-]?\d{{1,3}}(?:{sep}\d{{3}})+(?:{dec}\d+)?(?:[eE][+-]?\d+)?$'
        grouping=f"NOT contains({original},'{thousands}') OR regexp_matches({original},'{pattern}')"
        expr = f"replace({expr}, '{thousands}', '')"
    if decimal == ',':
        expr = f"replace({expr}, ',', '.')"
    value = f'try_cast({expr} AS DOUBLE)'
    valid=f'isfinite({value})'+(f' AND ({grouping})' if grouping else '')
    return f'CASE WHEN {valid} THEN {value} END'


def profile(con, table):
    names = [r[0] for r in con.execute(f'DESCRIBE {quote(table)}').fetchall() if r[0] != '_row_id']
    count = con.execute(f'SELECT count(*) FROM {quote(table)}').fetchone()[0]
    result = {'rows': count, 'columns': [], 'semantic_notice':
              'Roles are hypotheses, not approved business definitions. Review identifiers, dates, units and denominators.'}
    for name in names:
        col = quote(name)
        num, dates = numeric_expression(name), date_expression(name)
        missing = f"({col} IS NULL OR trim(CAST({col} AS VARCHAR))='')"
        n, unique, numeric, dated = con.execute(f'''SELECT count(*) FILTER (WHERE {missing}),
            count(DISTINCT NULLIF(trim(CAST({col} AS VARCHAR)),'')), count({num}), count({dates})
            FROM {quote(table)}''').fetchone()
        present = count - n
        identifier = identifier_name(name)
        kind = ('identifier' if identifier else 'numeric' if present and numeric == present else
                'date' if present and dated == present else 'text' if unique > min(50, count * .5) else 'category')
        candidates = []
        normalized = re.sub(r'\W+', '_', name.lower()).strip('_')
        for role, aliases in ROLE_NAMES.items():
            if normalized in aliases:
                candidates.append({'role': role, 'confidence': 'name match; review required',
                                   'basis': f'Column name {name!r}; values do not establish business meaning.'})
        if identifier:
            candidates.insert(0, {'role': 'identifier', 'confidence': 'name match; review required',
                                 'basis': 'Identifier-like name; excluded from automatic measure selection.'})
        elif present and unique == present and kind in ('text', 'category'):
            candidates.append({'role': 'identifier', 'confidence': 'ambiguous',
                               'basis': 'Every nonmissing value is unique; could also be free text.'})
        if normalized in ('amount', 'value', 'total'):
            candidates.append({'role': 'measure', 'confidence': 'ambiguous',
                               'basis': 'Amount could be revenue, expense, balance or another quantity. No revenue assumption.'})
        item = {'column': name, 'inferred_type': kind, 'missing': n, 'distinct': unique,
                'numeric_values': numeric, 'recognized_dates': dated, 'candidates': candidates,
                'key_candidate': bool(count and not n and unique == count), 'statistics': None}
        item['potential_missing_markers']=con.execute(f'''SELECT count(*) FROM {quote(table)}
            WHERE upper(trim(CAST({col} AS VARCHAR))) IN ('NA','N/A','NULL','NONE','?')''').fetchone()[0]
        if numeric and not identifier:
            row = con.execute(f'''SELECT min(x),max(x),avg(x),median(x),stddev_samp(x),
                quantile_cont(x,.05),quantile_cont(x,.25),quantile_cont(x,.75),quantile_cont(x,.95),
                skewness(x) FROM (SELECT {num} x FROM {quote(table)})''').fetchone()
            item['statistics'] = dict(zip(['min','max','mean','median','stddev','p05','p25','p75','p95','skewness'], row))
        if dated:
            item['date_range'] = con.execute(f'SELECT min({dates}),max({dates}) FROM {quote(table)}').fetchone()
        result['columns'].append(item)
    cols = ','.join(quote(n) for n in names)
    distinct = con.execute(f'SELECT count(*) FROM (SELECT DISTINCT {cols} FROM {quote(table)})').fetchone()[0]
    result['exact_duplicate_rows'] = count - distinct
    result['possible_analyses'] = ['quality, category comparisons, records']
    if {'year','month','day'} <= {c.lower() for c in names}:
        result['possible_analyses'].append('Split year/month/day fields are present: review their meaning, then assemble a date in reversible preparation. They do not establish an event timestamp by themselves.')
    if any(c['inferred_type'] == 'numeric' for c in result['columns']):
        result['possible_analyses'].append('Basic descriptive statistics on verified numeric fields need no business-role approval; business metrics still require reviewed definitions.')
    if any(c['recognized_dates'] for c in result['columns']):
        result['possible_analyses'].append('time trends after date-role review')
    result['unsupported_without_definitions'] = [
        'Row grain and business meaning require confirmation.',
        'Rates require explicit denominators; counts alone are not incidence rates.',
        'Causal conclusions and population generalization are not established.',
        'Forecasts, funnels and retention require suitable time and entity data.']
    roles={hypothesis['role'] for col in result['columns'] for hypothesis in col['candidates']}
    if {'activity','date'}<=roles and 'identifier' in roles:
        dataset_type='Possible event records: activity, date and identifier-like fields are present. Confirm event identities and sequences.'
    elif {'customer','product'}<=roles and roles & {'revenue','quantity','price'}:
        dataset_type='Possible transactional observations. Confirm order/line-item grain, refunds, units and amount definitions.'
    else:
        dataset_type='General structured observations. Column names and values do not establish a reliable industry classification.'
    keys=[c['column'] for c in result['columns'] if c['key_candidate'] and c['inferred_type']=='identifier'
          and c['column'].lower() not in ('rownames','index')]
    result['understanding']={'type_hypothesis':dataset_type,
        'row_grain_hypothesis':('Unique identifier candidates: '+', '.join(keys[:5])+'. Verify their business meaning; uniqueness alone is insufficient.'
                                if keys else 'A defensible business row grain cannot be inferred automatically; confirm it before defining KPIs.'),
        'important_fields':[c['column'] for c in result['columns'] if c['candidates']][:15],
        'needs_clarification':['Confirm row grain and identifier meaning.','Confirm units/currency and denominators.',
                               'Confirm observation-window completeness and missing-value markers.'],
        'dataset_kind_is_verified':False}
    return result
