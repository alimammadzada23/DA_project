"""Structure discovery: conditional categories, functional dependencies, derived columns, redundancy clusters.

Everything is detected from values. Column names are never used. Results are plain
dataclasses; the report renders them as tables.
"""
from dataclasses import asdict, dataclass, field
from itertools import combinations
import math

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

from ....datasets import quote
from ...profile import numeric_expression
from ...store import literal
from ..stats import cramers_v_corrected

CATEGORICAL = ('categorical', 'binary')


@dataclass
class ConditionalCategory:
    column: str          # the column that is only applicable sometimes
    level: str           # its "not applicable" level
    parent: str          # the column that decides applicability
    parent_value: str    # the parent value that coincides with the level
    rows: int
    overlap_level: float     # share of rows with the level that have the parent value
    overlap_parent: float    # share of rows with the parent value that have the level

    @property
    def applicable_where(self):
        """Rows where the column is applicable: the parent does not have the value."""
        return f"(trim(CAST({quote(self.parent)} AS VARCHAR)) IS DISTINCT FROM {literal(self.parent_value)})"


@dataclass
class Dependency:
    determinant: str
    dependent: str
    kind: str                # 'determines' or 'equivalent'
    groups_checked: int
    share_consistent: float


@dataclass
class DerivedColumn:
    column: str
    formula: str
    sources: list
    factor: float
    r2_sample: float
    r2_full: float
    rows: int


@dataclass
class Association:
    first: str
    second: str
    measure: str
    value: float
    n: int
    subset: str = ''


@dataclass
class Cluster:
    members: list
    representative: str = ''
    reason: str = ''


@dataclass
class StructureResult:
    conditional: list = field(default_factory=list)
    dependencies: list = field(default_factory=list)
    derived: list = field(default_factory=list)
    associations: list = field(default_factory=list)
    clusters: list = field(default_factory=list)
    checked_columns: list = field(default_factory=list)
    limits: list = field(default_factory=list)
    queries: dict = field(default_factory=dict)

    def log(self, section, *queries):
        self.queries.setdefault(section, []).extend(queries)

    def conditional_for(self, column):
        return next((c for c in self.conditional if c.column == column), None)

    def derived_for(self, column):
        return next((d for d in self.derived if d.column == column), None)

    def cluster_for(self, column):
        return next((c for c in self.clusters if column in c.members), None)

    def to_dict(self):
        return {key: [asdict(item) for item in getattr(self, key)] for key in ('conditional', 'dependencies', 'derived', 'associations', 'clusters')} | {
            'checked_columns': self.checked_columns, 'limits': self.limits}


def text_value(column):
    return f'trim(CAST({quote(column)} AS VARCHAR))'


def discover(a, facts, cfg, absent_sql):
    """Run every structure check on analysis `a` (an auto_analysis.Analysis) with per-column `facts`."""
    result = StructureResult()
    categorical = [n for n, f in facts.items() if f['type'] in CATEGORICAL and 2 <= f['distinct_clean'] <= cfg.structure_level_cap]
    if len(categorical) > cfg.structure_column_cap:
        result.limits.append(f'Conditional categories were checked for the first {cfg.structure_column_cap} of {len(categorical)} categorical columns.')
        categorical = categorical[:cfg.structure_column_cap]
    counts = level_counts(a, categorical, absent_sql, result)
    result.conditional = conditional_categories(a, counts, cfg, result)
    explained = {(c.column, c.parent) for c in result.conditional}
    # A conditional column's real levels imply the parent's applicable value; that is not a separate finding.
    result.dependencies = [d for d in dependencies(a, facts, cfg, absent_sql, result) if (d.determinant, d.dependent) not in explained]
    result.derived = derived_columns(a, facts, cfg, result)
    return result


def level_counts(a, columns, absent_sql, result):
    counts = {}
    for column in columns:
        query = a.run(f'SELECT {text_value(column)} AS v,count(*) AS n FROM selected WHERE NOT {absent_sql(column)} GROUP BY 1 ORDER BY 1',
                      f'Levels of {column}')
        result.log('conditional', query)
        counts[column] = {v: n for v, n in query['rows']}
    return counts


