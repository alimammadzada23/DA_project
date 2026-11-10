"""Multivariate analysis: which fields keep information when the others are taken into account.

1. Gradient boosting (scikit-learn HistGradientBoosting), cross-validated against a baseline.
2. Permutation importance on held-out folds, per field and per redundancy cluster (grouped permutation).
3. An interpretable adjusted model (statsmodels Logit or OLS) on a non-redundant, VIF-pruned field set.
4. Univariate vs adjusted verdicts, and accumulated local effects (ALE) for the top numeric fields.

Models use a seeded sample (disclosed). Nothing here changes data; missing values are handled inside
the models only (natively by boosting; median fill plus an indicator in the adjusted model).
"""
from dataclasses import dataclass, field
import math
import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import (accuracy_score, average_precision_score, brier_score_loss, log_loss, mean_absolute_error, r2_score,
                             roc_auc_score)
from sklearn.model_selection import KFold, StratifiedKFold, TimeSeriesSplit

from ....datasets import quote
from ...profile import date_expression, numeric_expression


@dataclass
class ModelData:
    frame: pd.DataFrame          # encoded features for boosting (categories as float codes, NaN = missing)
    raw: pd.DataFrame            # unencoded values (text categories, numbers) for the adjusted model
    y: np.ndarray
    fields: list
    categorical: list
    total_rows: int
    classes: list = field(default_factory=list)
    time_ordered: bool = False


@dataclass
class MultivariateResult:
    kind: str = ''
    fields: list = field(default_factory=list)
    metrics: list = field(default_factory=list)        # [measure, model mean, model sd, baseline mean, per fold]
    no_signal: bool = False
    signal: float | None = None
    importance: list = field(default_factory=list)     # [group, members, mean drop, sd, n]
    regression: list = field(default_factory=list)     # [field, term, estimate, low, high, p]
    regression_note: str = ''
    dropped_vif: list = field(default_factory=list)
    verdicts: list = field(default_factory=list)
    ale: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    sample_rows: int = 0
    total_rows: int = 0


def load(a, spec, fields, facts, cfg, time_axis, absent_sql, text_value):
    numeric = [n for n in fields if facts[n]['type'] == 'numeric']
    categorical = [n for n in fields if n not in numeric]
    parts = [numeric_expression(n) + f' AS {quote(n)}' if n in numeric else f'CASE WHEN {absent_sql(n)} THEN NULL ELSE {text_value(n)} END AS {quote(n)}'
             for n in fields]
    target = spec.positive_flag if spec.kind == 'binary' else spec.label if spec.kind == 'multiclass' else spec.value
    parts.append(f'{target} AS "__target"')
    if time_axis: parts.append(f'{date_expression(time_axis)} AS "__time"')
    data, evidence = a.frame(f'SELECT {",".join(parts)} FROM selected WHERE {spec.ok} ORDER BY hash(_row_id + {cfg.seed}),_row_id '
                             f'LIMIT {cfg.model_sample_rows}', 'Seeded sample for the multivariate model')
    total = a.run(f'SELECT count(*) FROM selected WHERE {spec.ok}', 'Rows with a target value')['rows'][0][0]
    if time_axis:
        data = data.sort_values('__time', kind='stable', na_position='last').reset_index(drop=True)
    encoded = pd.DataFrame(index=data.index)
    for n in fields:
        if n in numeric:
            encoded[n] = pd.to_numeric(data[n], errors='coerce').astype(float)
        else:
            counts = data[n].value_counts()
            top = sorted(counts.index, key=lambda v: (-counts[v], str(v)))[:cfg.model_level_cap]
            mapping = {v: float(i) for i, v in enumerate(top)}
            encoded[n] = data[n].map(lambda v: np.nan if v is None or v != v else mapping.get(v, float(len(top))))
    y = data['__target'].to_numpy()
    if spec.kind == 'numeric': y = y.astype(float)
    elif spec.kind == 'binary': y = y.astype(int)
    else: y = y.astype(str)
    classes = sorted(set(y.tolist()), key=str) if spec.kind != 'numeric' else []
    return ModelData(encoded, data[fields], y, fields, categorical, total, classes, bool(time_axis)), evidence


