"""Deterministic full-table profiling. Semantic labels are reviewable hypotheses."""
import re

from ..datasets import quote


ROLE_NAMES = {
    'date': {'date', 'timestamp', 'order_date', 'event_time', 'date_received', 'year','month','day','hour','minute',
             'dep_time','arr_time','sched_dep_time','sched_arr_time','time_hour'},
    'customer': {'customer', 'customer_id', 'user_id', 'client_id'},
    'product': {'product', 'product_id', 'product_name', 'sku'},
    'transaction': {'order_id', 'transaction_id', 'invoice_id'},
    'revenue': {'revenue', 'sales', 'sales_amount', 'net_sales'},
    'expense': {'cost', 'expense', 'expenses', 'cost_amount'},
    'quantity': {'quantity', 'qty', 'units'},
    'price': {'price', 'unit_price'},
    'geography': {'country', 'state', 'city', 'region', 'latitude', 'longitude'},
    'company': {'company', 'company_name', 'supplier', 'vendor'},
    'status': {'status', 'outcome', 'response', 'timely_response'},
    'duration': {'duration', 'elapsed_time', 'delay', 'arr_delay', 'dep_delay'},
    'text': {'description', 'comment', 'narrative', 'notes', 'text'},
    'activity': {'activity','event','event_name'},
    'issue': {'issue','complaint_type'},
}


def identifier_name(name):
    return bool(re.search(r'(^id$|_id$|^id_|identifier|^zip|^postal|^code$|^rownames$|^index$|^flight$|^tailnum$)',
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
    if any(c['inferred_type'] == 'numeric' for c in result['columns']):
        result['possible_analyses'].append('distributions, numeric comparisons; approve metric definitions first')
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
    elif {'company','issue'}<=roles:
        dataset_type='Possible service/complaint observations. Confirm whether each row is a complaint, event or aggregated count.'
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
