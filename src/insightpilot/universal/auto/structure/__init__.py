"""Structure discovery: conditional categories, functional dependencies, derived columns, redundancy clusters.

Everything is detected from values. Column names are never used. Results are plain
dataclasses; the report renders them as tables.
"""
from dataclasses import asdict, dataclass, field
from itertools import combinations
import math

import numpy as np
import pandas as pd
from scipy import stats
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
    sample: object = field(default=None, repr=False)
    sample_rows: int = 0
    total_rows: int = 0
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
    result.sample = sample_frame(a, facts, cfg, absent_sql, result)
    explained = {(c.column, c.parent) for c in result.conditional}
    # A conditional column's real levels imply the parent's applicable value; that is not a separate finding.
    result.dependencies = [d for d in dependencies(a, facts, cfg, absent_sql, result) if (d.determinant, d.dependent) not in explained]
    result.derived = derived_columns(a, facts, cfg, result)
    return result


def sample_frame(a, facts, cfg, absent_sql, result):
    """One seeded sample (at most cfg.association_sample_rows rows): categorical fields as text, numeric fields as numbers."""
    columns = [n for n, f in facts.items() if f['type'] in ('categorical', 'binary', 'numeric')]
    if not columns: return None
    numeric = {n for n in columns if facts[n]['type'] == 'numeric'}
    projection = ','.join((numeric_expression(n) if n in numeric else f'CASE WHEN {absent_sql(n)} THEN NULL ELSE {text_value(n)} END') + f' AS {quote(n)}'
                          for n in columns)
    data, evidence = a.frame(f'SELECT {projection} FROM selected ORDER BY hash(_row_id + {cfg.seed}),_row_id LIMIT {cfg.association_sample_rows}',
                             'Seeded sample for structure screening and field associations')
    total = a.run('SELECT count(*) FROM selected', 'Rows')['rows'][0][0]
    result.log('associations', evidence)
    result.sample_rows, result.total_rows = len(data), total
    return data


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
    """A determines B when B has a single value within ≥ cfg.dependency_overlap of A's groups with two or more rows.

    Candidates are screened on the shared sample with a looser 95% threshold, then every candidate is
    checked exactly on all rows. A dependency that holds on all rows also holds in any subset of rows.
    """
    eligible = [n for n, f in facts.items() if f['type'] in ('categorical', 'binary', 'numeric') and 2 <= f['distinct_clean'] <= cfg.dependency_level_cap
                and f['distinct_clean'] <= 0.5 * max(f['clean'], 1)]
    if len(eligible) > cfg.structure_column_cap:
        result.limits.append(f'Functional dependencies were checked for the first {cfg.structure_column_cap} of {len(eligible)} eligible columns.')
        eligible = eligible[:cfg.structure_column_cap]
    data = result.sample
    codes = {n: pd.factorize(data[n], use_na_sentinel=True)[0] for n in eligible} if data is not None else {}
    candidates = []
    for determinant in eligible:
        dependents = [d for d in eligible if d != determinant and facts[determinant]['distinct_clean'] >= facts[d]['distinct_clean']]
        if data is None:
            candidates += [(determinant, d) for d in dependents]; continue
        key = codes[determinant]; present = key >= 0
        order = np.argsort(key[present], kind='stable'); sorted_key = key[present][order]
        if not len(sorted_key): continue
        starts = np.flatnonzero(np.r_[True, sorted_key[1:] != sorted_key[:-1]])
        for dependent in dependents:
            value = codes[dependent][present][order]; known = value >= 0
            count = np.add.reduceat(known.astype(np.int64), starts)
            low = np.minimum.reduceat(np.where(known, value, np.iinfo(np.int64).max), starts)
            high = np.maximum.reduceat(np.where(known, value, -1), starts)
            checked = count >= 2
            # Screening only: the exact rule is applied to every candidate on all rows below.
            if checked.any() and (low[checked] == high[checked]).mean() >= 0.95:
                candidates.append((determinant, dependent))
    found = {}
    for determinant, dependent in candidates:
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

    Screened with a trimmed R² (largest 1% of residuals dropped) on 1,000 sampled rows (R² ≥ threshold − 0.15),
    refitted on the whole seeded sample, then
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
            # Screen on the first 1,000 sampled rows with a looser bar (R² ≥ derived_r2 − 0.15), then fit the survivors on
            # the whole sample with the usual rule; the sample is already in seeded random order.
            screen = SCREEN_ROWS if len(c) > 2 * SCREEN_ROWS else len(c)
            for start in range(0, len(forms), 400):
                chunk = forms[start:start + 400]
                rough = trimmed_fits(c[:screen], [values[:screen] for _, _, values in chunk]) if screen < len(c) else [True] * len(chunk)
                survivors = [form for form, fit in zip(chunk, rough) if fit is True or (fit and fit[1] >= cfg.derived_r2 - 0.15)]
                fits = trimmed_fits(c, [values for _, _, values in survivors])
                for (formula, sources, _), fit in zip(survivors, fits):
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


