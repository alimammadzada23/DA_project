"""Finite presentation planning and statements calculated from executed results."""
from .metrics import NUMERIC_ROLES


def label(name): return str(name).replace('_',' ')


def format_value(value):
    if value is None: return 'Unavailable'
    if isinstance(value,(int,float)):
        digits=2 if abs(value)>=100 else 4
        return f'{value:,.{digits}f}'.rstrip('0').rstrip('.') if value%1 else f'{value:,.0f}'
    return str(value)


def calendar_grain(profile,basis):
    field=next((c for c in profile['columns'] if c['column']==basis),{})
    bounds=field.get('date_range')
    if bounds and (bounds[1]-bounds[0]).days<90: return 'day'
    return 'month'


def plan(engine, profile, metric):
    """Reviewed intent wins; names, domains and file paths never choose a KPI."""
    context=engine.context
    _,required=metric.compile(engine.info['columns'],engine.info['roles'])
    measures=[c for c in required if engine.info['roles'].get(c) in NUMERIC_ROLES]
    charts=[]
    if context.date_basis:
        field=next(c for c in profile['columns'] if c['column']==context.date_basis)
        bounds=field.get('date_range')
        # A single point is a value, not a time pattern. The UI offers a finer
        # grain without inventing a trend or widening an almost-zero axis.
        if bounds and (bounds[1]-bounds[0]).days>=({'day':1,'week':7,'month':28,'quarter':90,'year':365}[context.time_grain]):
            charts.append({'kind':'trend','field':context.date_basis,
                'title':f'{metric.name} by {context.time_grain} · {label(context.date_basis)}'})
    if context.comparison: charts.append({'kind':'ranked','field':context.comparison,
        'title':f'{metric.name} by {label(context.comparison)}'})
    if measures: charts.append({'kind':'histogram','field':measures[0],
        'title':f'Distribution of {label(measures[0])}'})
    return charts[:3]


def interpretation(result,metric,dimension=None):
    if not result['rows']: return 'No eligible observations remain in this selection.'
    if 'category' in result['columns'] and 'value' in result['columns']:
        i=result['columns'].index('value'); pairs=[r for r in result['rows'] if r[i] is not None]
        if not pairs: return 'The metric is unavailable for every displayed group. Review eligible inputs and denominators.'
        winner=max(pairs,key=lambda r:r[i]); cat=winner[result['columns'].index('category')]
        return f'{cat if cat is not None else "Missing category"} has the largest displayed {metric.name}: {winner[i]:,.5g} {metric.units}. Rankings describe the selected records; group size and coverage may differ.'
    if 'period' in result['columns']:
        valid=[r for r in result['rows'] if r[1] is not None]
        if len(valid)<2: return 'At least two observed periods are needed to describe a change.'
        first,last=valid[0],valid[-1]
        return f'{metric.name} is {first[1]:,.5g} {metric.units} in {first[0]} and {last[1]:,.5g} in {last[0]}. Edge periods can be incomplete; this comparison is descriptive.'
    if 'bin_start' in result['columns']:
        n=sum(r[1] for r in result['rows'])
        return f'{n:,} finite observations contribute to this distribution. Missing and nonfinite values are excluded.'
    return 'Values are calculated from the current selection; inspect the executed evidence before drawing conclusions.'