def splits(data, kind, cfg):
    if data.time_ordered: return list(TimeSeriesSplit(n_splits=cfg.cv_folds).split(data.frame))
    if kind != 'numeric' and min(np.unique(data.y, return_counts=True)[1]) >= cfg.cv_folds:
        return list(StratifiedKFold(cfg.cv_folds, shuffle=True, random_state=cfg.seed).split(data.frame, data.y))
    return list(KFold(cfg.cv_folds, shuffle=True, random_state=cfg.seed).split(data.frame))


def model(kind, data, cfg):
    mask = [n in data.categorical for n in data.fields]
    options = dict(max_iter=cfg.model_iterations, learning_rate=0.1, max_leaf_nodes=31, min_samples_leaf=20, early_stopping=False,
                   random_state=cfg.seed, categorical_features=mask if any(mask) else None)
    return HistGradientBoostingRegressor(**options) if kind == 'numeric' else HistGradientBoostingClassifier(**options)


def score(kind, fitted, X, y, classes):
    if kind == 'numeric': return r2_score(y, fitted.predict(X))
    proba = fitted.predict_proba(X)
    if kind == 'binary': return roc_auc_score(y, proba[:, list(fitted.classes_).index(1)])
    return roc_auc_score(y, proba, multi_class='ovr', average='macro', labels=list(fitted.classes_))


def calibration_error(y, p, bins=10):
    """Expected calibration error: Σ_bins (n_b / n)·|mean prediction − observed rate|, equal-width bins."""
    edges = np.linspace(0, 1, bins + 1); index = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1); total = 0.0
    for b in range(bins):
        inside = index == b
        if inside.any(): total += inside.mean() * abs(p[inside].mean() - y[inside].mean())
    return total


def cross_validate(data, kind, cfg, groups):
    """Fold metrics against the baseline, plus permutation drops for every group of columns."""
    folds = splits(data, kind, cfg); X = data.frame; y = data.y
    per_fold = {}; drops = {name: [] for name in groups}
    for index, (train, test) in enumerate(folds):
        if kind != 'numeric' and len(np.unique(y[test])) < 2: continue
        fitted = model(kind, data, cfg).fit(X.iloc[train], y[train])
        values = {}
        if kind == 'numeric':
            prediction = fitted.predict(X.iloc[test]); baseline = np.full(len(test), np.median(y[train]))
            values['R²'] = (r2_score(y[test], prediction), r2_score(y[test], np.full(len(test), y[train].mean())))
            values['MAE'] = (mean_absolute_error(y[test], prediction), mean_absolute_error(y[test], baseline))
        else:
            proba = fitted.predict_proba(X.iloc[test]); labels = list(fitted.classes_)
            majority = pd.Series(y[train]).value_counts().sort_index(key=lambda s: s.map(str)).idxmax()
            prior = np.tile([np.mean(y[train] == c) for c in labels], (len(test), 1))
            if kind == 'binary':
                p = proba[:, labels.index(1)]
                values['AUC'] = (roc_auc_score(y[test], p), 0.5)
                values['PR-AUC (average precision)'] = (average_precision_score(y[test], p), float(np.mean(y[test])))
                values['Brier score'] = (brier_score_loss(y[test], p), brier_score_loss(y[test], prior[:, labels.index(1)]))
                values['Calibration error (ECE)'] = (calibration_error(y[test], p), calibration_error(y[test], prior[:, labels.index(1)]))
            else:
                values['AUC (macro one-vs-rest)'] = (roc_auc_score(y[test], proba, multi_class='ovr', average='macro', labels=labels), 0.5)
            values['Log loss'] = (log_loss(y[test], proba, labels=labels), log_loss(y[test], prior, labels=labels))
            values['Accuracy'] = (accuracy_score(y[test], np.array(labels)[proba.argmax(axis=1)]), float(np.mean(y[test] == majority)))
        for measure, pair in values.items(): per_fold.setdefault(measure, []).append(pair)
        rows = test[:cfg.permutation_rows]
        base = score(kind, fitted, X.iloc[rows], y[rows], data.classes)
        for repeat in range(cfg.permutation_repeats):
            rng = np.random.default_rng(cfg.seed + 1000 * index + repeat)
            order = rng.permutation(len(rows))
            for name, columns in groups.items():
                shuffled = X.iloc[rows].copy()
                shuffled[columns] = shuffled[columns].to_numpy()[order]
                drops[name].append(base - score(kind, fitted, shuffled, y[rows], data.classes))
    return per_fold, drops, len(folds)