SCREEN_ROWS = 1000


def trimmed_fits(c, columns):
    """trimmed_fit for many candidate columns at once; identical rules, vectorised over the complete candidates."""
    output = [None] * len(columns)
    if not columns: return output
    matrix = np.vstack(columns)
    complete = np.isfinite(matrix).all(axis=1) & bool(np.isfinite(c).all())
    for i in np.flatnonzero(~complete): output[i] = trimmed_fit(c, matrix[i])
    rows = np.flatnonzero(complete)
    if not len(rows) or len(c) < 20 or np.var(c) == 0: return output
    f = matrix[rows]
    denominator = (f * f).sum(axis=1)
    variance = f.var(axis=1)
    k = np.divide(f @ c, denominator, out=np.zeros(len(rows)), where=denominator != 0)
    residual = (c[None, :] - k[:, None] * f) ** 2
    keep = residual <= np.quantile(residual, 0.99, axis=1)[:, None]
    count = keep.sum(axis=1)
    s1 = keep @ c; s2 = keep @ (c * c)
    total = s2 - s1 * s1 / count
    sse = (residual * keep).sum(axis=1)
    for j, i in enumerate(rows):
        if not denominator[j] or variance[j] == 0 or not total[j] > 0: continue
        output[i] = (float(k[j]), float(1 - sse[j] / total[j]))
    return output