def conditional_categories(a, counts, cfg, result):
    """Level L of X occurs in exactly the rows where Y = v (both overlaps ≥ cfg.dependency_overlap).

    Columns whose levels cover the same rows form one group. The group's parent is the column
    with the fewest levels, then the earliest column: when every member is symmetric, the data
    alone cannot tell which one decides applicability. Every other member must keep at least two
    levels besides L, so L is a "not applicable" level beside real answers.
    """
    columns = list(counts); links = []
    for i, x in enumerate(columns):
        for y in columns[i + 1:]:
            for level, level_rows in counts[x].items():
                for value, value_rows in counts[y].items():
                    if min(level_rows, value_rows) / max(level_rows, value_rows) < cfg.dependency_overlap: continue
                    query = a.run(f'SELECT count(*) FROM selected WHERE {text_value(x)}={literal(level)} AND {text_value(y)}={literal(value)}',
                                  f'Rows with {x} = {level} and {y} = {value}')
                    result.log('conditional', query)
                    both = query['rows'][0][0]
                    if both / level_rows >= cfg.dependency_overlap and both / value_rows >= cfg.dependency_overlap:
                        links.append(((x, level), (y, value), both))
    parent_of = {}
    def root(node):
        while parent_of.get(node, node) != node: node = parent_of[node]
        return node
    for left, right, _ in links:
        parent_of.setdefault(left, left); parent_of.setdefault(right, right)
        parent_of[root(left)] = root(right)
    groups = {}
    for node in parent_of: groups.setdefault(root(node), set()).add(node)
    rows_of = {}
    for left, right, both in links: rows_of[left] = rows_of[right] = both
    output = []
    for nodes in groups.values():
        nodes = sorted(nodes, key=lambda n: columns.index(n[0]))
        if len({column for column, _ in nodes}) != len(nodes): continue   # one column matched twice: not a single level
        parent, value = min(nodes, key=lambda n: (len(counts[n[0]]), columns.index(n[0])))
        for column, level in nodes:
            if column == parent or len(counts[column]) < 3: continue
            both = rows_of[(column, level)]
            output.append(ConditionalCategory(column, level, parent, value, both, both / counts[column][level], both / counts[parent][value]))
    return sorted(output, key=lambda c: (columns.index(c.parent), columns.index(c.column)))


def dependencies(a, facts, cfg, absent_sql, result):
    """A determines B when B has a single value within ≥ cfg.dependency_overlap of A's groups with two or more rows."""
    eligible = [n for n, f in facts.items() if f['type'] in ('categorical', 'binary', 'numeric') and 2 <= f['distinct_clean'] <= cfg.dependency_level_cap
                and f['distinct_clean'] <= 0.5 * max(f['clean'], 1)]
    if len(eligible) > cfg.structure_column_cap:
        result.limits.append(f'Functional dependencies were checked for the first {cfg.structure_column_cap} of {len(eligible)} eligible columns.')
        eligible = eligible[:cfg.structure_column_cap]
    found = {}
    for a_col, b_col in combinations(eligible, 2):
        for determinant, dependent in ((a_col, b_col), (b_col, a_col)):
            if facts[determinant]['distinct_clean'] < facts[dependent]['distinct_clean']: continue
            query = a.run(f'''SELECT count(*) FILTER (WHERE d=1),count(*) FROM (SELECT {text_value(determinant)} AS k,count(DISTINCT {text_value(dependent)}) AS d,
                count(*) AS n FROM selected WHERE NOT {absent_sql(determinant)} AND NOT {absent_sql(dependent)} GROUP BY 1) WHERE n>=2''',
                f'Does {determinant} determine {dependent}?')
            result.log('dependencies', query)
            consistent, groups = query['rows'][0]
            if groups and consistent / groups >= cfg.dependency_overlap:
                found[(determinant, dependent)] = Dependency(determinant, dependent, 'determines', groups, consistent / groups)
    output = []
    for (determinant, dependent), item in found.items():
        if (dependent, determinant) in found:
            if (determinant, dependent) < (dependent, determinant):
                item.kind = 'equivalent'; output.append(item)
        else:
            output.append(item)
    return sorted(output, key=lambda d: (d.determinant, d.dependent))


OPERATIONS = [('×', lambda a, b: a * b), ('+', lambda a, b: a + b), ('/', lambda a, b: a / b), ('−', lambda a, b: a - b)]