def design(raw, fields, facts, cfg, not_applicable=None, binned=()):
    """Adjusted-model design: standardised numbers (median-filled, plus a missing indicator), one-hot levels with pooling.

    A conditional field's not-applicable level gets no column (its parent field carries it), so the field's
    estimates compare its levels among the rows where it applies. This nested coding avoids exact collinearity.
    """
    not_applicable = not_applicable or {}
    # Numeric fields with a non-linear shape enter as equal-count bins, so a U or threshold is not forced into a line.
    columns = {}; owner = {}; labels = {}
    n = len(raw)
    for name in fields:
        if facts[name]['type'] == 'numeric' and name in binned:
            x = pd.to_numeric(raw[name], errors='coerce').astype(float)
            edges = np.unique(np.quantile(x.dropna(), np.linspace(0, 1, cfg.numeric_bins + 1))) if x.notna().any() else []
            if len(edges) < 3: continue
            index = np.clip(np.searchsorted(edges, x, side='left') - 1, 0, len(edges) - 2)
            values = pd.Series([f'({edges[i]:.4g}, {edges[i + 1]:.4g}]' if i > 0 else f'[{edges[0]:.4g}, {edges[1]:.4g}]' for i in index], index=raw.index)
            values = values.where(x.notna(), '(missing)')
            levels = sorted(values.unique(), key=lambda v: (v == '(missing)', float(v.split(',')[0].strip('([')) if v != '(missing)' else 0))
            reference = levels[0]
            for level in levels[1:]:
                key = f'{name} in {level}'; columns[key] = (values == level).astype(float); owner[key] = name; labels[key] = f'{level} vs {reference}'
        elif facts[name]['type'] == 'numeric':
            x = pd.to_numeric(raw[name], errors='coerce').astype(float)
            sd = x.std()
            if not sd or not math.isfinite(sd): continue
            z = (x - x.mean()) / sd
            columns[f'{name}'] = z.fillna(0.0); owner[name] = name; labels[name] = 'per 1 standard deviation'
            if x.isna().any():
                key = f'{name} (missing)'; columns[key] = x.isna().astype(float); owner[key] = name; labels[key] = 'missing vs present'
        else:
            values = raw[name].where(raw[name].notna(), '(missing)').astype(str)
            counts = values.value_counts()
            keep = {v for v, c in counts.items() if c >= max(cfg.small_group_rows, cfg.rare_level_share * n)}
            excluded = not_applicable.get(name)
            values = values.where(values.isin(keep) | (values == excluded), '(other)')
            counts = values.value_counts()
            applicable = [v for v in counts.index if v != excluded]
            if len(applicable) < 2: continue
            reference = sorted(applicable, key=lambda v: (-counts[v], v))[0]
            for level in sorted(applicable):
                if level == reference: continue
                key = f'{name} = {level}'; columns[key] = (values == level).astype(float); owner[key] = name
                labels[key] = f'{level} vs {reference}'
    return pd.DataFrame(columns), owner, labels


def vif(corr):
    """VIF_j = 1 / (1 − R²_j), with R²_j = r_jᵀ R₋ⱼ⁺ r_j from the correlation matrix; exact collinearity gives infinity."""
    values = []
    for j in range(len(corr)):
        others = [k for k in range(len(corr)) if k != j]
        r = corr[others, j]
        r2 = float(r @ np.linalg.pinv(corr[np.ix_(others, others)]) @ r) if others else 0.0
        values.append(math.inf if r2 >= 1 - 1e-10 else 1 / (1 - r2))
    return values


