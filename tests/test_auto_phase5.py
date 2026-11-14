"""Phase 5: time analysis, no-target mode, text and geo fields, and the statistics behind them."""
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats
from scipy.optimize import linprog

from insightpilot.universal import Workspace
from insightpilot.universal import auto_analysis as aa
from insightpilot.universal.auto import stats as auto_stats
from insightpilot.universal.auto import timeseries as ts
from insightpilot.universal.auto.unsupervised import dip
from insightpilot.universal.auto_report import html_report

FIXTURES = Path(__file__).parent / 'fixtures'


# ---------------- statistics against hand-computed or reference values ----------------
def test_mann_kendall_matches_a_hand_computed_example():
    result = ts.mann_kendall([1, 2, 3, 4, 5])
    # S = 10, Var(S) = 5·4·15/18, z = (S − 1)/√Var(S); no autocorrelation correction for so short a series.
    assert result['s'] == 10 and result['var'] == pytest.approx(5 * 4 * 15 / 18)
    assert result['z'] == pytest.approx(9 / math.sqrt(5 * 4 * 15 / 18)) and result['p'] == pytest.approx(2 * stats.norm.sf(result['z']))
    y = np.random.default_rng(1).normal(size=40)
    assert ts.mann_kendall(y)['tau'] == pytest.approx(stats.kendalltau(np.arange(40), y).statistic)
    # Ties reduce the variance by Σ t(t−1)(2t+5)/18.
    tied = ts.mann_kendall([1, 1, 2, 2, 3])
    assert tied['var'] == pytest.approx((5 * 4 * 15 - 2 * (2 * 1 * 9)) / 18)


def test_theil_sen_and_psi_reference_values():
    y = [1.0, 2.5, 2.9, 4.2, 5.1, 5.8]
    expected = stats.theilslopes(y, np.arange(6), alpha=0.95)
    result = ts.theil_sen(y)
    assert (result['slope'], result['low'], result['high']) == pytest.approx((expected.slope, expected.low_slope, expected.high_slope))
    assert ts.psi([50, 50], [25, 75]) == pytest.approx(-0.25 * math.log(0.5) + 0.25 * math.log(1.5))
    assert ts.psi([10, 0], [10, 10]) == pytest.approx((0.5 - 1) * math.log(0.5) + (0.5 - 1e-4) * math.log(0.5 / 1e-4))
    assert ts.psi_label(0.05, 0.1, 0.25) == 'stable' and ts.psi_label(0.3, 0.1, 0.25) == 'major shift'


def test_stl_seasonality_and_breaks_on_planted_series():
    rng = np.random.default_rng(3)
    seasonal = list(10 * np.sin(2 * np.pi * np.arange(72) / 12) + rng.normal(0, 1, 72))
    fit = ts.stl(seasonal, 12, 42, 99)
    assert fit['strength'] > 0.8 and fit['p'] <= 0.01 and fit['peak'] in (2, 3, 4)
    noise = ts.stl(list(rng.normal(size=72)), 12, 42, 99)
    assert noise['p'] > 0.01
    jump = list(rng.normal(0, 1, 80) + np.where(np.arange(80) >= 50, 5.0, 0.0))
    found = ts.breaks(jump, 4, 3, 1.5)
    assert [b['index'] for b in found if abs(b['jump']) > 2] and min(abs(b['index'] - 50) for b in found) <= 1
    assert ts.breaks(list(rng.normal(size=80)), 4, 3, 1.5) == [] or all(abs(b['jump']) < 2 for b in ts.breaks(list(rng.normal(size=80)), 4, 3, 1.5))


def test_choose_grain_and_period_helpers():
    candidates = {'day': (730, 17_000), 'week': (105, 17_000), 'month': (24, 17_000)}
    assert ts.choose_grain('daily', candidates, 20, 30)[0] == 'week'
    assert ts.choose_grain('daily', {'day': (100, 100_000)}, 20, 30)[0] == 'day'
    assert ts.native_grain(1 / 24) == 'sub-daily' and ts.native_grain(1) == 'daily' and ts.native_grain(30) == 'monthly'
    from datetime import date
    assert ts.spanned(date(2020, 1, 1), date(2021, 12, 1), 'month') == 24 and ts.spanned(date(2020, 1, 6), date(2020, 1, 20), 'week') == 3


def unimodal_lp(x):
    """Exact dip by linear programming over the mode segment (a slow reference for small samples)."""
    x = np.sort(x); n = len(x); best = None
    for m in range(n - 1):
        rows, bounds = [], []
        def slope(j):
            r = np.zeros(n + 1); h = x[j + 1] - x[j]; r[j + 1] = 1 / h; r[j] = -1 / h; return r
        for i in range(n):
            r = np.zeros(n + 1); r[i] = -1; r[n] = -1; rows.append(r); bounds.append(-(i + 1) / n)
            r = np.zeros(n + 1); r[i] = 1; r[n] = -1; rows.append(r); bounds.append(i / n)
        for j in range(n - 1): rows.append(-slope(j)); bounds.append(0)
        for j in range(n - 2):
            rows.append(slope(j) - slope(j + 1) if j < m else slope(j + 1) - slope(j)); bounds.append(0)
        c = np.zeros(n + 1); c[n] = 1
        result = linprog(c, A_ub=np.array(rows), b_ub=np.array(bounds), bounds=[(0, 1)] * n + [(0, None)], method='highs')
        if result.status == 0 and (best is None or result.fun < best): best = result.fun
    return best


