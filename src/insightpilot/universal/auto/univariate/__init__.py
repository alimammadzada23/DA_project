"""Comparable single-feature predictive power, computed from DuckDB count tables.

Every field type is binned the same way (levels for categories, equal-count bins for
numbers, one extra bin for missing values) and scored with cross-validated target
encoding: out-of-fold ΔAUC over 0.5 for classification, out-of-fold R² for regression.
"""
import numpy as np

from ....datasets import quote
from ...profile import date_expression, numeric_expression
from ...store import literal
from .. import stats

MISSING_BIN = '␞MISSING␞'
OTHER_BIN = '␞OTHER␞'


def level_bins(a, column, where, cap, absent_sql):
    """Every level up to `cap` (by frequency), the rest as one group, missing values as their own bin."""
    query = a.run(f'''SELECT CAST({quote(column)} AS VARCHAR) AS v FROM selected WHERE {where} AND NOT {absent_sql(column)}
        GROUP BY 1 ORDER BY count(*) DESC,v LIMIT {cap + 1}''', f'Levels of {column} for predictive power', limit=cap + 1)
    levels = [r[0] for r in query['rows']]
    grouped = len(levels) > cap
    listed = ','.join(literal(v) for v in levels[:cap]) or 'NULL'
    sql = (f"CASE WHEN {absent_sql(column)} THEN {literal(MISSING_BIN)} WHEN CAST({quote(column)} AS VARCHAR) IN ({listed}) "
           f"THEN CAST({quote(column)} AS VARCHAR) ELSE {literal(OTHER_BIN)} END")
    return sql, query, grouped


def numeric_bins(a, column, where, bins):
    """Equal-count bins from quantiles computed on all eligible rows (no target involved); missing values form bin −1."""
    x = numeric_expression(column)
    quantiles = ','.join(repr(i / bins) for i in range(1, bins))
    query = a.run(f'SELECT quantile_cont({x},[{quantiles}]) FROM selected WHERE {where}', f'Bin edges of {column}')
    edges = sorted({float(e) for e in (query['rows'][0][0] or []) if e is not None})
    position = ' + '.join(f'CASE WHEN {x} > {e!r} THEN 1 ELSE 0 END' for e in edges) or '0'
    return f'CASE WHEN {x} IS NULL THEN -1 ELSE {position} END', query, len(edges) + 1


def fold_sql(cfg, stratify_sql=None):
    """Deterministic folds: rows ordered by hash(_row_id + seed) within each class, dealt round-robin."""
    partition = f'PARTITION BY {stratify_sql} ' if stratify_sql else ''
    return f'((row_number() OVER ({partition}ORDER BY hash(_row_id + {cfg.seed}),_row_id) - 1) % {cfg.cv_folds})'


