"""Segments: readable rules from a shallow decision tree, validated on held-out rows.

The tree is grown on four of five seeded folds of the model sample; every leaf is then checked on the
fifth (held-out) fold. A segment survives when its held-out lift is at least cfg.segment_lift_minimum (or at
most its inverse, for low-risk segments) and its held-out 95% interval excludes the overall rate. Each surviving
rule is translated to SQL, so its size and rate are also reported on every row of the table.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ....datasets import quote
from ...profile import numeric_expression
from ...store import literal
from ..stats import wilson_interval


@dataclass
class Segment:
    rule: str
    conditions: list                     # [(field, operator, value)]
    sql: str
    train_rows: int
    train_rate: float
    held_out_rows: int
    held_out_rate: float
    held_out_low: float | None
    held_out_high: float | None
    held_out_lift: float | None
    rows: int = 0                        # full table
    share_of_rows: float = 0.0
    rate: float | None = None
    low: float | None = None
    high: float | None = None
    lift: float | None = None
    share_of_target: float | None = None
    impact: float = 0.0
    direction: str = ''
    kept: bool = False
    reason: str = ''


def encode(raw, fields, numeric, cfg, joint=()):
    """Numbers stay numbers (missing allowed); categories become one indicator per frequent level, plus (missing).

    Reported interactions between two categorical fields add one indicator per joint level, so the tree can
    find segments that depend on a combination even when neither field has an effect on its own.
    """
    columns = {}; meta = {}
    texts = {name: raw[name].where(raw[name].notna(), '(missing)').astype(str) for name in fields if name not in numeric}
    for first, second in joint:
        if first not in texts or second not in texts: continue
        combined = texts[first] + '\x1e' + texts[second]
        counts = combined.value_counts()
        for level in sorted(counts.index, key=lambda v: (-counts[v], v))[:cfg.model_level_cap]:
            left, right = level.split('\x1e')
            key = f'{first}\x1f{second}\x1f{level}'; columns[key] = (combined == level).astype(float)
            meta[key] = ((first, second), 'joint', (left, right))
    for name in fields:
        if name in numeric:
            columns[name] = pd.to_numeric(raw[name], errors='coerce').astype(float); meta[name] = (name, 'number', None)
        else:
            text = raw[name].where(raw[name].notna(), '(missing)').astype(str)
            counts = text.value_counts()
            for level in sorted(counts.index, key=lambda v: (-counts[v], v))[:cfg.model_level_cap]:
                key = f'{name}\x1f{level}'; columns[key] = (text == level).astype(float); meta[key] = (name, 'level', level)
    return pd.DataFrame(columns), meta


def leaves(tree, X, meta, pruned=frozenset()):
    """Each leaf's conditions, from the root down. 'Or missing' is stated only for fields that had missing values."""
    t = tree.tree_; output = {}; has_missing = set(X.columns[X.isna().any()])

    def walk(node, conditions):
        if t.children_left[node] == t.children_right[node] or node in pruned:
            output[node] = conditions; return
        column = X.columns[t.feature[node]]; threshold = float(t.threshold[node])
        name, kind, level = meta[column]
        missing_left = bool(t.missing_go_to_left[node]) if column in has_missing and hasattr(t, 'missing_go_to_left') else False
        if kind == 'level':
            walk(t.children_left[node], conditions + [(name, '≠', level)])
            walk(t.children_right[node], conditions + [(name, '=', level)])
        elif kind == 'joint':
            walk(t.children_left[node], conditions + [(name, 'not both', level)])
            walk(t.children_right[node], conditions + [(name, 'both', level)])
        else:
            walk(t.children_left[node], conditions + [(name, '≤', threshold, missing_left)])
            walk(t.children_right[node], conditions + [(name, '>', threshold, column in has_missing and not missing_left)])
    walk(0, [])
    return output


def simplify(conditions):
    """Merge repeated numeric bounds on one field; a field fixed to a level drops its ≠ conditions."""
    merged = []; bounds = {}
    for condition in conditions:
        if len(condition) == 4:
            name, operator, value, missing = condition
            key = (name, operator); current = bounds.get(key)
            if current is None or (operator == '≤' and value < current[0]) or (operator == '>' and value > current[0]):
                bounds[key] = (value, missing if current is None else (missing and current[1]))
        else:
            merged.append(condition)
    equal = {c[0] for c in merged if c[1] == '='}
    merged = [c for c in merged if not (c[1] == '≠' and c[0] in equal)]
    # A pair fixed to one joint level makes "not both" conditions on that pair redundant.
    pinned = {c[0] for c in merged if c[1] == 'both'}
    merged = [c for c in merged if not (c[1] == 'not both' and c[0] in pinned)]
    for (name, operator), (value, missing) in sorted(bounds.items()): merged.append((name, operator, value, missing))
    return merged


