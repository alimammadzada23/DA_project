"""Static operation policy shared by schema, normalization and execution.

Input null means automatic operation policy, never unrestricted access. Dynamic
field meaning, workspace scope, units and approvals remain live semantic checks.
"""
from copy import deepcopy
from dataclasses import dataclass

MAX_GROUPS = 100
MAX_RECORDS = 100
MAX_PERIODS = 1000
MAX_SCATTER = 2000
MAX_OUTLIERS = 1000
MAX_PERIOD_COMPARISON_ROWS = 10000


@dataclass(frozen=True)
class Operation:
    groups: tuple
    fields: tuple
    measures: tuple
    charts: tuple
    sorts: tuple = ('records',)
    limit_kind: str = 'none'
    limit_bound: int = MAX_GROUPS
    required: tuple = ()


OPERATIONS = {
    'aggregate': Operation((0, 2), (0, 0), (1, 4), ('auto', 'table', 'bar', 'pie'),
                           ('records', 'group', 'm0', 'm1', 'm2', 'm3'), 'aggregate', required=('measures',)),
    'records': Operation((0, 0), (0, 40), (0, 0), ('auto', 'table'), limit_kind='preview', limit_bound=MAX_RECORDS),
    'schema': Operation((0, 0), (0, 40), (0, 0), ('auto', 'table')),
    'missing': Operation((0, 0), (0, 40), (0, 0), ('auto', 'table')),
    'summary': Operation((0, 0), (1, 1), (0, 0), ('auto', 'table'), required=('fields',)),
    'distribution': Operation((0, 0), (1, 1), (0, 0), ('auto', 'table', 'histogram'), required=('fields',)),
    'group_distribution': Operation((1, 1), (1, 1), (0, 0), ('auto', 'table', 'box'),
                                    ('records', 'group'), 'groups', required=('group_by', 'fields')),
    'scatter': Operation((0, 0), (2, 2), (0, 0), ('auto', 'table', 'scatter'),
                         limit_kind='preview', limit_bound=MAX_SCATTER, required=('fields',)),
    'trend': Operation((0, 0), (0, 0), (1, 1), ('auto', 'table', 'line'), ('period',),
                       'series', MAX_PERIODS, ('measures',)),
    'compare_periods': Operation((1, 1), (0, 0), (1, 1), ('auto', 'table', 'bar'),
                                limit_kind='complete', limit_bound=MAX_PERIOD_COMPARISON_ROWS, required=('group_by', 'measures', 'periods')),
    'outliers': Operation((0, 0), (1, 1), (0, 0), ('auto', 'table'),
                          limit_kind='preview', limit_bound=MAX_OUTLIERS, required=('fields',)),
}


def automatic_limit(operation, groups):
    policy = OPERATIONS[operation]
    if policy.limit_kind == 'aggregate': return None if groups else 1
    if policy.limit_kind == 'preview': return policy.limit_bound
    return None


def execution_bound(data):
    """Only validated plans reach this function; bounds cannot come from code."""
    return data['limit'] if data['limit'] is not None else OPERATIONS[data['operation']].limit_bound


def defaults(data, grain):
    op = data['operation']; policy = OPERATIONS[op]
    result = {'group_by': [], 'measures': [], 'filters': [], 'fields': [],
              'sort_by': 'period' if op == 'trend' else 'records',
              'descending': op != 'trend', 'bins': 25, 'date_basis': None,
              'grain': grain, 'relationship': None, 'chart': 'auto', 'inherit': False}
    for key, value in result.items(): data.setdefault(key, deepcopy(value))
    # Missing and explicit null have the same documented automatic meaning.
    if data.get('limit') is None: data['limit'] = automatic_limit(op, data['group_by'])
    for key in ('limit','bins'):
        if isinstance(data.get(key),float) and data[key].is_integer():data[key]=int(data[key])
    if policy.limit_kind in ('none', 'complete', 'series'): data['limit'] = None
    if op == 'trend':
        # The existing executor/line chart is chronological; canonicalize old
        # inert defaults as well. Explicit reverse-order requests clarify.
        data['sort_by'] = 'period'; data['descending'] = False
    return data


def legacy_saved_plan(data, grain):
    """Reproduce old ignored display defaults; never change inputs/approvals."""
    result = defaults(deepcopy(data), grain); policy = OPERATIONS[result['operation']]
    if result['sort_by'] not in policy.sorts: result['sort_by'] = policy.sorts[0]
    if result['chart'] not in policy.charts: result['chart'] = 'auto'
    if result['operation']=='aggregate' and not result['group_by'] and result['chart'] in ('bar','pie'):
        result['chart']='table'
    return result


def prompt_rules():
    return {name: {'group_by_count': p.groups, 'field_count': p.fields,
                   'measure_count': p.measures, 'charts': p.charts, 'sort_by': p.sorts,
                   'limit_policy': p.limit_kind, 'result_bound': p.limit_bound}
            for name,p in OPERATIONS.items()}


def operation_variants(properties):
    """Disjoint operation + inheritance branches, with reusable definitions."""
    variants = []
    definitions = {key: deepcopy(value) for key, value in properties.items()}
    definitions['measure'] = deepcopy(properties['measures']['items'])
    for name, policy in OPERATIONS.items():
        for inherited in (False, True):
            p = {key: {'$ref': '#/$defs/' + key} for key in properties}
            p.update(action={'const': 'analyze'}, operation={'const': name}, inherit={'const': inherited})
            for key, bounds in (('group_by', policy.groups), ('fields', policy.fields), ('measures', policy.measures)):
                definition = deepcopy(properties[key]); definition.update(minItems=bounds[0], maxItems=bounds[1])
                if key == 'measures': definition['items'] = {'$ref': '#/$defs/measure'}
                p[key] = definition
            p['chart'] = {'enum': list(policy.charts)}; p['sort_by'] = {'enum': list(policy.sorts)}
            p['limit'] = {'type': ['integer', 'null'], 'minimum': 1, 'maximum': policy.limit_bound,
                          'description': 'Null or omission selects the automatic policy for this operation.'}
            if policy.limit_kind in ('none', 'complete', 'series'): p['limit'] = {'type': 'null'}
            if name != 'aggregate': p['relationship'] = {'type': 'null'}
            if name != 'compare_periods': p.pop('periods', None)
            required = ['version', 'action', 'table', 'operation']
            if inherited: required.append('inherit')
            else: required.extend(policy.required)
            variants.append({'type': 'object', 'additionalProperties': False, 'required': required, 'properties': p})
    return variants, definitions