def vif_prune(X, owner, maximum):
    """Drop whole fields, highest VIF first, until every column's VIF ≤ maximum."""
    dropped = []
    while X.shape[1] > 1:
        corr = np.corrcoef(X.to_numpy(), rowvar=False)
        corr = np.nan_to_num(corr, nan=0.0); np.fill_diagonal(corr, 1.0)
        vif_values = vif(corr)
        by_field = {}
        for column, value in zip(X.columns, vif_values): by_field[owner[column]] = max(by_field.get(owner[column], 0), float(value))
        worst = max(by_field, key=lambda f: (by_field[f], f))
        if by_field[worst] <= maximum: break
        dropped.append([worst, by_field[worst]])
        X = X[[c for c in X.columns if owner[c] != worst]]
    return X, dropped


def fit_regression(kind, y, X):
    import statsmodels.api as sm
    exog = sm.add_constant(X, has_constant='add')
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        fitted = sm.Logit(y, exog).fit(disp=0, maxiter=200) if kind == 'binary' else sm.OLS(y, exog).fit(cov_type='HC3')
    if kind == 'binary' and not fitted.mle_retvals.get('converged', True):
        raise ValueError('did not converge (possible separation)')
    return fitted


def adjusted_model(data, kind, fields, facts, cfg, not_applicable=None, binned=()):
    """Returns (rows, VIF-dropped fields, note, fitted model, design, column owners) for binary and numeric targets."""
    if kind == 'multiclass':
        return [], [], 'Not computed for multiclass targets; see the per-class comparisons in each field section.', None, None, None
    X, owner, labels = design(data.raw, fields, facts, cfg, not_applicable, binned)
    if X.empty: return [], [], 'No usable fields for the adjusted model.', None, None, None
    X, dropped = vif_prune(X, owner, cfg.vif_maximum)
    try:
        fitted = fit_regression(kind, data.y, X)
    except Exception as exc:
        return [], dropped, f'The adjusted model could not be estimated ({type(exc).__name__}: {exc}).', None, None, None
    intervals = fitted.conf_int(); rows = []
    for column in X.columns:
        estimate, (low, high), p = fitted.params[column], intervals.loc[column], fitted.pvalues[column]
        if kind == 'binary': estimate, low, high = (float(np.exp(min(v, 709.0))) for v in (estimate, low, high))
        rows.append([owner[column], labels[column], estimate, low, high, float(p)])
    note = ('Odds ratios from a logistic regression' if kind == 'binary' else 'Coefficients from a linear regression with HC3 standard errors')
    note += (', holding the other listed fields constant. Numbers are standardised (per 1 SD); categories are compared with their most '
             'common level among rows where they apply; rare levels are pooled; missing numbers are median-filled with a missing indicator. '
             'Associations, not causes.')
    return rows, dropped, note, fitted, X, owner


def lr_comparison(kind, y, X, owner, fitted, name):
    """Likelihood-ratio contribution of one field alone (vs the null model) and adjusted (full vs full without it).

    LR = 2·(log L_bigger − log L_smaller), χ² with df = the field's number of columns. Unlike AUC gains, these do
    not shrink merely because other, unrelated fields are present, so a smaller adjusted value reflects overlap.
    """
    from scipy import stats as scipy_stats
    columns = [c for c in X.columns if owner[c] == name]
    if not columns: return None
    alone = fit_regression(kind, y, X[columns])
    null = fit_regression(kind, y, X.iloc[:, :0])
    rest = [c for c in X.columns if owner[c] != name]
    reduced = fit_regression(kind, y, X[rest]) if rest else null
    univariate = 2 * (alone.llf - null.llf); adjusted = 2 * (fitted.llf - reduced.llf); df = len(columns)
    def sign(model):
        if len(columns) != 1 or not owner[columns[0]] == columns[0]: return ''
        low, high = model.conf_int().loc[columns[0]]
        return '+' if low > 0 else '−' if high < 0 else '0'
    return {'univariate': max(univariate, 0.0), 'adjusted': max(adjusted, 0.0), 'df': df,
            'p_univariate': float(scipy_stats.chi2.sf(univariate, df)), 'p_adjusted': float(scipy_stats.chi2.sf(adjusted, df)),
            'sign_univariate': sign(alone), 'sign_adjusted': sign(fitted)}