# ---------------- associations and clusters ----------------
def associations(a, facts, structure, cfg, absent_sql):
    """Pairwise association of fields, computed in memory from one seeded sample of at most cfg.association_sample_rows rows.

    Categorical pairs: bias-corrected Cramér's V; numeric pairs: Spearman ρ on complete pairs; mixed pairs:
    correlation ratio η. Categorical fields keep their 30 most frequent levels (others grouped, missing as its
    own level). Pairs that include a conditional field use the rows where it applies.
    """
    fields = [n for n, f in facts.items() if f['type'] in ('categorical', 'binary', 'numeric')]
    if len(fields) > cfg.structure_column_cap:
        structure.limits.append(f'Associations were computed for the first {cfg.structure_column_cap} of {len(fields)} fields.')
        fields = fields[:cfg.structure_column_cap]
    structure.checked_columns = fields
    if len(fields) < 2: return []
    numeric = {n for n in fields if facts[n]['type'] == 'numeric'}
    data = structure.sample if structure.sample is not None else sample_frame(a, facts, cfg, absent_sql, structure)
    if structure.total_rows > structure.sample_rows:
        structure.limits.append(f'Associations between fields use a seeded sample of {structure.sample_rows:,} of {structure.total_rows:,} rows (seed {cfg.seed}).')
    codes = {}
    for n in fields:
        if n in numeric: continue
        counts = data[n].value_counts()
        top = sorted(counts.index, key=lambda v: (-counts[v], str(v)))[:cfg.category_limit]
        mapping = {v: i for i, v in enumerate(top)}
        codes[n] = np.array([mapping.get(v, len(top)) if v is not None and v == v else len(top) + 1 for v in data[n]], dtype=np.int64)
    values = {n: pd.to_numeric(data[n], errors='coerce').to_numpy(dtype=float) for n in numeric}
    applicable = {}
    for c in structure.conditional:
        if c.column in fields:
            applicable[c.column] = (data[c.parent].to_numpy(dtype=object) != c.parent_value), f'rows where {c.parent} ≠ {c.parent_value}', c.parent
    # Spearman ρ is the Pearson correlation of average ranks; columns without missing values are ranked once.
    ranks = {n: stats.rankdata(values[n]) for n in numeric if not np.isnan(values[n]).any()}
    output = []
    for first, second in combinations(fields, 2):
        mask = np.ones(len(data), dtype=bool); notes = []
        skip = False
        for column in (first, second):
            if column in applicable:
                rows, note, parent = applicable[column]
                if parent in (first, second): skip = True; break
                mask &= rows; notes.append(note)
        if skip: continue
        kinds = (first in numeric, second in numeric)
        if kinds == (True, True) and not notes and first in ranks and second in ranks:
            rx, ry = ranks[first], ranks[second]; n = len(rx)
            value = float(np.corrcoef(rx, ry)[0, 1]) if n >= 3 and np.ptp(rx) and np.ptp(ry) else None
            measure = 'Spearman ρ'
        elif kinds == (True, True):
            x, y = values[first][mask], values[second][mask]
            keep = ~np.isnan(x) & ~np.isnan(y)
            n = int(keep.sum())
            value = float(stats.spearmanr(x[keep], y[keep]).statistic) if n >= 3 and np.ptp(x[keep]) and np.ptp(y[keep]) else None
            measure = 'Spearman ρ'
        elif kinds == (False, False):
            left, right = codes[first][mask], codes[second][mask]
            width = right.max() + 1 if len(right) else 1
            table = np.bincount(left * width + right, minlength=(left.max() + 1 if len(left) else 1) * width).reshape(-1, width)
            value = cramers_v_corrected(table); measure = "Cramér's V (bias-corrected)"; n = int(table.sum())
        else:
            category, number = (first, second) if not kinds[0] else (second, first)
            group, x = codes[category][mask], values[number][mask]
            keep = ~np.isnan(x); group, x = group[keep], x[keep]; n = len(x)
            if n < 2:
                value = None
            else:
                sizes = np.bincount(group); sums = np.bincount(group, weights=x)
                present = sizes > 0
                value = correlation_ratio(list(zip(sizes[present], sums[present] / sizes[present])), n, float(x.mean()), float(x.var()))
            measure = 'correlation ratio η'
        if value is not None and math.isfinite(value):
            output.append(Association(first, second, measure, float(f'{value:.12g}'), int(n), '; '.join(notes)))
    structure.associations = output
    structure.clusters = clusters(fields, output, cfg)
    return output


def correlation_ratio(groups, total_n, total_mean, total_variance_population):
    """η = sqrt(between-group sum of squares / total sum of squares)."""
    total_ss = total_variance_population * total_n
    if not total_ss or total_ss <= 0: return None
    between = sum(n * (mean - total_mean) ** 2 for n, mean in groups if n)
    return math.sqrt(min(1.0, between / total_ss))


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
    """Representative: the member with the highest target power, or the lowest missing share without a target.

    Derived columns represent a cluster only when every member is derived.
    """
    derived = {d.column for d in structure.derived}
    for cluster in structure.clusters:
        members = [m for m in cluster.members if m not in derived] or cluster.members
        if power:
            cluster.representative = max(members, key=lambda m: (power.get(m) if power.get(m) is not None else -1, -cluster.members.index(m)))
            cluster.reason = 'highest cross-validated predictive power'
        else:
            cluster.representative = min(members, key=lambda m: (missing_share.get(m, 0), cluster.members.index(m)))
            cluster.reason = 'lowest missing share'
