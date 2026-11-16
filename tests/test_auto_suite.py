"""Phase 6: the rest of the planted-truth suite (spec §9.2) and the determinism and property tests (§9.4).

Fixtures 2–9, 11, 12, 16 and 17 are asserted in the other test_auto_* modules; this module covers 1 (top findings),
10, 13, 14, 15 and 18, a CMH reference value, and checks that results do not depend on row order or column names.
"""
import json
import math
import os
from pathlib import Path
import time

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import roc_auc_score

from insightpilot.universal import Workspace
from insightpilot.universal import auto_analysis as aa
from insightpilot.universal.auto.report import results

FIXTURES = Path(__file__).parent / 'fixtures'


@pytest.fixture(scope='module')
def workspace(tmp_path_factory):
    w = Workspace.create(tmp_path_factory.mktemp('suite') / 'w')
    for key, table in (('01-conditional', 'c01'), ('05-simpson', 'c05'), ('10-high-cardinality', 'c10'), ('13-tiny', 'c13'),
                       ('18-targets', 'c18')):
        w.add_file(FIXTURES / f'ENGINE-{key}.csv', table, provenance={'origin': 'synthetic'})
    return w


def items(output):
    return {i.get('id'): i for i in aa.iter_items(output)}


def frame(item):
    return pd.DataFrame(item['rows'], columns=[c['name'] for c in item['columns']])


def failed(output):
    return [s for s in output['stages'] if s['status'] in ('failed', 'partial')]


# ---------------- planted truths ----------------
def test_01_conditional_field_stays_out_of_the_top_findings(workspace):
    output = aa.run(workspace, 'c01', target='outcome', target_type='binary', positive='yes', save_evidence=False)
    top = output['findings'][:10]
    assert any(f['type'] == 'conditional_category' and f['fields'][0] == 'addon' for f in output['findings'])
    assert not [f for f in top if 'addon' in f['fields'] and f['type'] in ('strong_association', 'high_impact_segment')]
    power = next(e for e in output['ranking'] if e['field'] == 'addon')
    # No effect among applicable rows: no positive power and no significant test there.
    assert power['power'] < 0.02 and power['adjusted_p'] >= 0.05 and power['subset'] != 'all rows'


def test_10_high_cardinality_surfaces_the_three_levels_without_overfitting(workspace):
    output = aa.run(workspace, 'c10', target='outcome', target_type='binary', positive='yes', save_evidence=False)
    assert not failed(output)
    rates = frame(items(output)['code-rates'])
    assert len(rates) == 31 and rates.Category.iloc[-1].startswith('(other:')
    top = rates[~rates.Category.str.startswith('(')].sort_values('Rows', ascending=False).head(3)
    assert set(top.Category) == {'K0001', 'K0002', 'K0003'} and (top.Lift > 2).all()
    raw = pd.read_csv(FIXTURES / 'ENGINE-10-high-cardinality.csv')
    oracle = roc_auc_score(raw.outcome == 'yes', raw.code.isin(['K0001', 'K0002', 'K0003'])) - 0.5
    power = next(e for e in output['ranking'] if e['field'] == 'code')['power']
    # Cross-validated encoding cannot learn more than the three real levels carry.
    assert 0.5 * oracle < power <= oracle + 0.01
    levels = {c[2] for s in output['segments'] for c in s['conditions'] if c[0] == 'code'}
    for f in output['findings']:
        if 'code' in f['fields']:
            mentioned = set(pd.Series(f['statement'].replace(',', ' ').split()).str.extract(r'(K\d{4})')[0].dropna())
            assert mentioned <= {'K0001', 'K0002', 'K0003'}, f['statement']
    assert all(level in {'K0001', 'K0002', 'K0003'} for level in levels)


def test_13_tiny_data_uses_exact_tests_and_warns(workspace):
    output = aa.run(workspace, 'c13', target='outcome', target_type='binary', positive='yes', save_evidence=False)
    assert not [s for s in output['stages'] if s['status'] == 'failed']
    tests = {r[0] for key, item in items(output).items() if key and key.endswith('-test') for r in item['rows']}
    assert any(t.startswith('Fisher') for t in tests)
    sparse = frame(items(output)['colour-test']).iloc[0]
    assert sparse.Test.startswith('Fisher–Freeman–Halton') and 'exact test used' in sparse.Flags
    assert output['small_sample_warnings'] and output['segments'] == []
    assert any('Small minority class' in w for w in output['warnings']) or output['small_sample_warnings']