def test_dip_statistic_equals_the_exact_unimodal_fit():
    rng = np.random.default_rng(0)
    for trial in range(40):
        n = int(rng.integers(5, 13))
        x = rng.normal(size=n) if trial % 2 else np.concatenate([rng.normal(0, 1, n // 2), rng.normal(6, 1, n - n // 2)])
        assert dip.dip_statistic(x) == pytest.approx(unimodal_lp(x), abs=1e-9)
    bimodal = np.concatenate([rng.normal(0, 1, 400), rng.normal(5, 1, 400)])
    assert dip.dip_test(bimodal, 200, 42)[1] < 0.01 and dip.dip_test(rng.normal(size=800), 200, 42)[1] > 0.05


def test_association_intervals():
    low, high = auto_stats.spearman_interval(0.5, 100)
    se = math.sqrt((1 + 0.125) / 97)
    assert (low, high) == pytest.approx((math.tanh(math.atanh(0.5) - 1.959964 * se), math.tanh(math.atanh(0.5) + 1.959964 * se)), abs=1e-6)
    v_low, v_high = auto_stats.cramers_v_interval(30.0, 4, 500, 3)
    assert v_low < math.sqrt(30 / 1000) < v_high
    # The lower bound solves P(χ² ≤ observed | λ) = 0.975.
    assert stats.ncx2.cdf(30.0, 4, v_low ** 2 * 1000) == pytest.approx(0.975, abs=1e-6)
    e_low, e_high = auto_stats.eta_interval(0.3, 4, 200)
    assert e_low < 0.3 < e_high
    big = auto_stats.cramers_v_interval(15000.0, 8, 45000, 3)
    assert big[0] < math.sqrt(15000 / 90000) < big[1]


# ---------------- planted truths ----------------
@pytest.fixture(scope='module')
def workspace(tmp_path_factory):
    w = Workspace.create(tmp_path_factory.mktemp('phase5') / 'w')
    w.add_file(FIXTURES / 'ENGINE-11-timeseries.csv', 'ts', provenance={'origin': 'synthetic'})
    w.add_file(FIXTURES / 'ENGINE-12-unlabelled.csv', 'u', provenance={'origin': 'synthetic'})
    return w


@pytest.fixture(scope='module')
def timed(workspace):
    return aa.run(workspace, 'ts', target='sales', target_type='numeric', time_axis='order_date', save_evidence=False)


@pytest.fixture(scope='module')
def unlabelled(workspace):
    return aa.run(workspace, 'u', save_evidence=False)


def series(output, unit):
    return next(s for s in output['time']['series'] if s['unit'] == unit)


def test_time_series_fixture_trend_seasonality_break_and_drift(timed):
    assert timed['time']['grain'] == 'week' and timed['time']['native'] == 'daily'
    target = series(timed, 'mean')
    assert target['trend']['p'] < 0.05 and target['trend']['low'] > 0
    yearly = [f for f in target['seasonality'] if f['meaning'] == 'week of year']
    assert yearly and yearly[0]['strength'] >= 0.4 and yearly[0]['p'] <= 0.01
    peak_week = int(yearly[0]['peak_label'].split()[-1])
    assert 11 <= peak_week <= 16                      # sin(2π·day/365.25) peaks around day 91 (ISO week 13)
    breaks = [pd.Timestamp(b['label']) for b in target['breaks']]
    assert breaks and min(abs((b - pd.Timestamp('2021-07-05')).days) for b in breaks) <= 7
    drift = {d['column']: d for d in timed['time']['drift']}
    assert drift['channel']['label'] == 'major shift' and drift['region']['label'] == 'stable' and drift['price']['label'] == 'stable'
    types = {f['type'] for f in timed['findings']}
    assert {'trend', 'seasonality', 'break', 'drift'} <= types
    assert any(f['type'] == 'drift' and f['fields'] == ['channel'] for f in timed['findings'])
    # No seasonality is reported for the row count, which has none.
    assert not [f for f in series(timed, 'rows')['seasonality'] if f['p'] <= 0.01 and f['strength'] >= 0.4]


def test_time_axis_makes_cross_validation_time_ordered_and_flags_later_dates(timed):
    suspects = {s['field']: s for s in timed['leakage']}
    assert 'shipped_on' in suspects and 'later than the time axis' in suspects['shipped_on']['reasons'][0]
    power = next(i for i in aa.iter_items(timed) if i.get('id') == 'channel-power')
    assert 'consecutive time blocks' in power['note']
    texts = [i['text'] for i in timed['sections']['time'] if i['type'] == 'text']
    assert any('earlier rows only' in t for t in texts)
    assert any('time-ordered folds' in i['text'] for i in timed['sections']['multivariate'] if i['type'] == 'text')


def test_time_ordered_power_trains_on_earlier_blocks_only():
    # Two bins; fold 0 only has bin a, later folds only bin b: with past-only training, b gets the prior.
    cells = {(0, 'a', 1): 10.0, (0, 'a', 0): 10.0, (1, 'b', 1): 5.0, (1, 'b', 0): 5.0, (2, 'b', 1): 5.0, (2, 'b', 0): 5.0}
    past = auto_stats.cv_classification_power(cells, [0, 1], 2, 20.0, past_only=True)
    assert len(past) == 2 and all(v == pytest.approx(0.5) for v in past)


def test_time_report_draws_the_trend_line_and_break(timed):
    html = html_report(timed)
    assert len(html.encode()) < 2_000_000 and 'stroke-dasharray="6 4"' in html and 'stroke-dasharray="2 3"' in html
    assert 'Distribution drift' in html and 'Trend tests' in html


def test_no_target_mode_finds_three_stable_clusters_and_the_planted_rows(unlabelled):
    u = unlabelled['unsupervised']; c = u['clusters']
    assert c['stable'] and c['k'] == 3 and c['method'] == 'k-medoids on Gower distance'
    chosen = next(r for r in c['candidates'] if r[0] == 3)
    assert chosen[1] >= 0.25 and chosen[2] >= 0.6
    assert sum(n for _, n, _ in c['sizes']) == 1500
    raw = pd.read_csv(FIXTURES / 'ENGINE-12-unlabelled.csv')
    planted = set(raw.index[(raw.x.abs() > 20) | (raw.y.abs() > 20)] + 1)
    flagged = {r[0] for r in u['anomalies']['rows']}
    assert u['anomalies']['flagged'] == 15 and planted <= flagged
    shapes = {s['field']: s for s in u['shapes']}
    assert 'more than one mode' in shapes['x']['labels'] and 'more than one mode' not in shapes['noise']['labels']
    assert {s['key']: s['status'] for s in unlabelled['stages']}['unsupervised'] == 'done'
    types = [f['type'] for f in unlabelled['findings']]
    assert 'cluster_structure' in types and 'unusual_rows' in types


def test_no_stable_structure_is_reported_for_noise(tmp_path):
    rng = np.random.default_rng(9)
    path = tmp_path / 'noise.csv'
    pd.DataFrame({'a': rng.normal(size=600), 'b': rng.normal(size=600), 'c': rng.normal(size=600)}).round(4).to_csv(path, index=False)
    w = Workspace.create(tmp_path / 'w'); w.add_file(path, 'n')
    output = aa.run(w, 'n', save_evidence=False)
    assert not output['unsupervised']['clusters']['stable']
    assert any(f['type'] == 'cluster_structure' and f['statement'].startswith('No stable cluster structure') for f in output['findings'])


def test_text_and_geo_fields(tmp_path):
    rng = np.random.default_rng(7); n = 1500
    words = ['delivery', 'late', 'great', 'product', 'refund', 'the', 'was', 'and', 'very', 'support', 'broken', 'box']
    y = rng.random(n) < 0.3
    comments = [' '.join(rng.choice(words, size=int(rng.integers(8, 20) if t else rng.integers(3, 8)))) for t in y]
    frame = pd.DataFrame({'comment': comments, 'country': rng.choice(['DE', 'FR', 'US', 'GB', 'XX'], size=n, p=[.4, .3, .15, .14, .01]),
                          'site_lat': np.round(rng.uniform(48, 54, n), 4), 'site_lon': np.round(rng.uniform(5, 15, n), 4),
                          'outcome': np.where(y, 'yes', 'no')})
    path = tmp_path / 'tg.csv'; frame.to_csv(path, index=False)
    w = Workspace.create(tmp_path / 'w'); w.add_file(path, 'tg')
    output = aa.run(w, 'tg', target='outcome', target_type='binary', positive='yes', save_evidence=False)
    items = {i.get('id'): i for i in aa.iter_items(output)}
    tokens = [r[0] for r in items['comment-text-tokens']['rows']]
    assert 'the' not in tokens and 'and' not in tokens and 'delivery' in tokens
    summary = dict(items['comment-text-summary']['rows'])
    assert summary['Detected language (stop-word share)'].startswith('english')
    assert 'Cramér' in items['comment-text-length']['note']
    assert items['country-geo-invalid']['rows'] == [['XX', int((frame.country == 'XX').sum())]]
    checks = dict(items['site-lat-site-lon-geo-checks']['rows'])
    assert checks['Rows with both coordinates'] == n
    cells = items['site-lat-site-lon-geo-cells']
    overall = float(y.mean())
    assert cells['rows'][0][3] / cells['rows'][0][-1] == pytest.approx(overall, rel=1e-9)
