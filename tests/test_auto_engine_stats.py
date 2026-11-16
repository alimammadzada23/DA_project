"""Analyst-engine statistics agree with statsmodels, scikit-learn and row-level reference calculations."""
import math

import numpy as np
import pandas as pd
import pytest
from scipy import stats
from sklearn.metrics import normalized_mutual_info_score, r2_score, roc_auc_score
from statsmodels.stats.proportion import proportion_confint

from insightpilot.universal.auto import stats as s
from insightpilot.universal.auto.config import DEFAULT, AnalysisConfig


@pytest.mark.parametrize('successes,n', [(0, 10), (3, 10), (10, 10), (1869, 7043), (1, 40)])
def test_wilson_matches_statsmodels(successes, n):
    assert s.wilson_interval(successes, n) == pytest.approx(proportion_confint(successes, n, method='wilson'))


def test_corrected_cramers_v_by_hand_and_bounds():
    table = np.array([[30, 10], [10, 30], [20, 20]])
    chi2 = stats.chi2_contingency(table, correction=False)[0]; n = table.sum(); r, k = table.shape
    phi = max(0, chi2 / n - (k - 1) * (r - 1) / (n - 1))
    expected = math.sqrt(phi / min(k - (k - 1) ** 2 / (n - 1) - 1, r - (r - 1) ** 2 / (n - 1) - 1))
    assert s.cramers_v_corrected(table) == pytest.approx(expected)
    assert s.cramers_v_corrected(table) < stats.contingency.association(table, method='cramer')
    assert s.cramers_v_corrected([[50, 50], [50, 50]]) == 0.0
    assert s.cramers_v_corrected([[5, 5]]) is None


def test_auc_from_counts_matches_sklearn():
    rng = np.random.default_rng(1)
    score = rng.integers(0, 6, 500); y = (rng.random(500) < 0.2 + 0.1 * score).astype(int)
    frame = pd.DataFrame({'score': score, 'y': y}).groupby('score').y.agg(['sum', 'count'])
    auc = s.auc_from_counts(frame.index, frame['sum'], frame['count'] - frame['sum'])
    assert auc == pytest.approx(roc_auc_score(y, score))


def reference_folds(data, folds=5, m=20.0):
    """Row-level reference: smoothed target encoding fitted on the other folds."""
    aucs = []
    for fold in range(folds):
        train, test = data[data.fold != fold], data[data.fold == fold]
        prior = train.y.mean(); stats_ = train.groupby('bin').y.agg(['sum', 'count'])
        encoded = (stats_['sum'] + m * prior) / (stats_['count'] + m)
        aucs.append(roc_auc_score(test.y, test.bin.map(encoded).fillna(prior)))
    return aucs


def test_cv_classification_power_matches_row_level_reference():
    rng = np.random.default_rng(3)
    data = pd.DataFrame({'bin': rng.integers(0, 8, 2000), 'fold': rng.integers(0, 5, 2000)})
    data['y'] = (rng.random(2000) < 0.1 + 0.08 * data.bin).astype(int)
    cells = data.groupby(['fold', 'bin', 'y']).size().to_dict()
    assert s.cv_classification_power(cells, [0, 1], 5, 20.0) == pytest.approx(reference_folds(data))


def test_cv_multiclass_power_is_macro_one_vs_rest():
    rng = np.random.default_rng(4)
    data = pd.DataFrame({'bin': rng.integers(0, 4, 1500), 'fold': rng.integers(0, 5, 1500)})
    data['y'] = (data.bin + rng.integers(0, 2, 1500)) % 3
    cells = data.groupby(['fold', 'bin', 'y']).size().to_dict()
    expected = [np.mean([roc_auc_score(test.y == c, test.bin.map(
        (train.assign(t=train.y == c).groupby('bin').t.sum() + 20 * (train.y == c).mean()) / (train.groupby('bin').size() + 20)))
        for c in (0, 1, 2)]) for fold in range(5) for train, test in [(data[data.fold != fold], data[data.fold == fold])]]
    assert s.cv_classification_power(cells, [0, 1, 2], 5, 20.0) == pytest.approx(expected)


def test_cv_regression_power_matches_r2_score():
    rng = np.random.default_rng(5)
    data = pd.DataFrame({'bin': rng.integers(0, 6, 1200), 'fold': rng.integers(0, 5, 1200)})
    data['y'] = data.bin * 2.0 + rng.normal(size=1200)
    grouped = data.groupby(['fold', 'bin']).y.agg(['count', 'sum', lambda v: (v * v).sum()])
    cells = {key: tuple(row) for key, row in zip(grouped.index, grouped.to_numpy())}
    expected = []
    for fold in range(5):
        train, test = data[data.fold != fold], data[data.fold == fold]
        means = (train.groupby('bin').y.sum() + 20 * train.y.mean()) / (train.groupby('bin').size() + 20)
        expected.append(r2_score(test.y, test.bin.map(means)))
    assert s.cv_regression_power(cells, 5, 20.0) == pytest.approx(expected)


def test_nmi_matches_sklearn_and_permutation_baseline_is_near_zero_for_noise():
    rng = np.random.default_rng(6)
    x = rng.integers(0, 5, 3000); y = (x + rng.integers(0, 3, 3000)) % 4
    table = pd.crosstab(x, y).to_numpy()
    assert s.normalized_mutual_information(table) == pytest.approx(normalized_mutual_info_score(x, y))
    noise = pd.crosstab(rng.integers(0, 5, 3000), rng.integers(0, 2, 3000)).to_numpy()
    result = s.permutation_nmi(noise, 50, seed=1)
    assert abs(result['adjusted']) < 0.002 and result['p_value'] > 0.05
    assert s.permutation_nmi(noise, 50, seed=1) == result


def test_bootstrap_interval_is_seeded_and_covers_the_median():
    values = np.arange(1, 101, dtype=float)
    first = s.bootstrap_interval(values, np.median, 500, 0.95, seed=1)
    assert first == s.bootstrap_interval(values, np.median, 500, 0.95, seed=1)
    assert first[0] < 50.5 < first[1]


def test_config_hash_changes_with_any_setting():
    assert DEFAULT.hash == AnalysisConfig().hash and len(DEFAULT.hash) == 16
    assert DEFAULT.with_changes(seed=7).hash != DEFAULT.hash