def test_18_multiclass_and_numeric_targets_use_the_right_tests_and_metrics(workspace):
    multi = aa.run(workspace, 'c18', target='grade', target_type='multiclass', save_evidence=False)
    found = items(multi)
    assert frame(found['x1-test']).Test.iloc[0] == 'Kruskal–Wallis H' and frame(found['c1-test']).Test.iloc[0] == 'Chi-square test of independence'
    assert multi['multivariate']['metrics'][0][0] == 'AUC (macro one-vs-rest)' and multi['multivariate']['metrics'][0][1] > 0.75
    assert [e['field'] for e in multi['ranking'][:2]] == ['x1', 'c1']
    numeric = aa.run(workspace, 'c18', target='score', target_type='numeric', save_evidence=False)
    found = items(numeric)
    assert frame(found['x2-test']).Test.iloc[0] == 'Spearman rank correlation' and frame(found['c2-test']).Test.iloc[0] == 'Kruskal–Wallis H'
    metrics = {m[0]: m for m in numeric['multivariate']['metrics']}
    assert metrics['R²'][1] > 0.8 and metrics['MAE'][1] < metrics['MAE'][3]
    assert [e['field'] for e in numeric['ranking'][:2]] == ['x2', 'c2'] and {e['power_metric'] for e in numeric['ranking']} == {'R²'}


def test_14_wide_data_finishes_within_the_budget(tmp_path):
    rng = np.random.default_rng(14); n = 1500
    data = pd.DataFrame({f'v{i:03d}': np.round(rng.normal(size=n), 3) for i in range(300)})
    logit = 1.2 * data.v010 - 1.0 * data.v050 + 0.9 * data.v100 + 0.8 * data.v200 - 0.9 * data.v290
    data['outcome'] = np.where(rng.random(n) < 1 / (1 + np.exp(-logit)), 'yes', 'no')
    path = tmp_path / 'wide.csv'; data.to_csv(path, index=False)
    w = Workspace.create(tmp_path / 'w'); w.add_file(path, 'wide')
    started = time.perf_counter()
    output = aa.run(w, 'wide', target='outcome', target_type='binary', positive='yes', save_evidence=False)
    assert time.perf_counter() - started < 300 and not failed(output)
    assert {e['field'] for e in output['ranking'][:5]} == {'v010', 'v050', 'v100', 'v200', 'v290'}
    assert any(text.startswith('Wide data: 300 ranked fields') for text in output['warnings'])


@pytest.mark.skipif(os.environ.get('INSIGHTPILOT_LARGE_TESTS') != '1', reason='set INSIGHTPILOT_LARGE_TESTS=1 for the 1M-row test')
def test_15_large_data_finishes_within_the_target_and_discloses_samples(tmp_path):
    rng = np.random.default_rng(15); n = 1_000_000
    data = {f'x{i}': np.round(rng.normal(size=n), 4) for i in range(30)}
    data |= {f'c{i}': rng.choice([f'l{j}' for j in range(int(rng.integers(2, 12)))], size=n) for i in range(19)}
    logit = -1 + 0.8 * data['x0'] - 0.6 * data['x1'] + 0.5 * (data['c0'] == 'l1')
    data['target'] = np.where(rng.random(n) < 1 / (1 + np.exp(-logit)), 'yes', 'no')
    path = tmp_path / 'large.csv'; pd.DataFrame(data).to_csv(path, index=False)
    w = Workspace.create(tmp_path / 'w'); w.add_file(path, 'large')
    started = time.perf_counter()
    output = aa.run(w, 'large', target='target', target_type='binary', positive='yes', save_evidence=False)
    assert time.perf_counter() - started < 300 and not failed(output)
    assert {row[0] for row in output['sampling']} >= {'multivariate model'}
    assert [e['field'] for e in output['ranking'][:3]] == ['x0', 'x1', 'c0']


# ---------------- reference value: Cochran–Mantel–Haenszel ----------------
def test_cmh_pooled_odds_ratio_matches_the_hand_formula(workspace):
    output = aa.run(workspace, 'c05', target='outcome', target_type='binary', positive='yes', save_evidence=False)
    check = next(c for c in output['confounding'] if c['field'] == 'treatment' and c['stratifier'] == 'stratum')
    raw = pd.read_csv(FIXTURES / 'ENGINE-05-simpson.csv')
    exposed = raw.treatment == check['comparison'].split('=')[1].split(' vs')[0].strip()
    numerator = denominator = 0.0
    for _, part in raw.groupby('stratum'):
        e = exposed[part.index]; y = part.outcome == 'yes'; n = len(part)
        a_, b_, c_, d_ = (e & y).sum(), (e & ~y).sum(), (~e & y).sum(), (~e & ~y).sum()
        numerator += a_ * d_ / n; denominator += b_ * c_ / n
    assert check['stratified'] == pytest.approx(numerator / denominator, rel=1e-9)


