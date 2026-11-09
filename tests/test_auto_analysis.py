"""Automatic analysis on synthetic fixtures with planted effects, checked against direct pandas/SciPy calculations."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from insightpilot.universal import Workspace
from insightpilot.universal import auto_analysis as aa
from insightpilot.universal.auto_report import report_tables
from insightpilot.universal.report import replay

FIXTURES = Path(__file__).parent / 'fixtures'


def raw(name):
    return pd.read_csv(FIXTURES / f'SYNTHETIC-auto-{name}.csv', dtype=str, keep_default_na=False)


@pytest.fixture(scope='module')
def workspace(tmp_path_factory):
    workspace = Workspace.create(tmp_path_factory.mktemp('auto') / 'w')
    for name in ('binary', 'multiclass', 'numeric'):
        workspace.add_file(FIXTURES / f'SYNTHETIC-auto-{name}.csv', name, provenance={'origin': 'synthetic'})
    return workspace


@pytest.fixture(scope='module')
def binary(workspace):
    return aa.run(workspace, 'binary', target='churn', target_type='binary', positive='Yes', time_axis='signup')


@pytest.fixture(scope='module')
def multiclass(workspace):
    return aa.run(workspace, 'multiclass', target='level', target_type='multiclass', save_evidence=False)


@pytest.fixture(scope='module')
def numeric(workspace):
    return aa.run(workspace, 'numeric', target='y', target_type='numeric', time_axis='day', save_evidence=False)


@pytest.fixture(scope='module')
def descriptive(workspace):
    return aa.run(workspace, 'binary', save_evidence=False)


def item(output, item_id):
    return next(i for i in aa.iter_items(output) if i.get('id') == item_id)


def rows(output, item_id):
    found = item(output, item_id)
    return pd.DataFrame(found['rows'], columns=[c['name'] for c in found['columns']])


def field(output, name):
    return next(f for f in output['sections']['fields'] if f['field'] == name)


def statuses(output):
    return {s['key']: s['status'] for s in output['stages']}


# ---------- stage 1 and 2 ----------
def test_types_are_detected_from_the_data(workspace):
    prepared = aa.prepare(workspace, 'binary')
    assert {n: c['type'] for n, c in prepared['columns'].items()} == {
        'id': 'identifier', 'region': 'categorical', 'segment': 'categorical', 'tenure': 'numeric', 'amount': 'numeric',
        'balance': 'numeric', 'signup': 'date', 'local_date': 'date', 'ambiguous_date': 'ambiguous_date', 'notes': 'text',
        'constant': 'constant', 'score': 'numeric', 'churn': 'binary'}
    assert prepared['date_columns'] == ['signup', 'local_date']


def test_target_suggestions_are_ranked_with_reasons(workspace):
    suggestions = aa.prepare(workspace, 'binary')['suggestions']
    assert suggestions[0]['column'] == 'churn' and suggestions[0]['positive'] == 'Yes'
    assert any('hint only' in reason for reason in suggestions[0]['reasons'])
    assert {s['column'] for s in suggestions} >= {'region', 'tenure'}
    assert all(s['column'] not in ('id', 'notes', 'constant', 'signup') for s in suggestions)
    numeric_target = next(s for s in aa.prepare(workspace, 'numeric')['suggestions'] if s['column'] == 'y')
    assert numeric_target['target_type'] == 'numeric'


def test_quality_findings_match_the_planted_problems(binary):
    data = raw('binary')
    problems = rows(binary, 'quality-problems')
    issues = set(zip(problems['Column'], problems['Issue']))
    assert {('amount', 'NA-like markers'), ('tenure', 'numbers stored as text'), ('segment', 'leading or trailing whitespace'),
            ('signup', 'dates stored as text (YYYY-MM-DD)'), ('constant', 'constant'), ('notes', 'text'), ('id', 'identifier')} <= issues
    assert problems[(problems.Column == 'amount') & (problems.Issue == 'NA-like markers')].Rows.item() == (data.amount == 'N/A').sum() == 4
    overview = dict(rows(binary, 'quality-overview').values)
    assert overview['Rows'] == len(data) and overview['Exact duplicate rows (beyond the first copy)'] == len(data) - len(data.drop_duplicates()) == 2
    missing = rows(binary, 'quality-missing').set_index('Column')
    assert missing.loc['balance', 'Rows'] == (data.balance == '').sum() == 6
    expected_ids = [i + 1 for i in data.index[data.balance == '']]
    assert missing.loc['balance'].iloc[2] == ', '.join(map(str, expected_ids))
    variants = rows(binary, 'quality-variants')
    assert variants.Spellings.tolist() == ["' Retail' | 'Retail' | 'retail'"]
    assert rows(binary, 'quality-duplicates')['_row_id values'].tolist() == ['2, 401', '3, 402']


# ---------- stage 3 ----------
def test_corrections_create_one_reported_version_and_restore_the_active_one(workspace, binary):
    info = workspace.info('binary')
    assert info['current'] == binary['original_version'] != binary['analysis_version']
    assert binary['analysis_version'] in {v['table'] for v in info['versions']}
    applied = rows(binary, 'corrections-applied')
    assert set(zip(applied.Column, applied.Change)) >= {('segment', 'trimmed whitespace'), ('amount', "set to missing: 'N/A'"),
        ('amount', 'converted to numbers'), ('signup', 'converted to dates'), ('local_date', 'converted to dates')}
    assert 'ambiguous_date' not in applied.Column.tolist() and 'churn' not in applied.Column.tolist()
    assert set(applied['Version created']) == {binary['analysis_version']}


def test_issues_that_are_only_reported(binary):
    kept = rows(binary, 'corrections-not-changed')
    pairs = set(zip(kept.Column, kept.Action))
    assert {('ambiguous_date', 'not converted'), ('(all columns)', 'not removed'), ('segment', 'not merged'),
            ('balance', 'not filled'), ('amount', 'not filled')} <= pairs


def test_missing_pattern_is_observed_not_filled(workspace, binary):
    patterns = rows(binary, 'missing-patterns')
    first = patterns[patterns['Column with missing values'] == 'balance'].iloc[0]
    assert (first['Other column'], first['Value in every missing row'], first['Missing rows'], first['Rows with that value in total']) == ('tenure', '0', 6, 6)
    with workspace.connect() as con:
        assert con.execute(f'SELECT count(*) FROM "{binary["analysis_version"]}" WHERE balance IS NULL').fetchone()[0] == 6


# ---------- stage 4 ----------
def test_binary_target_overview(binary):
    data = raw('binary')
    classes = rows(binary, 'target-classes').set_index('Class')
    assert classes.loc['Yes', 'Rows'] == (data.churn == 'Yes').sum()
    assert classes.loc['Yes', 'Share'] == pytest.approx((data.churn == 'Yes').mean())
    summary = rows(binary, 'target-baseline').set_index('Measure')
    assert summary.loc['Accuracy of always predicting the majority class', 'Share'] == pytest.approx(data.churn.value_counts(normalize=True).max())


def test_numeric_target_overview(numeric):
    data = raw('numeric').y.astype(float)
    summary = rows(numeric, 'target-summary').iloc[0]
    assert (summary['median'], summary['mean'], summary['std']) == pytest.approx((data.median(), data.mean(), data.std()))
    assert summary['skewness'] == pytest.approx(stats.skew(data, bias=False))


def test_class_imbalance_note(workspace, tmp_path):
    path = tmp_path / 'rare.csv'
    path.write_text('a,flag\n' + ''.join(f'{i % 3},{"rare" if i < 5 else "common"}\n' for i in range(100)))
    workspace.add_file(path, 'rare')
    output = aa.run(workspace, 'rare', target='flag', target_type='binary', positive='rare', save_evidence=False)
    assert any(i['type'] == 'text' and 'below the 10% threshold' in i['text'] for i in output['sections']['target'])


# ---------- stage 5: every field × target combination ----------
def test_categorical_by_binary_has_planted_rates_and_scipy_chi_square(binary):
    data = raw('binary')
    table = rows(binary, 'region-rates').set_index('Category')
    # Planted 60% and 20%; the two planted duplicate rows add one B positive and one C negative.
    assert table.loc['B', 'Positive rate'] == pytest.approx(61 / 101) and table.loc['A', 'Positive rate'] == pytest.approx(0.2)
    expected = data.groupby('region').churn.apply(lambda s: (s == 'Yes').mean())
    assert table['Positive rate'].to_dict() == pytest.approx(expected.to_dict())
    assert table['Share of all positives'].sum() == pytest.approx(1)
    test = rows(binary, 'region-test').iloc[0]
    crosstab = pd.crosstab(data.region, data.churn)
    chi = stats.chi2_contingency(crosstab, correction=False)
    assert (test['Statistic'], test['p-value']) == pytest.approx((chi.statistic, chi.pvalue))
    assert test['Effect size'] == pytest.approx(stats.contingency.association(crosstab, method='cramer'))
    titles = [i['title'] for i in field(binary, 'region')['items'] if i['type'] == 'figure']
    assert any('rows' in t for t in titles) and any('positive rate' in t for t in titles)


def test_numeric_by_binary_matches_scipy(binary):
    data = raw('binary')
    data = data[data.amount != 'N/A'].assign(amount=lambda d: d.amount.astype(float))
    summary = rows(binary, 'amount-summary').set_index('Class')
    for label, group in data.groupby('churn'):
        assert summary.loc[label, 'Median'] == pytest.approx(group.amount.median())
        assert summary.loc[label, 'Std'] == pytest.approx(group.amount.std())
    expected = stats.mannwhitneyu(data.amount[data.churn == 'Yes'], data.amount[data.churn == 'No'], method='asymptotic')
    test = rows(binary, 'amount-test').iloc[0]
    assert (test['Statistic'], test['p-value']) == pytest.approx((expected.statistic, expected.pvalue))
    n1, n2 = (data.churn == 'Yes').sum(), (data.churn == 'No').sum()
    assert test['Effect size'] == pytest.approx(2 * expected.statistic / (n1 * n2) - 1) and test['Effect size'] > 0
    bins = rows(binary, 'amount-bins')
    assert bins.Rows.sum() == len(data) and bins.Positives.sum() == (data.churn == 'Yes').sum()


def test_date_field_by_binary_target(binary):
    periods = rows(binary, 'local-date-periods')
    assert periods.Rows.sum() == 402 and 'Positive rate' in periods.columns


def test_categorical_and_numeric_by_multiclass_match_scipy(multiclass):
    data = raw('multiclass')
    colour = rows(multiclass, 'colour-test').iloc[0]
    chi = stats.chi2_contingency(pd.crosstab(data.colour, data.level), correction=False)
    assert (colour['Statistic'], colour['p-value']) == pytest.approx((chi.statistic, chi.pvalue))
    shares = rows(multiclass, 'colour-classes').set_index('Category')
    assert shares.loc['red', 'low share'] == pytest.approx(pd.crosstab(data.colour, data.level, normalize='index').loc['red', 'low'])
    size = rows(multiclass, 'size-test').iloc[0]
    groups = [data['size'].astype(float)[data.level == level] for level in ('high', 'low', 'mid')]
    kruskal = stats.kruskal(*groups)
    assert (size['Statistic'], size['p-value']) == pytest.approx((kruskal.statistic, kruskal.pvalue))
    assert size['Effect size'] == pytest.approx(kruskal.statistic / (len(data) - 1))
    assert any(i['title'] == 'colour: class shares' for i in field(multiclass, 'colour')['items'])


def test_numeric_target_comparisons_match_scipy(numeric):
    data = raw('numeric').astype({'x': float, 'y': float})
    correlation = rows(numeric, 'x-test').set_index('Test')
    spearman = stats.spearmanr(data.x, data.y); pearson = stats.pearsonr(data.x, data.y)
    assert correlation.loc['Spearman rank correlation', 'Statistic'] == pytest.approx(spearman.statistic)
    assert correlation.loc['Spearman rank correlation', 'p-value'] == pytest.approx(spearman.pvalue)
    assert correlation.loc['Pearson correlation (reference)', 'Statistic'] == pytest.approx(pearson.statistic)
    group = rows(numeric, 'group-test').iloc[0]
    kruskal = stats.kruskal(*[data.y[data.group == g] for g in ('east', 'north', 'south')])
    assert (group['Statistic'], group['p-value']) == pytest.approx((kruskal.statistic, kruskal.pvalue))
    medians = rows(numeric, 'group-summary').set_index('Category').Median
    assert medians.to_dict() == pytest.approx(data.groupby('group').y.median().to_dict())


def test_time_axis_periods_and_completeness(numeric):
    data = raw('numeric').assign(day=lambda d: pd.to_datetime(d.day), y=lambda d: d.y.astype(float))
    counts = rows(numeric, 'day-periods-time')
    expected = data.groupby(data.day.dt.to_period('M')).size()  # a 718-day range is grouped by month
    assert counts.Rows.tolist() == expected.tolist()
    assert counts.Flags.iloc[0].startswith('incomplete: starts 2022-01-15') and counts.Flags.iloc[-1].startswith('incomplete: ends')
    medians = rows(numeric, 'day-periods-time-target')
    assert medians['Median y'].tolist() == pytest.approx(data.groupby(data.day.dt.to_period('M')).y.median().tolist())


def test_skipped_fields_and_reasons(binary):
    reasons = dict(binary['skipped'])
    assert set(reasons) == {'id', 'notes', 'constant', 'ambiguous_date', 'signup'}
    assert reasons['signup'] == 'used as the time axis' and reasons['id'].startswith('identifier-like')


def test_high_cardinality_categories_are_grouped_as_other(workspace, tmp_path):
    path = tmp_path / 'many.csv'
    path.write_text('code,flag\n' + ''.join(f'k{i % 45},{"y" if i % 3 == 0 else "n"}\n' for i in range(450)))
    workspace.add_file(path, 'many')
    output = aa.run(workspace, 'many', target='flag', target_type='binary', positive='y', save_evidence=False)
    table = item(output, 'code-rates')
    assert len(table['rows']) == 31 and table['rows'][-1][0] == '(other: 15 categories)' and '30 most frequent' in table['note']
    assert sum(r[1] for r in table['rows']) == 450


# ---------- stages 6–8 ----------
def test_ranking_orders_by_effect_and_adjusts_p_values(binary):
    ranking = rows(binary, 'ranking')
    effects = ranking[["Cramér's V", '|rank-biserial r|', 'epsilon-squared', '|Spearman ρ|']].max(axis=1)
    assert effects.is_monotonic_decreasing
    assert ranking['Benjamini–Hochberg adjusted p'].tolist() == pytest.approx(list(stats.false_discovery_control(ranking['p-value'])))
    assert 'not causes' in item(binary, 'ranking')['title']


def test_relationships_matrix_and_pairs(numeric, descriptive):
    pairs = rows(numeric, 'relationships-pairs')
    assert set(pairs['Measure']) == {'correlation ratio η'}
    data = raw('numeric').assign(x=lambda d: d.x.astype(float))
    between = sum(len(g) * (g.mean() - data.x.mean()) ** 2 for _, g in data.groupby('group').x)
    assert pairs.Value.iloc[0] == pytest.approx((between / ((data.x - data.x.mean()) ** 2).sum()) ** .5)
    matrix = item(descriptive, 'relationships-matrix')
    assert len(matrix['rows']) == len(matrix['columns']) - 1 >= 2


def test_descriptive_report_without_target(descriptive):
    assert statuses(descriptive)['ranking'] == 'skipped' and statuses(descriptive)['target_overview'] == 'skipped'
    region = rows(descriptive, 'region-counts').set_index('Category')
    assert region.Rows.to_dict() == raw('binary').region.value_counts().to_dict()
    tenure = rows(descriptive, 'tenure-summary').iloc[0]
    assert tenure['median'] == pytest.approx(raw('binary').tenure.astype(float).median())


# ---------- robustness ----------
def test_every_stage_records_its_status_and_time(binary):
    assert statuses(binary) == {'profile': 'done', 'target_selection': 'done', 'corrections': 'done', 'target_overview': 'done',
                                'fields': 'done', 'ranking': 'done', 'relationships': 'done', 'time': 'done', 'evidence': 'done'}
    assert all(s['seconds'] >= 0 for s in binary['stages'])


def test_a_failed_stage_is_reported_and_later_stages_continue(workspace, monkeypatch):
    monkeypatch.setattr(aa, 'target_overview', lambda *args: (_ for _ in ()).throw(RuntimeError('planted failure')))
    output = aa.run(workspace, 'multiclass', target='level', target_type='multiclass', save_evidence=False)
    failed = next(s for s in output['stages'] if s['key'] == 'target_overview')
    assert failed['status'] == 'failed' and failed['error'] == 'RuntimeError: planted failure'
    assert statuses(output)['fields'] == 'done' and statuses(output)['ranking'] == 'done'


def test_a_failed_field_is_listed(workspace, monkeypatch):
    real = aa.categorical_multiclass
    monkeypatch.setattr(aa, 'categorical_multiclass', lambda a, spec, name, output: (_ for _ in ()).throw(ValueError('field broke')))
    output = aa.run(workspace, 'multiclass', target='level', target_type='multiclass', save_evidence=False)
    assert output['field_failures'] == [['colour', 'ValueError: field broke']]
    assert statuses(output)['fields'] == 'done with errors'


@pytest.mark.parametrize('kwargs,message', [
    (dict(target='missing'), 'existing target'), (dict(target='churn', target_type='numeric'), 'can be analysed as: binary'),
    (dict(target='churn', target_type='binary', positive='Maybe'), 'positive class'),
    (dict(target='id', target_type='binary'), 'nothing'), (dict(time_axis='region'), 'time axis must be a date')])
def test_invalid_choices_stop_before_analysis(workspace, kwargs, message):
    output = aa.run(workspace, 'binary', save_evidence=False, **kwargs)
    selection = next(s for s in output['stages'] if s['key'] == 'target_selection')
    assert selection['status'] == 'failed' and message in selection['error']
    assert all(s['status'] == 'skipped' for s in output['stages'][2:])


def test_runs_are_deterministic(workspace):
    first = aa.run(workspace, 'binary', target='churn', target_type='binary', positive='Yes', time_axis='signup', save_evidence=False)
    second = aa.run(workspace, 'binary', target='churn', target_type='binary', positive='Yes', time_axis='signup', save_evidence=False)
    one, two = report_tables(first), report_tables(second)
    assert [(i, t) for i, t, _ in one] == [(i, t) for i, t, _ in two]
    for (_, _, a), (_, _, b) in zip(one, two):
        a = a.replace({first['analysis_version']: 'V'}); b = b.replace({second['analysis_version']: 'V'})
        pd.testing.assert_frame_equal(a, b)


def test_charts_are_saved_as_replayable_evidence(workspace, binary):
    blocks = {n: b for n, b in workspace.saved_objects('block').items() if b.get('automatic_analysis') == binary['run_id']}
    assert len(blocks) == binary['saved_blocks'] > 10
    block = blocks[next(n for n in blocks if 'region: positive rate' in n)]
    engine, _ = replay(workspace, block['analysis'])
    assert engine.info['current'] == binary['analysis_version'] == block['evidence']['version']
    assert workspace.info('binary')['current'] == binary['original_version']


def test_runs_persist_and_reload(workspace, binary):
    aa.save_run(workspace, binary)
    assert aa.list_runs(workspace)[0]['run_id'] == binary['run_id']
    assert aa.load_run(workspace, binary['run_id'])['sections']['ranking'] == binary['sections']['ranking']
    with pytest.raises(ValueError):
        aa.load_run(workspace, '../../etc/passwd')


def test_no_rows_are_loaded_into_pandas(workspace, monkeypatch):
    seen = []
    real = aa.Analysis.run
    monkeypatch.setattr(aa.Analysis, 'run', lambda self, sql, computation, limit=50_000: seen.append(limit) or real(self, sql, computation, limit=limit))
    aa.run(workspace, 'numeric', target='y', target_type='numeric', save_evidence=False)
    assert max(seen) <= aa.SETTINGS['scatter_sample_rows'] * 10
