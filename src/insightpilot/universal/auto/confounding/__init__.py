"""Confounding (Simpson's paradox) and interaction screens.

Confounding: each top field is reduced to one comparison (its most distinct level vs the rest, or above vs
at-or-below its median) and stratified by each of the next fields. Binary targets use the Cochran–Mantel–Haenszel
common odds ratio (Mantel & Haenszel 1959); numeric targets use an inverse-variance-style weighted mean difference.
Interactions: for each pair of top fields, cross-validated logistic (or ridge) models with and without the pair's
joint levels are compared; only gains clearly above fold noise are reported.
"""
from dataclasses import dataclass, field
from itertools import combinations
import math
import warnings

import numpy as np
import pandas as pd

from ....datasets import quote
from ...profile import numeric_expression
from ...store import literal


@dataclass
class ConfoundingCheck:
    field: str
    comparison: str
    stratifier: str
    measure: str
    crude: float | None
    stratified: float | None
    low: float | None
    high: float | None
    p_value: float | None
    homogeneity_p: float | None
    strata_used: int
    flag: str
    strata: list = field(default_factory=list)   # [stratum, rows exposed, rows other, effect in stratum]


def exposure(a, spec, name, kind, cfg, absent_sql):
    """(SQL condition, description) for the field's single comparison, chosen from all rows without the target's sign."""
    if kind == 'numeric':
        x = numeric_expression(name)
        median = a.run(f'SELECT median({x}) FROM selected WHERE {spec.ok}', f'Median of {name}')['rows'][0][0]
        if median is None: return None, None, None
        return f'{x} > {float(median)!r}', f'{name} > {float(median):,.4g} (median) vs at or below', f'{x} IS NOT NULL'
    y = spec.positive_flag if spec.kind == 'binary' else spec.value
    value = f'trim(CAST({quote(name)} AS VARCHAR))'
    query = a.run(f'''SELECT {value} AS level,count(*) AS n,avg({y}) AS m FROM selected WHERE {spec.ok} AND NOT {absent_sql(name)}
        GROUP BY 1 HAVING count(*)>={cfg.small_group_rows} ORDER BY 1''', f'Levels of {name} for the confounding screen')
    if len(query['rows']) < 2: return None, None, None
    overall = sum(n * m for _, n, m in query['rows']) / sum(n for _, n, _ in query['rows'])
    level = max(query['rows'], key=lambda r: (abs(r[2] - overall), r[0]))[0]
    return f'{value}={literal(level)}', f'{name} = {level} vs other levels', f'NOT {absent_sql(name)}'


def stratum_sql(a, spec, name, kind, absent_sql):
    if kind == 'numeric':
        x = numeric_expression(name)
        edges = a.run(f'SELECT quantile_cont({x},[0.25,0.5,0.75]) FROM selected WHERE {spec.ok}', f'Quartiles of {name}')['rows'][0][0] or []
        edges = sorted({float(e) for e in edges if e is not None})
        position = ' + '.join(f'CASE WHEN {x} > {e!r} THEN 1 ELSE 0 END' for e in edges) or '0'
        labels = [f'≤ {edges[0]:,.4g}'] + [f'({edges[i]:,.4g}, {edges[i + 1]:,.4g}]' for i in range(len(edges) - 1)] + [f'> {edges[-1]:,.4g}'] if edges else ['all']
        cases = ' '.join(f'WHEN {position} = {i} THEN {literal(label)}' for i, label in enumerate(labels))
        return f"CASE WHEN {x} IS NULL THEN '(missing)' ELSE CASE {cases} END END"
    value = f'trim(CAST({quote(name)} AS VARCHAR))'
    top = a.run(f'SELECT {value} FROM selected WHERE {spec.ok} AND NOT {absent_sql(name)} GROUP BY 1 ORDER BY count(*) DESC,1 LIMIT 5',
                f'Most frequent levels of {name}')['rows']
    listed = ','.join(literal(r[0]) for r in top) or 'NULL'
    return f"CASE WHEN {absent_sql(name)} THEN '(missing)' WHEN {value} IN ({listed}) THEN {value} ELSE '(other)' END"


