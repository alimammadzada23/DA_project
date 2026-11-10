"""Statistics for the analyst engine. Inputs are small count tables computed by DuckDB.

Every function states its formula and reference; tests compare each with SciPy,
statsmodels, scikit-learn or hand-computed examples.
"""
import math

import numpy as np
from scipy import stats


def wilson_interval(successes, n, confidence=0.95):
    """Wilson score interval for a proportion (Wilson 1927; Agresti & Coull 1998).

    centre = (p + z²/2n) / (1 + z²/n), half-width = z·sqrt(p(1−p)/n + z²/4n²) / (1 + z²/n).
    """
    if not n: return (None, None)
    z = stats.norm.ppf(0.5 + confidence / 2)
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return (max(0.0, centre - half), min(1.0, centre + half))


def cramers_v_corrected(table):
    """Bias-corrected Cramér's V (Bergsma 2013, J. Korean Stat. Soc. 42:323–328).

    φ²̃ = max(0, χ²/n − (k−1)(r−1)/(n−1)), r̃ = r − (r−1)²/(n−1), k̃ = k − (k−1)²/(n−1),
    V = sqrt(φ²̃ / min(k̃−1, r̃−1)). Empty rows and columns are dropped first.
    """
    values = np.asarray(table, dtype=float)
    values = values[values.sum(axis=1) > 0][:, values.sum(axis=0) > 0]
    if values.ndim != 2 or min(values.shape) < 2: return None
    n = values.sum()
    if n < 2: return None
    chi2 = stats.chi2_contingency(values, correction=False)[0]
    r, k = values.shape
    phi2 = max(0.0, chi2 / n - (k - 1) * (r - 1) / (n - 1))
    r_tilde = r - (r - 1) ** 2 / (n - 1); k_tilde = k - (k - 1) ** 2 / (n - 1)
    denominator = min(k_tilde - 1, r_tilde - 1)
    return math.sqrt(phi2 / denominator) if denominator > 0 else None


def auc_from_counts(scores, positives, negatives):
    """ROC AUC from grouped scores: P(score₊ > score₋) + ½·P(tie) (Mann–Whitney form, Hanley & McNeil 1982)."""
    levels = {}
    for score, p, n in zip(scores, positives, negatives):
        key = round(float(score), 12)
        a, b = levels.get(key, (0.0, 0.0)); levels[key] = (a + p, b + n)
    total_p = sum(p for p, _ in levels.values()); total_n = sum(n for _, n in levels.values())
    if not total_p or not total_n: return None
    below = 0.0; wins = 0.0
    for key in sorted(levels):
        p, n = levels[key]
        wins += p * below + 0.5 * p * n
        below += n
    return wins / (total_p * total_n)


def cv_classification_power(cells, classes, folds, smoothing):
    """Out-of-fold AUC of a single-feature model that predicts each bin's smoothed target rate.

    cells: {(fold, bin, class): rows}. For held-out fold f the score of bin b is
    (positives_b + m·prior) / (rows_b + m) using only the other folds (m = smoothing,
    prior = training-fold positive share), i.e. cross-validated target encoding
    (Micci-Barreca 2001). Multiclass targets use the macro one-vs-rest AUC.
    Returns the AUC per fold.
    """
    bins = sorted({b for _, b, _ in cells}, key=str)
    per_fold = []
    for fold in range(folds):
        aucs = []
        targets = classes if len(classes) > 2 else classes[-1:]
        for positive in targets:
            train_rows = {b: 0.0 for b in bins}; train_pos = {b: 0.0 for b in bins}
            test_pos = {b: 0.0 for b in bins}; test_neg = {b: 0.0 for b in bins}
            for (f, b, c), n in cells.items():
                if f == fold:
                    if c == positive: test_pos[b] += n
                    else: test_neg[b] += n
                else:
                    train_rows[b] += n
                    if c == positive: train_pos[b] += n
            total_rows = sum(train_rows.values())
            if not total_rows: continue
            prior = sum(train_pos.values()) / total_rows
            scores = [(train_pos[b] + smoothing * prior) / (train_rows[b] + smoothing) for b in bins]
            auc = auc_from_counts(scores, [test_pos[b] for b in bins], [test_neg[b] for b in bins])
            if auc is not None: aucs.append(auc)
        if aucs: per_fold.append(float(np.mean(aucs)))
    return per_fold


