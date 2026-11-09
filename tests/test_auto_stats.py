"""DuckDB-aggregate statistics agree with SciPy computed directly on the raw values."""
import duckdb
import numpy as np
import pandas as pd
import pytest
from scipy import stats

from insightpilot.universal import auto_stats


@pytest.fixture
def data():
    rng = np.random.default_rng(7)
    # Rounded values produce many ties, which the corrections must handle.
    return pd.DataFrame({'value': np.round(rng.normal(size=400), 1), 'other': np.round(rng.normal(size=400), 0),
                         'group': rng.choice(['a', 'b', 'c'], size=400), 'flag': rng.choice([0, 1], size=400)})


def groups(data, value, group):
    with duckdb.connect() as con:
        con.register('frame', data)
        return con.execute(auto_stats.rank_groups_sql(value, group, 'frame')).fetchall()


def test_average_rank_matches_scipy(data):
    with duckdb.connect() as con:
        con.register('frame', data.assign(position=range(len(data))))
        ranks = [r[0] for r in con.execute(f'SELECT {auto_stats.average_rank("value")} AS r,position FROM frame ORDER BY position').fetchall()]
    assert ranks == pytest.approx(stats.rankdata(data.value))


def test_kruskal_wallis_matches_scipy(data):
    rows = groups(data, 'value', '"group"')
    result = auto_stats.kruskal_wallis([(n, r) for _, n, r, _ in rows], rows[0][3])
    expected = stats.kruskal(*[data.value[data.group == g] for g in ('a', 'b', 'c')])
    assert result['statistic'] == pytest.approx(expected.statistic) and result['p_value'] == pytest.approx(expected.pvalue)
    assert result['epsilon_squared'] == pytest.approx(expected.statistic / (len(data) - 1))


def test_mann_whitney_matches_scipy(data):
    rows = {g: (n, r, ties) for g, n, r, ties in groups(data, 'value', 'flag')}
    result = auto_stats.mann_whitney(rows[1][0], rows[1][1], rows[0][0], rows[1][2])
    expected = stats.mannwhitneyu(data.value[data.flag == 1], data.value[data.flag == 0], method='asymptotic', use_continuity=True)
    assert result['statistic'] == pytest.approx(expected.statistic) and result['p_value'] == pytest.approx(expected.pvalue)
    n1, n2 = rows[1][0], rows[0][0]
    assert result['rank_biserial'] == pytest.approx(2 * expected.statistic / (n1 * n2) - 1)


def test_spearman_from_ranks_matches_scipy(data):
    with duckdb.connect() as con:
        con.register('frame', data)
        n, rho = con.execute(f'SELECT count(*),corr(rx,ry) FROM (SELECT {auto_stats.average_rank("value")} rx,'
                             f'{auto_stats.average_rank("other")} ry FROM frame)').fetchone()
    expected = stats.spearmanr(data.value, data.other)
    assert rho == pytest.approx(expected.statistic) and auto_stats.correlation_p_value(rho, n) == pytest.approx(expected.pvalue)


def test_chi_square_and_cramers_v_match_scipy(data):
    table = pd.crosstab(data.group, data.flag).to_numpy()
    result = auto_stats.chi_square(table)
    expected = stats.chi2_contingency(table, correction=False)
    assert result['statistic'] == pytest.approx(expected.statistic) and result['p_value'] == pytest.approx(expected.pvalue)
    assert result['cramers_v'] == pytest.approx(stats.contingency.association(table, method='cramer', correction=False))
    assert result['cells_expected_below_5'] == 0


def test_chi_square_flags_sparse_cells_and_drops_empty_rows():
    result = auto_stats.chi_square([[10, 0], [0, 0], [1, 3]])
    assert result['cells'] == 4 and result['cells_expected_below_5'] == 3
    assert auto_stats.chi_square([[1, 2]]) is None


def test_correlation_ratio_matches_definition(data):
    total = data.value
    between = sum(len(g) * (g.mean() - total.mean()) ** 2 for _, g in data.groupby('group').value)
    expected = (between / ((total - total.mean()) ** 2).sum()) ** .5
    groups = [(len(g), g.mean()) for _, g in data.groupby('group').value]
    assert auto_stats.correlation_ratio(groups, len(total), total.mean(), total.var(ddof=0)) == pytest.approx(expected)


def test_benjamini_hochberg_matches_scipy_and_keeps_gaps():
    p = [0.01, 0.04, 0.03, 0.2, 0.001]
    assert auto_stats.benjamini_hochberg(p) == pytest.approx(list(stats.false_discovery_control(p)))
    assert auto_stats.benjamini_hochberg([0.02, None, 0.04]) == pytest.approx([0.04, None, 0.04])


def test_undefined_statistics_are_none():
    assert auto_stats.kruskal_wallis([(5, 15.0), (5, 40.0)], ties=10 ** 3 - 10) is None  # every value tied
    assert auto_stats.mann_whitney(0, 0, 5, 0) is None
    assert auto_stats.correlation_p_value(None, 10) is None and auto_stats.correlation_p_value(1.0, 10) == 0.0
    assert auto_stats.correlation_ratio([(3, 1.0)], 3, 1.0, 0.0) is None
