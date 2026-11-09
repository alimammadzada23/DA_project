"""Exact, deterministic statistics from aggregates that DuckDB computes over full tables.

Rank tests use average ranks for ties and the standard tie corrections, so no table
is loaded into memory. Each function returns plain numbers; None means a statistic is
undefined for the data (for example, every value is tied).
"""
import math

import numpy as np
from scipy import stats


def average_rank(value_sql):
    """SQL window expression for the average rank of a value, as scipy.stats.rankdata(method='average')."""
    return f'(rank() OVER (ORDER BY {value_sql}) + (count(*) OVER (PARTITION BY {value_sql}) - 1) / 2.0)'


def rank_groups_sql(value_sql, group_sql, source):
    """Per-group count and rank sum, plus the tie term, for complete (value, group) pairs from `source`.

    Written with subqueries only, so it can follow the Engine's own WITH clause.
    """
    pairs = f'(SELECT v,g FROM (SELECT {value_sql} AS v,{group_sql} AS g FROM {source}) WHERE v IS NOT NULL AND g IS NOT NULL)'
    return (f'SELECT g,count(*)::DOUBLE AS n,sum(r)::DOUBLE AS rank_sum,'
            f'(SELECT sum(t*t*t-t) FROM (SELECT count(*)::DOUBLE AS t FROM {pairs} GROUP BY v)) AS ties '
            f'FROM (SELECT g,{average_rank("v")} AS r FROM {pairs}) GROUP BY g ORDER BY g')


def kruskal_wallis(groups, ties):
    """H test from (n, rank_sum) per group; returns H, p, epsilon-squared (Tomczak & Tomczak 2014)."""
    groups = [(n, r) for n, r in groups if n]
    total = sum(n for n, _ in groups)
    if len(groups) < 2 or total < 3: return None
    correction = 1 - (ties or 0) / (total ** 3 - total)
    if correction <= 0: return None
    h = (12 / (total * (total + 1)) * sum(r * r / n for n, r in groups) - 3 * (total + 1)) / correction
    h = max(h, 0.0)
    return {'statistic': h, 'df': len(groups) - 1, 'p_value': float(stats.chi2.sf(h, len(groups) - 1)),
            'epsilon_squared': h / (total - 1), 'n': int(total)}


def mann_whitney(n_first, rank_sum_first, n_second, ties):
    """Two-sided normal approximation with tie and continuity correction (scipy method='asymptotic').

    U is reported for the first group; rank-biserial r = 2U/(n1·n2) − 1 is positive
    when values in the first group tend to be larger.
    """
    if not n_first or not n_second: return None
    total = n_first + n_second
    u = rank_sum_first - n_first * (n_first + 1) / 2
    mean = n_first * n_second / 2
    variance = n_first * n_second / 12 * ((total + 1) - (ties or 0) / (total * (total - 1)))
    if variance <= 0: return None
    larger = max(u, n_first * n_second - u)
    z = (larger - mean - 0.5) / math.sqrt(variance)
    return {'statistic': u, 'z': z, 'p_value': float(min(1.0, 2 * stats.norm.sf(z))),
            'rank_biserial': 2 * u / (n_first * n_second) - 1, 'n': int(total)}


def correlation_p_value(r, n):
    """Two-sided p for a correlation coefficient from the t distribution with n − 2 degrees of freedom."""
    if r is None or n is None or n < 3 or not math.isfinite(r): return None
    if abs(r) >= 1: return 0.0
    t = r * math.sqrt((n - 2) / (1 - r * r))
    return float(2 * stats.t.sf(abs(t), n - 2))


def chi_square(table):
    """Pearson chi-square of independence (no continuity correction) and Cramér's V.

    Rows or columns with no observations are removed first; they carry no information.
    """
    values = np.asarray(table, dtype=float)
    values = values[values.sum(axis=1) > 0][:, values.sum(axis=0) > 0]
    if values.ndim != 2 or min(values.shape) < 2: return None
    statistic, p_value, dof, expected = stats.chi2_contingency(values, correction=False)
    n = values.sum()
    return {'statistic': float(statistic), 'df': int(dof), 'p_value': float(p_value), 'n': int(n),
            'cramers_v': math.sqrt(float(statistic) / (n * (min(values.shape) - 1))),
            'cells_expected_below_5': int((expected < 5).sum()), 'cells': int(expected.size),
            'minimum_expected': float(expected.min())}


def correlation_ratio(groups, total_n, total_mean, total_variance_population):
    """η = sqrt(between-group sum of squares / total sum of squares) from (n, mean) per group."""
    total_ss = total_variance_population * total_n if total_variance_population is not None else None
    if not total_ss or total_ss <= 0: return None
    between = sum(n * (mean - total_mean) ** 2 for n, mean in groups if n and mean is not None)
    return math.sqrt(min(1.0, between / total_ss))


def benjamini_hochberg(p_values):
    """Benjamini–Hochberg adjusted p-values; None entries stay None and are not counted."""
    present = sorted((p, index) for index, p in enumerate(p_values) if p is not None)
    adjusted = [None] * len(p_values); running = 1.0; m = len(present)
    for rank in range(m, 0, -1):
        p, index = present[rank - 1]
        running = min(running, p * m / rank)
        adjusted[index] = min(1.0, running)
    return adjusted