def field_power(a, spec, column, kind, cfg, absent_sql):
    """Cross-validated single-feature power and permutation-adjusted NMI of `column` for target `spec`."""
    where = spec.ok
    if kind == 'numeric':
        bin_sql, edges, count = numeric_bins(a, column, where, cfg.numeric_bins); setup = [edges]; grouping = f'{count} equal-count bins'
    elif kind == 'date':
        d = date_expression(column)
        bin_sql = f"CASE WHEN {d} IS NULL THEN {literal(MISSING_BIN)} ELSE strftime(date_trunc('month',{d}),'%Y-%m') END"
        setup = []; grouping = 'calendar months plus a missing bin'
    else:
        bin_sql, levels, grouped = level_bins(a, column, where, cfg.ranking_level_cap, absent_sql); setup = [levels]
        grouping = f'top {cfg.ranking_level_cap} levels plus one group' if grouped else 'every level'
    if spec.kind == 'numeric':
        query = a.run(f'''SELECT fold,bin,count(*) AS n,sum(y) AS s,sum(y*y) AS ss FROM (SELECT {bin_sql} AS bin,{spec.value} AS y,
            {fold_sql(cfg)} AS fold FROM selected WHERE {where}) GROUP BY 1,2''', f'Fold × bin sums of {spec.column} for {column}')
        cells = {(f, b): (float(n), float(s), float(ss)) for f, b, n, s, ss in query['rows']}
        per_fold = stats.cv_regression_power(cells, cfg.cv_folds, cfg.encoding_smoothing)
        target_bins, target_edges, _ = numeric_bins(a, spec.column, where, cfg.numeric_bins)
        table_query = a.run(f'SELECT {bin_sql} AS bin,{target_bins} AS ybin,count(*) FROM selected WHERE {where} GROUP BY 1,2',
                            f'{column} × binned {spec.column}')
        queries = [*setup, query, target_edges, table_query]; table_rows = table_query['rows']
        value = float(np.mean(per_fold)) if per_fold else None; metric = 'R²'; auc = None
    else:
        y = spec.positive_flag if spec.kind == 'binary' else spec.label
        query = a.run(f'''SELECT fold,bin,y,count(*) AS n FROM (SELECT {bin_sql} AS bin,{y} AS y,{fold_sql(cfg, y)} AS fold
            FROM selected WHERE {where}) GROUP BY 1,2,3''', f'Fold × bin × class counts for {column}')
        cells = {(f, b, c): float(n) for f, b, c, n in query['rows']}
        classes = [0, 1] if spec.kind == 'binary' else sorted({c for _, _, c in cells}, key=str)
        per_fold = stats.cv_classification_power(cells, classes, cfg.cv_folds, cfg.encoding_smoothing)
        auc = float(np.mean(per_fold)) if per_fold else None
        value = auc - 0.5 if auc is not None else None; metric = 'ΔAUC'
        merged = {}
        for (_, b, c), n in cells.items(): merged[(b, c)] = merged.get((b, c), 0) + n
        table_rows = [(b, c, n) for (b, c), n in merged.items()]; queries = [*setup, query]
    table = contingency(table_rows)
    nmi = stats.permutation_nmi(table, cfg.permutations, cfg.seed) if table.size and min(table.shape) > 1 else None
    return {'metric': metric, 'value': value, 'auc': auc, 'sd': float(np.std(per_fold, ddof=1)) if len(per_fold) > 1 else None,
            'folds': [float(f) for f in per_fold], 'nmi': nmi['nmi'] if nmi else None, 'nmi_adjusted': nmi['adjusted'] if nmi else None,
            'nmi_p': nmi['p_value'] if nmi else None, 'grouping': grouping, 'queries': queries}


def contingency(rows):
    xs = sorted({r[0] for r in rows}, key=str); ys = sorted({r[1] for r in rows}, key=str)
    table = np.zeros((len(xs), len(ys)), dtype=np.int64)
    for x, y, n in rows: table[xs.index(x), ys.index(y)] += int(n)
    return table


def rate_columns(positives, rows, overall, confidence):
    """Practical effect of one group: rate, Wilson interval, difference in percentage points and lift."""
    rate = positives / rows if rows else None
    low, high = stats.wilson_interval(positives, rows, confidence)
    return [rate, low, high, (rate - overall) * 100 if rate is not None else None, rate / overall if rate is not None and overall else None]


def monotone_share(values, weights):
    """Share of the weighted between-bin variation explained by the best monotone (isotonic) fit.

    1 means a monotone curve explains the bins fully; low values with real differences between bins
    indicate a non-linear shape such as a U or a threshold (isotonic regression: Barlow et al. 1972).
    """
    from sklearn.isotonic import IsotonicRegression
    v = np.asarray(values, dtype=float); w = np.asarray(weights, dtype=float); x = np.arange(len(v))
    total = float((w * (v - np.average(v, weights=w)) ** 2).sum())
    if len(v) < 3 or total <= 0: return None
    best = min(float((w * (v - IsotonicRegression(increasing=up).fit(x, v, sample_weight=w).predict(x)) ** 2).sum()) for up in (True, False))
    return 1 - best / total