def describe(conditions):
    parts = []; excluded = {}
    for c in conditions:
        if len(c) == 3 and c[1] == '≠': excluded.setdefault(c[0], []).append(c[2])
    for c in conditions:
        if len(c) == 4:
            name, operator, value, missing = c
            parts.append(f'{name} {operator} {value:,.4g}' + (' (or missing)' if missing else ''))
        elif c[1] in ('both', 'not both'):
            (first, second), (left, right) = c[0], c[2]
            joint = f'{first} = {left} AND {second} = {right}'
            parts.append(joint if c[1] == 'both' else f'NOT ({joint})')
        elif c[1] == '≠':
            if c[0] in excluded:
                levels = excluded.pop(c[0])
                parts.append(f'{c[0]} ≠ {levels[0]}' if len(levels) == 1 else f'{c[0]} not ' + ', '.join(levels[:-1]) + f' or {levels[-1]}')
        else:
            parts.append(f'{c[0]} {c[1]} {c[2]}')
    return ' AND '.join(parts) or 'all rows'


def to_sql(conditions, absent_sql):
    parts = []
    for c in conditions:
        if len(c) == 4:
            name, operator, value, missing = c
            x = numeric_expression(name); test = f'{x} {"<=" if operator == "≤" else ">"} {float(value)!r}'
            parts.append(f'({test} OR {x} IS NULL)' if missing else f'({test})')
        elif c[1] in ('both', 'not both'):
            (first, second), (left, right) = c[0], c[2]
            test = '(' + level_sql(first, left, absent_sql) + ' AND ' + level_sql(second, right, absent_sql) + ')'
            parts.append(test if c[1] == 'both' else f'(NOT {test})')
        else:
            name, operator, level = c
            if level == '(missing)': test = absent_sql(name)
            else: test = f'(NOT {absent_sql(name)} AND trim(CAST({quote(name)} AS VARCHAR)) = {literal(level)})'
            parts.append(test if operator == '=' else f'(NOT {test})')
    return ' AND '.join(parts) or 'true'


def level_sql(name, level, absent_sql):
    if level == '(missing)': return absent_sql(name)
    return f'(NOT {absent_sql(name)} AND trim(CAST({quote(name)} AS VARCHAR)) = {literal(level)})'


def prune(tree, y_train, X_train, kind, alpha=0.001):
    """Nodes whose two children do not differ on the training rows (χ² or Welch t, p ≥ alpha) become leaves.

    Applied bottom-up until nothing changes, so splits that only fit noise are removed before validation.
    """
    from scipy import stats as scipy_stats
    t = tree.tree_; pruned = set()
    node_rows = {}
    indicator = tree.decision_path(X_train)
    for node in range(t.node_count):
        node_rows[node] = indicator[:, node].nonzero()[0]

    def is_leaf(node):
        return t.children_left[node] == t.children_right[node] or node in pruned
    changed = True
    while changed:
        changed = False
        for node in range(t.node_count):
            if is_leaf(node): continue
            left, right = t.children_left[node], t.children_right[node]
            if not (is_leaf(left) and is_leaf(right)): continue
            a_, b_ = y_train[node_rows[left]], y_train[node_rows[right]]
            if kind == 'binary':
                table = np.array([[a_.sum(), len(a_) - a_.sum()], [b_.sum(), len(b_) - b_.sum()]])
                p = scipy_stats.chi2_contingency(table, correction=False)[1] if table.sum(axis=0).all() else 1.0
            else:
                p = scipy_stats.ttest_ind(a_, b_, equal_var=False).pvalue if len(a_) > 1 and len(b_) > 1 else 1.0
            if not p < alpha:
                pruned.add(node); changed = True
    return pruned, node_rows