def screen(a, spec, ranked, kinds, cfg, absent_sql, applicable):
    """Top cfg.confounding_fields fields, each stratified by the cfg.confounding_strata next-ranked other fields."""
    from statsmodels.stats.contingency_tables import StratifiedTable
    checks = []; queries = []
    if spec.kind == 'multiclass': return checks, queries, 'Not computed for multiclass targets.'
    fields = ranked[:cfg.confounding_fields]
    for name in fields:
        target_spec = spec.restricted(applicable[name]) if name in applicable else spec
        condition, comparison, valid = exposure(a, target_spec, name, kinds[name], cfg, absent_sql)
        if not condition: continue
        strata_fields = [f for f in ranked if f != name][:cfg.confounding_strata]
        for stratifier in strata_fields:
            group = stratum_sql(a, target_spec, stratifier, kinds[stratifier], absent_sql)
            if spec.kind == 'binary':
                query = a.run(f'''SELECT {group} AS s,CASE WHEN {condition} THEN 1 ELSE 0 END AS e,{spec.positive_flag} AS y,count(*) AS n
                    FROM selected WHERE {target_spec.ok} AND {valid} GROUP BY 1,2,3''', f'{name} × {stratifier} × target')
                queries.append(query)
                cells = {}
                for s, e, y, n in query['rows']: cells.setdefault(s, np.zeros((2, 2)))[1 - e, 1 - y] += n
                crude_table = sum(cells.values())
                crude = odds_ratio(crude_table)
                informative = {s: t for s, t in cells.items() if t.sum(axis=1).all() and t.sum(axis=0).all()}
                if len(informative) < 2 or crude is None: continue
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore')
                    st = StratifiedTable([informative[s] for s in sorted(informative)])
                    pooled = float(st.oddsratio_pooled); low, high = (float(v) for v in st.oddsratio_pooled_confint())
                    p_value = float(st.test_null_odds().pvalue)
                    homogeneity = float(st.test_equal_odds().pvalue)
                crude_low, crude_high = crude_interval(crude_table)
                flag = change_flag(math.log(crude), math.log(pooled), crude_low > 1 or crude_high < 1, low > 1 or high < 1)
                strata = [[s, int(t[0].sum()), int(t[1].sum()), odds_ratio(t)] for s, t in sorted(informative.items())]
                checks.append(ConfoundingCheck(name, comparison, stratifier, 'odds ratio', crude, pooled, low, high, p_value, homogeneity,
                                               len(informative), flag, strata))
            else:
                query = a.run(f'''SELECT {group} AS s,CASE WHEN {condition} THEN 1 ELSE 0 END AS e,count(*) AS n,avg({spec.value}) AS m,
                    var_samp({spec.value}) AS v FROM selected WHERE {target_spec.ok} AND {valid} GROUP BY 1,2''', f'{name} × {stratifier} × target')
                queries.append(query)
                groups = {}
                for s, e, n, m, v in query['rows']: groups.setdefault(s, {})[e] = (n, m, v or 0.0)
                totals = {e: [0, 0.0] for e in (0, 1)}
                for s, g in groups.items():
                    for e, (n, m, _) in g.items(): totals[e][0] += n; totals[e][1] += n * m
                if not totals[0][0] or not totals[1][0]: continue
                crude = totals[1][1] / totals[1][0] - totals[0][1] / totals[0][0]
                usable = {s: g for s, g in groups.items() if 0 in g and 1 in g and g[0][0] > 1 and g[1][0] > 1}
                if len(usable) < 2: continue
                weights = {s: g[1][0] * g[0][0] / (g[1][0] + g[0][0]) for s, g in usable.items()}
                total_weight = sum(weights.values())
                pooled = sum(weights[s] * (g[1][1] - g[0][1]) for s, g in usable.items()) / total_weight
                se = math.sqrt(sum(weights[s] ** 2 * (g[1][2] / g[1][0] + g[0][2] / g[0][0]) for s, g in usable.items())) / total_weight
                low, high = pooled - 1.96 * se, pooled + 1.96 * se
                from scipy import stats as scipy_stats
                p_value = float(2 * scipy_stats.norm.sf(abs(pooled / se))) if se else None
                strata = [[s, g[1][0], g[0][0], g[1][1] - g[0][1]] for s, g in sorted(usable.items())]
                flag = change_flag(crude, pooled, True, low > 0 or high < 0, ratio_scale=False)
                checks.append(ConfoundingCheck(name, comparison, stratifier, 'difference in means', crude, pooled, low, high, p_value, None,
                                               len(usable), flag, strata))
    return checks, queries, ''


def odds_ratio(table):
    """(a·d)/(b·c) with Haldane's 0.5 correction when a cell is zero."""
    a_, b_, c_, d_ = (float(v) for v in np.asarray(table).ravel())
    if min(a_, b_, c_, d_) == 0: a_, b_, c_, d_ = a_ + .5, b_ + .5, c_ + .5, d_ + .5
    return (a_ * d_) / (b_ * c_) if b_ * c_ else None


def crude_interval(table):
    a_, b_, c_, d_ = (float(v) + (0.5 if min(np.asarray(table).ravel()) == 0 else 0) for v in np.asarray(table).ravel())
    se = math.sqrt(1 / a_ + 1 / b_ + 1 / c_ + 1 / d_); log_or = math.log(a_ * d_ / (b_ * c_))
    return math.exp(log_or - 1.96 * se), math.exp(log_or + 1.96 * se)


def change_flag(crude, stratified, crude_clear, stratified_clear, ratio_scale=True):
    """'reverses' when the signs differ and the stratified interval excludes no effect; 'drops by more than half' when
    the stratified effect is under half the crude one (log scale for odds ratios) and the crude effect was clear."""
    if crude_clear and stratified_clear and crude * stratified < 0: return 'reverses'
    if crude_clear and abs(stratified) < 0.5 * abs(crude): return 'drops by more than half'
    return ''