def derived_columns(a, facts, cfg, result):
    """C ≈ k·A or C ≈ k·(A op B) for op in ×, +, /, − (k fitted through the origin).

    Screened with a trimmed R² (largest 1% of residuals dropped) on a seeded sample, then
    confirmed with the plain R² on every complete row. Of formulas linking the same three
    columns, the product or sum form is kept.
    """
    numeric = [n for n, f in facts.items() if f['type'] == 'numeric']
    if len(numeric) > cfg.derived_column_cap:
        result.limits.append(f'Derived columns were searched among the first {cfg.derived_column_cap} of {len(numeric)} numeric columns.')
        numeric = numeric[:cfg.derived_column_cap]
    if len(numeric) < 2: return []
    numeric_sql = numeric_expression
    sample = a.run(f'SELECT {",".join(numeric_sql(n) + " AS " + quote(n) for n in numeric)} FROM selected '
                   f'ORDER BY hash(_row_id + {cfg.seed}),_row_id LIMIT {cfg.derived_sample_rows}', 'Sample for derived-column search',
                   limit=cfg.derived_sample_rows)
    result.log('derived', sample)
    data = {n: np.array([np.nan if r[i] is None else float(r[i]) for r in sample['rows']]) for i, n in enumerate(numeric)}
    candidates = []
    with np.errstate(divide='ignore', invalid='ignore', over='ignore'):
        for target in numeric:
            c = data[target]
            forms = [(f'{source}', [source], data[source]) for source in numeric if source != target]
            for first, second in combinations([n for n in numeric if n != target], 2):
                for symbol, operation in OPERATIONS:
                    forms.append((f'{first} {symbol} {second}', [first, second], operation(data[first], data[second])))
                    if symbol in ('/', '−'):
                        forms.append((f'{second} {symbol} {first}', [second, first], operation(data[second], data[first])))
            for formula, sources, values in forms:
                fit = trimmed_fit(c, values)
                if fit and fit[1] >= cfg.derived_r2:
                    candidates.append((target, formula, sources, fit[0], fit[1]))
    confirmed = {}
    n_sql = numeric_expression
    for target, formula, sources, factor, r2_sample in candidates:
        expression = n_sql(sources[0]) if len(sources) == 1 else '(' + formula_sql(formula, sources, n_sql) + ')'
        k = f'({float(factor)!r})'   # parenthesised: a negative factor must not form a -- comment
        query = a.run(f'''SELECT count(*),1-sum((c-{k}*f)*(c-{k}*f))/nullif(sum((c-m)*(c-m)),0) FROM
            (SELECT c,f,avg(c) OVER () AS m FROM (SELECT {n_sql(target)} AS c,{expression} AS f FROM selected)
             WHERE c IS NOT NULL AND f IS NOT NULL AND isfinite(f))''', f'Full-data fit of {target} ≈ {factor:.4g}·({formula})')
        result.log('derived', query)
        rows, r2_full = query['rows'][0]
        if r2_full is not None and r2_full >= cfg.derived_r2:
            key = frozenset([target, *sources])
            rank = (len(sources) == 1, '×+/−'.find(next((s for s in '×+/−' if f' {s} ' in formula), '×')), -r2_full, target)
            item = DerivedColumn(target, formula, sources, float(factor), float(r2_sample), float(r2_full), int(rows))
            if key not in confirmed or rank < confirmed[key][0]: confirmed[key] = (rank, item)
    kept = [item for _, item in confirmed.values()]
    # Accept the simplest formulas first; reject any that would make a column depend on itself
    # through another derived column (if B is k·A, A is not also reported as a formula of B).
    output = []; sources_of = {}

    def ancestors(column, seen=None):
        seen = seen or set()
        for source in sources_of.get(column, []):
            if source not in seen: seen.add(source); ancestors(source, seen)
        return seen
    for item in sorted(kept, key=lambda d: (len(d.sources), -d.r2_full, d.column)):
        if item.column in sources_of: continue
        if any(item.column == s or item.column in ancestors(s) for s in item.sources): continue
        sources_of[item.column] = list(item.sources); output.append(item)
    return sorted(output, key=lambda d: (len(d.sources), d.column))


def formula_sql(formula, sources, n_sql):
    symbol = next(s for s in '×+/−' if f' {s} ' in formula)
    first, second = sources
    sql_symbol = {'×': '*', '+': '+', '/': '/', '−': '-'}[symbol]
    return f'{n_sql(first)} {sql_symbol} NULLIF({n_sql(second)},0)' if symbol == '/' else f'{n_sql(first)} {sql_symbol} {n_sql(second)}'


def trimmed_fit(c, f):
    mask = np.isfinite(c) & np.isfinite(f)
    if mask.sum() < 20: return None
    c, f = c[mask], f[mask]
    denominator = (f * f).sum()
    if not denominator or np.var(c) == 0 or np.var(f) == 0: return None
    k = (c * f).sum() / denominator
    residual = (c - k * f) ** 2
    keep = residual <= np.quantile(residual, 0.99)
    total = ((c[keep] - c[keep].mean()) ** 2).sum()
    if not total: return None
    return k, 1 - residual[keep].sum() / total