# ---------------- determinism and properties ----------------
def full_data_tables(output):
    """Tables computed from every row (not from seeded samples or folds), keyed by item id."""
    keep = ('-rates', '-test', '-summary', '-bins', '-medians', '-classes')
    selected = {}
    for key, item in items(output).items():
        if item.get('type') != 'table' or not key: continue
        if key.endswith(keep) or key in ('target-classes', 'target-summary', 'structure-conditional', 'missing-explanations', 'confounding-checks'):
            selected[key] = item['rows']
    return selected


def close(a, b):
    if isinstance(a, float) and isinstance(b, float): return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
    if isinstance(a, list) and isinstance(b, list): return len(a) == len(b) and all(close(x, y) for x, y in zip(a, b))
    return a == b


def test_two_runs_give_identical_results_json_in_every_mode(workspace):
    for kwargs in (dict(target='outcome', target_type='binary', positive='yes'), dict(target='score', target_type='numeric'), {}):
        table = 'c05' if kwargs.get('target') == 'outcome' else 'c18'
        first, second = (aa.run(workspace, table, save_evidence=False, **kwargs) for _ in range(2))
        assert results(first) == results(second), kwargs


def test_shuffling_rows_changes_no_full_data_statistic(tmp_path):
    raw = pd.read_csv(FIXTURES / 'ENGINE-01-conditional.csv')
    shuffled = raw.sample(frac=1.0, random_state=3)
    paths = [tmp_path / 'a.csv', tmp_path / 'b.csv']; raw.to_csv(paths[0], index=False); shuffled.to_csv(paths[1], index=False)
    w = Workspace.create(tmp_path / 'w'); w.add_file(paths[0], 'a'); w.add_file(paths[1], 'b')
    original, reordered = (aa.run(w, t, target='outcome', target_type='binary', positive='yes', save_evidence=False) for t in ('a', 'b'))
    left, right = full_data_tables(original), full_data_tables(reordered)
    assert left.keys() == right.keys() and len(left) >= 8
    for key in left:
        assert close(left[key], right[key]), key


def test_renaming_every_column_changes_only_labels(tmp_path):
    raw = pd.read_csv(FIXTURES / 'ENGINE-05-simpson.csv')
    names = dict(zip(raw.columns, ['alpha', 'bravo', 'charlie', 'delta', 'echo', 'foxtrot'][:len(raw.columns)]))
    paths = [tmp_path / 'a.csv', tmp_path / 'b.csv']; raw.to_csv(paths[0], index=False); raw.rename(columns=names).to_csv(paths[1], index=False)
    w = Workspace.create(tmp_path / 'w'); w.add_file(paths[0], 'a'); w.add_file(paths[1], 'b')
    original = aa.run(w, 'a', target='outcome', target_type='binary', positive='yes', save_evidence=False)
    renamed = aa.run(w, 'b', target=names['outcome'], target_type='binary', positive='yes', save_evidence=False)
    text = results(renamed)
    for old, new in names.items(): text = text.replace(new, old)
    left, right = json.loads(results(original)), json.loads(text)
    for data in (left, right):
        for key in ('source_sha256', 'dataset', 'table', 'evidence'): data.pop(key, None)
        # The only name-dependent step is the disclosed name hint that raises a target suggestion's score.
        data['tables'] = [t for t in data['tables'] if t['id'] != 'target-suggestions']
    assert left == right


def test_exact_tests_are_used_only_where_they_are_cheap_and_needed():
    from scipy import stats
    from insightpilot.universal import auto_stats
    small = auto_stats.independence_test([[3, 1], [1, 3]])
    assert small['test'] == "Fisher's exact test" and small['p_value'] == pytest.approx(stats.fisher_exact([[3, 1], [1, 3]]).pvalue)
    sparse = auto_stats.independence_test([[50, 3, 20], [40, 1, 9], [3, 0, 7]])
    assert sparse['test'].startswith('Fisher–Freeman–Halton') and sparse['chi_square_p'] != sparse['p_value']
    # A large sparse table keeps the chi-square p-value: simulating 9,999 tables of half a million rows is not attempted.
    started = time.perf_counter()
    large = auto_stats.independence_test([[250_000, 3, 20_000], [200_000, 1, 9_000], [3_000, 0, 70]])
    assert large['test'] == 'Chi-square test of independence' and time.perf_counter() - started < 1
    plain = auto_stats.independence_test([[30, 20], [25, 35]])
    assert plain['test'] == 'Chi-square test of independence' and 'chi_square_p' not in plain