# ---------------- interactions ----------------
def bins(values, numeric, count):
    """Category labels: quintile bins for numbers, the most frequent levels for categories; missing is its own label."""
    if numeric:
        x = pd.to_numeric(values, errors='coerce')
        edges = np.unique(np.quantile(x.dropna(), np.linspace(0, 1, 6))) if x.notna().any() else np.array([])
        index = np.clip(np.searchsorted(edges, x, side='left') - 1, 0, max(len(edges) - 2, 0)) if len(edges) > 1 else np.zeros(len(x), int)
        labels = pd.Series([f'bin {i + 1}' for i in index], index=values.index)
        return labels.where(x.notna(), '(missing)')
    text = values.where(values.notna(), '(missing)').astype(str)
    counts = text.value_counts()
    keep = set(sorted(counts.index, key=lambda v: (-counts[v], v))[:count])
    return text.where(text.isin(keep), '(other)')


def interaction_screen(raw, y, kind, fields, numeric, cfg, time_ordered=False):
    """Δ = mean over folds of (score with joint levels − score with separate levels) for each pair of fields."""
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.metrics import r2_score, roc_auc_score
    from sklearn.model_selection import KFold, StratifiedKFold, TimeSeriesSplit
    if kind == 'multiclass': return [], 'Not computed for multiclass targets.'
    encoded = {name: bins(raw[name], name in numeric, cfg.interaction_levels) for name in fields}
    # Rows arrive in time order when a time axis exists: each fold then trains on earlier rows only.
    folds = list(TimeSeriesSplit(n_splits=cfg.cv_folds).split(raw)) if time_ordered else list((StratifiedKFold(cfg.cv_folds, shuffle=True, random_state=cfg.seed).split(raw, y) if kind == 'binary'
                  else KFold(cfg.cv_folds, shuffle=True, random_state=cfg.seed).split(raw)))
    rows = []
    for first, second in combinations(fields, 2):
        main = pd.get_dummies(pd.DataFrame({first: encoded[first], second: encoded[second]}), dtype=float)
        joint = pd.get_dummies(encoded[first] + ' × ' + encoded[second], dtype=float)
        gains = []
        for train, test in folds:
            scores = []
            for X in (main, joint):
                if kind == 'binary':
                    fitted = LogisticRegression(C=1.0, max_iter=1000).fit(X.iloc[train], y[train])
                    scores.append(roc_auc_score(y[test], fitted.predict_proba(X.iloc[test])[:, 1]))
                else:
                    fitted = Ridge(alpha=1.0).fit(X.iloc[train], y[train]); scores.append(r2_score(y[test], fitted.predict(X.iloc[test])))
            gains.append(scores[1] - scores[0])
        mean = float(np.mean(gains)); se = float(np.std(gains, ddof=1) / math.sqrt(len(gains)))
        rows.append([first, second, mean, se, False, interaction_lr(kind, y, encoded[first], encoded[second]), None])
    from ...auto_stats import benjamini_hochberg
    adjusted = benjamini_hochberg([r[5] for r in rows])
    for row, q in zip(rows, adjusted):
        row[6] = q
        row[4] = q is not None and q < 0.05 and row[2] >= cfg.interaction_minimum and row[2] > 2 * row[3]
    rows.sort(key=lambda r: (not r[4], -r[2], r[0], r[1]))
    return rows, ''


def interaction_lr(kind, y, first, second):
    """Likelihood-ratio test of joint levels against separate levels (nested models, in-sample):
    LR = 2·(log L_joint − log L_main), χ² with df = difference in model rank. Each design drops one reference level
    per factor. None when either model cannot be fitted or does not converge."""
    import statsmodels.api as sm
    from scipy import stats as scipy_stats
    main = pd.get_dummies(pd.DataFrame({'a': first.to_numpy(), 'b': second.to_numpy()}), drop_first=True, dtype=float)
    joint = pd.get_dummies(pd.Series((first + ' × ' + second).to_numpy()), drop_first=True, dtype=float)
    fits = []
    for design in (main, joint):
        exog = sm.add_constant(design, has_constant='add')
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            try:
                fitted = sm.GLM(y, exog, family=sm.families.Binomial()).fit() if kind == 'binary' else sm.OLS(y, exog).fit()
                llf = float(fitted.llf)
            except Exception:
                return None
        # A fit that did not converge (for example, a joint level that is all one outcome) gives no valid test.
        if (kind == 'binary' and not getattr(fitted, 'converged', True)) or not math.isfinite(llf): return None
        fits.append((llf, np.linalg.matrix_rank(exog.to_numpy())))
    (l_main, k_main), (l_joint, k_joint) = fits
    df = k_joint - k_main
    if df <= 0: return None
    return float(scipy_stats.chi2.sf(max(0.0, 2 * (l_joint - l_main)), df))


def two_way_table(a, spec, first, second, kinds, absent_sql):
    groups = [stratum_sql(a, spec, name, kinds[name], absent_sql) for name in (first, second)]
    measure = spec.positive_flag if spec.kind == 'binary' else spec.value
    return a.run(f'SELECT {groups[0]} AS a,{groups[1]} AS b,count(*) AS n,avg({measure}) AS m FROM selected WHERE {spec.ok} GROUP BY 1,2 ORDER BY 1,2',
                 f'{first} × {second}: target by joint levels')