# ---------------- associations and clusters ----------------
def associations(a, facts, structure, cfg, category_sql, correlation, correlation_ratio, absent_sql):
    """Pairwise association of non-identifier fields; conditional fields are measured on their applicable rows."""
    fields = [n for n, f in facts.items() if f['type'] in ('categorical', 'binary', 'numeric')]
    if len(fields) > cfg.structure_column_cap:
        structure.limits.append(f'Associations were computed for the first {cfg.structure_column_cap} of {len(fields)} fields.')
        fields = fields[:cfg.structure_column_cap]
    structure.checked_columns = fields
    output = []
    for first, second in combinations(fields, 2):
        conditions = []; notes = []
        for column in (first, second):
            conditional = structure.conditional_for(column)
            if conditional and conditional.parent in (first, second):
                conditions = None; break
            if conditional:
                conditions.append(conditional.applicable_where); notes.append(f'rows where {conditional.parent} ≠ {conditional.parent_value}')
        if conditions is None: continue
        where = ' AND '.join(conditions) or 'true'
        kinds = tuple('numeric' if facts[c]['type'] == 'numeric' else 'categorical' for c in (first, second))
        if kinds == ('numeric', 'numeric'):
            stats_, query = correlation(a, f'CASE WHEN {where} THEN {numeric_of(first)} END', f'CASE WHEN {where} THEN {numeric_of(second)} END',
                                        f'Spearman ρ of {first} and {second}')
            value, measure, n = stats_['spearman'], 'Spearman ρ', stats_['n']; queries = [query]
        elif kinds == ('categorical', 'categorical'):
            left, _, top_left = category_sql(a, first, where); right, _, top_right = category_sql(a, second, where)
            query = a.run(f'SELECT {left} AS x,{right} AS y,count(*) AS n FROM selected WHERE {where} GROUP BY 1,2', f'{first} × {second}')
            table = contingency(query['rows'])
            value, measure, n = cramers_v_corrected(table), "Cramér's V (bias-corrected)", int(table.sum()); queries = [top_left, top_right, query]
        else:
            category, number = (first, second) if kinds[0] == 'categorical' else (second, first)
            expression, _, top = category_sql(a, category, where); x = numeric_of(number)
            groups = a.run(f'SELECT {expression} AS g,count(v) AS n,avg(v) AS mean FROM (SELECT *,{x} AS v FROM selected WHERE {where}) '
                           f'WHERE v IS NOT NULL GROUP BY 1', f'{number} by {category}')
            total = a.run(f'SELECT count({x}),avg({x}),var_pop({x}) FROM selected WHERE {where}', f'{number} overall')
            n, mean, variance = total['rows'][0]
            value = correlation_ratio([(g_n, g_mean) for _, g_n, g_mean in groups['rows']], n, mean, variance)
            measure = 'correlation ratio η'; queries = [top, groups, total]
        structure.log('associations', *queries)
        if value is not None and math.isfinite(value):
            output.append(Association(first, second, measure, float(f'{value:.12g}'), int(n or 0), '; '.join(notes)))
    structure.associations = output
    structure.clusters = clusters(fields, output, cfg)
    return output


def numeric_of(column):
    return numeric_expression(column)


def contingency(rows):
    xs = sorted({r[0] for r in rows}, key=str); ys = sorted({r[1] for r in rows}, key=str)
    table = np.zeros((len(xs), len(ys)))
    for x, y, n in rows: table[xs.index(x), ys.index(y)] = n
    return table


def clusters(fields, pairs, cfg):
    """Average-linkage clustering on 1 − |association|; clusters are cut at distance 1 − cfg.redundancy_threshold."""
    if len(fields) < 2: return []
    index = {f: i for i, f in enumerate(fields)}
    distance = np.ones((len(fields), len(fields))); np.fill_diagonal(distance, 0)
    for p in pairs:
        distance[index[p.first], index[p.second]] = distance[index[p.second], index[p.first]] = 1 - min(1.0, abs(p.value))
    labels = fcluster(linkage(squareform(distance, checks=False), method='average'), t=1 - cfg.redundancy_threshold, criterion='distance')
    groups = {}
    for f, label in zip(fields, labels): groups.setdefault(label, []).append(f)
    return [Cluster(members) for members in sorted(groups.values(), key=lambda m: index[m[0]]) if len(members) > 1]


def choose_representatives(structure, power, missing_share):
    """Representative: the member with the highest target power, or the lowest missing share without a target."""
    for cluster in structure.clusters:
        if power:
            cluster.representative = max(cluster.members, key=lambda m: (power.get(m) if power.get(m) is not None else -1, -cluster.members.index(m)))
            cluster.reason = 'highest cross-validated predictive power'
        else:
            cluster.representative = min(cluster.members, key=lambda m: (missing_share.get(m, 0), cluster.members.index(m)))
            cluster.reason = 'lowest missing share'