def lr_verdict(c, cfg):
    if c['sign_univariate'] in '+−' and c['sign_adjusted'] in '+−' and c['sign_univariate'] and c['sign_adjusted'] and c['sign_univariate'] != c['sign_adjusted']:
        return 'reverses'
    if c['p_univariate'] >= 0.05: return 'no univariate signal'
    if c['p_adjusted'] >= 0.05: return 'disappears'
    # χ² grows with the square of an effect, so the effect retained is the square root of the χ² ratio.
    if retained(c) < cfg.weakens_share: return 'weakens substantially (>50%)'
    return 'holds'


def retained(c):
    return math.sqrt(c['adjusted'] / c['univariate']) if c['univariate'] else None


def ale(fitted, data, name, kind, cfg):
    """First-order accumulated local effects (Apley & Zhu 2020) on equal-count intervals, centred to mean zero."""
    X = data.frame.iloc[:cfg.permutation_rows]; x = X[name].to_numpy()
    present = ~np.isnan(x)
    if present.sum() < 20: return None
    edges = np.unique(np.quantile(x[present], np.linspace(0, 1, cfg.ale_bins + 1)))
    if len(edges) < 3: return None
    effects = []; counts = []
    predict = (lambda frame: fitted.predict(frame)) if kind == 'numeric' else \
        (lambda frame: fitted.predict_proba(frame)[:, list(fitted.classes_).index(1)])
    for low, high in zip(edges[:-1], edges[1:]):
        inside = present & (x >= low) & (x <= high) if low == edges[0] else present & (x > low) & (x <= high)
        if not inside.any(): effects.append(0.0); counts.append(0); continue
        lower = X[inside].copy(); upper = X[inside].copy(); lower[name] = low; upper[name] = high
        effects.append(float(np.mean(predict(upper) - predict(lower)))); counts.append(int(inside.sum()))
    accumulated = np.cumsum(effects)
    centre = np.average(accumulated, weights=counts) if sum(counts) else 0.0
    return {'upper_edges': [float(e) for e in edges[1:]], 'ale': [float(v - centre) for v in accumulated], 'rows': counts}


def verdict(univariate, adjusted, se, cfg, direction=None):
    """holds / weakens substantially (>50%) / disappears / reverses, from the univariate power and the adjusted importance."""
    if direction == 'reverses': return 'reverses'
    if univariate is None or univariate <= 0.01: return 'no univariate signal'
    floor = max(0.005, 1.96 * (se or 0))
    if adjusted is None or adjusted <= floor: return 'disappears'
    if adjusted < cfg.weakens_share * univariate: return 'weakens substantially (>50%)'
    return 'holds'