def cv_regression_power(cells, folds, smoothing):
    """Out-of-fold R² of a single-feature model predicting each bin's smoothed training mean.

    cells: {(fold, bin): (rows, Σy, Σy²)}. Prediction for bin b is
    (Σy_b + m·mean) / (rows_b + m) from the other folds. Per fold
    R² = 1 − SSE/SST with SST around the held-out fold's own mean (as scikit-learn's r2_score).
    """
    bins = sorted({b for _, b in cells}, key=str)
    per_fold = []
    for fold in range(folds):
        train = {b: [0.0, 0.0] for b in bins}
        for (f, b), (n, s, _) in cells.items():
            if f != fold: train[b][0] += n; train[b][1] += s
        rows = sum(n for n, _ in train.values())
        if not rows: continue
        mean = sum(s for _, s in train.values()) / rows
        sse = 0.0; test_n = 0.0; test_s = 0.0; test_ss = 0.0
        for (f, b), (n, s, ss) in cells.items():
            if f != fold: continue
            prediction = (train[b][1] + smoothing * mean) / (train[b][0] + smoothing)
            sse += ss - 2 * prediction * s + n * prediction * prediction
            test_n += n; test_s += s; test_ss += ss
        sst = test_ss - test_s * test_s / test_n if test_n else 0.0
        if sst > 0: per_fold.append(1 - sse / sst)
    return per_fold


def mutual_information(table):
    """Mutual information (nats) of a contingency table."""
    values = np.asarray(table, dtype=float); n = values.sum()
    if not n: return 0.0
    joint = values / n; rows = joint.sum(axis=1, keepdims=True); cols = joint.sum(axis=0, keepdims=True)
    mask = joint > 0
    return float((joint[mask] * np.log(joint[mask] / (rows @ cols)[mask])).sum())


def entropy(counts):
    p = np.asarray(counts, dtype=float); p = p[p > 0] / p.sum()
    return float(-(p * np.log(p)).sum())


def normalized_mutual_information(table):
    """MI / mean(H(X), H(Y)): the arithmetic normalisation used by scikit-learn's normalized_mutual_info_score."""
    values = np.asarray(table, dtype=float)
    h = (entropy(values.sum(axis=1)) + entropy(values.sum(axis=0))) / 2
    return mutual_information(values) / h if h > 0 else 0.0


def permutation_nmi(table, permutations, seed):
    """NMI and its mean under random permutation of the target.

    Permuting the target with both margins fixed is equivalent to drawing each row of the
    table from a multivariate hypergeometric distribution of the remaining column totals.
    """
    values = np.asarray(table, dtype=np.int64)
    observed = normalized_mutual_information(values)
    rng = np.random.default_rng(seed); null = []
    for _ in range(permutations):
        remaining = values.sum(axis=0).copy(); simulated = np.zeros_like(values)
        for index, size in enumerate(values.sum(axis=1)):
            draw = rng.multivariate_hypergeometric(remaining, int(size)); simulated[index] = draw; remaining -= draw
        null.append(normalized_mutual_information(simulated))
    return {'nmi': observed, 'null_mean': float(np.mean(null)) if null else 0.0,
            'adjusted': observed - (float(np.mean(null)) if null else 0.0),
            'p_value': (1 + sum(v >= observed for v in null)) / (1 + len(null)) if null else None}


def bootstrap_interval(values, statistic, resamples, confidence, seed):
    """Percentile bootstrap interval (Efron & Tibshirani 1993), seeded."""
    values = np.asarray(values, dtype=float)
    if len(values) < 2: return (None, None)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(values), size=(resamples, len(values)))
    estimates = statistic(values[draws], axis=1)
    alpha = (1 - confidence) / 2
    return (float(np.quantile(estimates, alpha)), float(np.quantile(estimates, 1 - alpha)))