def discover(a, spec, raw, y, fields, numeric, cfg, absent_sql, joint=()):
    from sklearn.model_selection import KFold, StratifiedKFold
    from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor
    if spec.kind == 'multiclass': return [], 'Not computed for multiclass targets.'
    if not fields: return [], 'No fields are available for segments.'
    X, meta = encode(raw, fields, numeric, cfg, joint)
    n = len(y)
    splitter = (StratifiedKFold(cfg.cv_folds, shuffle=True, random_state=cfg.seed).split(X, y) if spec.kind == 'binary'
                else KFold(cfg.cv_folds, shuffle=True, random_state=cfg.seed).split(X))
    train, held_out = next(iter(splitter))
    min_leaf = max(cfg.segment_min_rows, int(cfg.segment_min_share * len(train)))
    if len(train) < 2 * min_leaf: return [], f'Too few rows for segments (at least {2 * min_leaf:,} needed for training).'
    options = dict(max_depth=cfg.segment_depth, min_samples_leaf=min_leaf, random_state=cfg.seed)
    tree = (DecisionTreeClassifier(**options) if spec.kind == 'binary' else DecisionTreeRegressor(**options)).fit(X.iloc[train], y[train])
    overall_train = float(np.mean(y[train])); overall_held = float(np.mean(y[held_out]))
    pruned, _ = prune(tree, y[train], X.iloc[train], spec.kind)
    held_path = tree.decision_path(X.iloc[held_out]); train_path = tree.decision_path(X.iloc[train])
    segments = []
    for leaf, conditions in leaves(tree, X, meta, pruned).items():
        conditions = simplify(conditions)
        in_train = y[train][train_path[:, leaf].nonzero()[0]]; in_held = y[held_out][held_path[:, leaf].nonzero()[0]]
        if not len(in_held): continue
        if spec.kind == 'binary':
            low, high = wilson_interval(int(in_held.sum()), len(in_held), cfg.confidence)
        else:
            se = in_held.std(ddof=1) / np.sqrt(len(in_held)) if len(in_held) > 1 else None
            low, high = (in_held.mean() - 1.96 * se, in_held.mean() + 1.96 * se) if se else (None, None)
        lift = float(in_held.mean() / overall_held) if overall_held else None
        segments.append(Segment(describe(conditions), conditions, to_sql(conditions, absent_sql), len(in_train), float(in_train.mean()),
                                len(in_held), float(in_held.mean()), low, high, lift))
    for s in segments:
        high_risk = s.held_out_lift is not None and s.held_out_lift >= cfg.segment_lift_minimum
        low_risk = s.held_out_lift is not None and s.held_out_lift <= 1 / cfg.segment_lift_minimum
        excludes = s.held_out_low is not None and (s.held_out_low > overall_held or s.held_out_high < overall_held)
        s.direction = 'higher' if (s.held_out_lift or 1) > 1 else 'lower'
        enough = s.held_out_rows >= cfg.small_group_rows
        s.kept = (high_risk or low_risk) and excludes and enough
        s.reason = ('kept' if s.kept else f'fewer than {cfg.small_group_rows} held-out rows' if not enough else
                    'held-out lift too close to 1' if not (high_risk or low_risk) else 'held-out interval includes the overall rate')
    return segments, f'Tree grown on {len(train):,} rows; checked on {len(held_out):,} held-out rows (seed {cfg.seed}).'


def full_data(a, spec, segments):
    """Size, rate, interval, lift and share of the target for each rule on every row of the table."""
    measure = spec.positive_flag if spec.kind == 'binary' else spec.value
    total = a.run(f'SELECT count(*),avg({measure}),sum({measure}) FROM selected WHERE {spec.ok}', 'Overall target')
    rows, overall, target_sum = total['rows'][0]; queries = [total]
    for s in segments:
        query = a.run(f'SELECT count(*),avg({measure}),sum({measure}),stddev_samp({measure}) FROM selected WHERE {spec.ok} AND {s.sql}',
                      f'Segment: {s.rule}')
        queries.append(query)
        n, mean, total_sum, sd = query['rows'][0]
        s.rows, s.share_of_rows, s.rate = n, n / rows if rows else 0, mean
        if spec.kind == 'binary':
            s.low, s.high = wilson_interval(int(round(total_sum or 0)), n, 0.95) if n else (None, None)
        elif n and sd:
            s.low, s.high = mean - 1.96 * sd / np.sqrt(n), mean + 1.96 * sd / np.sqrt(n)
        s.lift = mean / overall if mean is not None and overall else None
        s.share_of_target = (total_sum or 0) / target_sum if target_sum else None
        s.impact = n * abs((mean or 0) - (overall or 0))
    return overall, queries