def analyse(a, spec, fields, facts, cfg, time_axis, absent_sql, text_value, clusters, ranked_effects, not_applicable=None, binned=()):
    result = MultivariateResult(kind=spec.kind, fields=list(fields))
    if not fields:
        result.notes.append('No fields are available for the multivariate model.'); return result, []
    data, evidence = load(a, spec, fields, facts, cfg, time_axis, absent_sql, text_value)
    result.sample_rows, result.total_rows = len(data.y), data.total_rows
    if data.total_rows > len(data.y):
        result.notes.append(f'Models use a seeded sample of {len(data.y):,} of {data.total_rows:,} rows (seed {cfg.seed}).')
    if data.time_ordered:
        result.notes.append(f'Cross-validation uses time-ordered folds on {time_axis}: each fold trains on earlier rows and validates on later rows.')
    scored = [e['field'] for e in ranked_effects if e['field'] in fields][:cfg.importance_fields]
    groups = {name: [name] for name in scored}
    for cluster in clusters:
        members = [m for m in cluster.members if m in fields]
        if len(members) > 1 and set(members) & set(scored): groups['cluster: ' + ', '.join(members)] = members
    if len(fields) > len(scored):
        result.notes.append(f'Permutation importance covers the {len(scored)} highest-ranked of {len(fields)} model fields and their clusters; '
                            'the model itself uses every field.')
    per_fold, drops, folds = cross_validate(data, spec.kind, cfg, groups)
    for measure, pairs in per_fold.items():
        model_values = [m for m, _ in pairs]; baseline = [b for _, b in pairs]
        result.metrics.append([measure, float(np.mean(model_values)), float(np.std(model_values, ddof=1)) if len(pairs) > 1 else None,
                               float(np.mean(baseline)), ', '.join(f'{v:.3f}' for v in model_values)])
    headline = next((m for m in result.metrics if m[0].startswith('AUC') or m[0] == 'R²'), None)
    result.signal = (headline[1] - 0.5) if headline and headline[0].startswith('AUC') else headline[1] if headline else None
    result.no_signal = result.signal is None or result.signal < cfg.signal_minimum
    if result.no_signal:
        result.notes.append(f'The fields have little joint predictive signal: the model improves on the baseline by less than {cfg.signal_minimum} '
                            '(ΔAUC or R²). Importance and adjusted effects are not reported.')
    for name, values in drops.items():
        values = np.asarray(values, dtype=float)
        result.importance.append([name, ', '.join(groups[name]), float(values.mean()) if len(values) else None,
                                  float(values.std(ddof=1)) if len(values) > 1 else None, len(values)])
    result.importance.sort(key=lambda r: (-(r[2] if r[2] is not None else -1), r[0]))
    representatives = {c.representative for c in clusters}
    in_cluster = {m for c in clusters for m in c.members}
    non_redundant = [n for n in fields if n not in in_cluster or n in representatives]
    if not result.no_signal:
        result.regression, result.dropped_vif, result.regression_note, fitted, X, owner = adjusted_model(
            data, spec.kind, non_redundant, facts, cfg, not_applicable, binned)
        if binned and fitted is not None:
            result.regression_note += ' Fields with a non-linear shape (' + ', '.join(sorted(b for b in binned if b in non_redundant)) + \
                ') enter as equal-count bins compared with their lowest bin.'
        importance = {r[0]: r for r in result.importance}
        dropped = {row[0] for row in result.dropped_vif}
        for effect in [e for e in ranked_effects if e['field'] in fields][:cfg.adjusted_fields]:
            name = effect['field']
            cluster = next((c for c in clusters if name in c.members and len(c.members) > 1), None)
            shared = ('shares information with ' + ', '.join(m for m in cluster.members if m != name)) if cluster else ''
            in_model = {owner[column] for column in X.columns} if fitted is not None else set()
            c = lr_comparison(spec.kind, data.y, X, owner, fitted, name) if name in in_model else None
            if c is not None:
                result.verdicts.append([name, c['univariate'], c['adjusted'], c['df'], retained(c),
                                        c['p_adjusted'], c['sign_univariate'], c['sign_adjusted'], lr_verdict(c, cfg), shared])
            elif fitted is not None:
                reason = f'left out of the adjusted model: VIF above {cfg.vif_maximum:g}' if name in dropped else (
                    f'represented by {cluster.representative} in the adjusted model' if cluster else 'not in the adjusted model')
                result.verdicts.append([name, None, None, None, None, None, '', '', reason, shared])
            else:
                drop = importance.get(name); mean = drop[2] if drop else None
                se = drop[3] / math.sqrt(drop[4]) if drop and drop[3] is not None and drop[4] else None
                result.verdicts.append([name, effect.get('power'), mean, None, mean / effect['power'] if mean is not None and effect.get('power') else None,
                                        None, '', '', verdict(effect.get('power'), mean, se, cfg) + ' (permutation-based)', shared])
        if spec.kind in ('binary', 'numeric'):
            final = model(spec.kind, data, cfg).fit(data.frame, data.y)
            numeric_ranked = [e['field'] for e in ranked_effects if e['field'] in fields and facts[e['field']]['type'] == 'numeric']
            for name in numeric_ranked[:cfg.ale_fields]:
                curve = ale(final, data, name, spec.kind, cfg)
                if curve: result.ale[name] = curve
    return result, [evidence]
