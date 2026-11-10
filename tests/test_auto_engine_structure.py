"""Phase 1 planted-truth fixtures (spec §9.2 #1, #3, #4, #16): structure, ranking and explained missingness."""
from pathlib import Path

import pandas as pd
import pytest

from insightpilot.universal import Workspace
from insightpilot.universal import auto_analysis as aa

FIXTURES = Path(__file__).parent / 'fixtures'


@pytest.fixture(scope='module')
def runs(tmp_path_factory):
    workspace = Workspace.create(tmp_path_factory.mktemp('engine') / 'w'); output = {}
    for key in ('01-conditional', '03-derived', '04-redundancy', '16-missingness'):
        table = 't' + key[:2]
        workspace.add_file(FIXTURES / f'ENGINE-{key}.csv', table, provenance={'origin': 'synthetic'})
        output[key[:2]] = aa.run(workspace, table, target='outcome', target_type='binary', positive='yes', save_evidence=False)
    return output


def ranking(output):
    item = next(i for i in output['sections']['ranking'] if i['id'] == 'ranking')
    return pd.DataFrame(item['rows'], columns=[c['name'] for c in item['columns']])


def items(output, field):
    return {i['id']: i for i in next(f for f in output['sections']['fields'] if f['field'] == field)['items'] if i['type'] != 'text'}


def test_every_stage_completes(runs):
    for output in runs.values():
        assert not [s for s in output['stages'] if s['status'] == 'failed'] and not output['field_failures']


def test_conditional_category_is_found_and_ranked_on_applicable_rows(runs):
    output = runs['01']
    assert [(c['column'], c['level'], c['parent'], c['parent_value']) for c in output['structure']['conditional']] == [
        ('addon', 'No service', 'service', 'none')]
    tests = items(output, 'addon')
    unconditional, applicable = tests['addon-test']['rows'][0][4], tests['addon-test-applicable']['rows'][0][4]
    assert unconditional > 0.2 and applicable < 0.05          # Cramér's V: all rows vs rows where the addon applies
    table = ranking(output).set_index('Field')
    assert table.loc['addon', 'Rows analysed'] == 'service ≠ none'
    assert abs(table.loc['addon', 'Predictive power']) < 0.05 and table.index[0] == 'service'


def test_derived_column_is_reported_once_under_its_sources(runs):
    output = runs['03']
    assert [(d['column'], d['formula']) for d in output['structure']['derived']] == [('c', 'a × b')]
    assert output['structure']['derived'][0]['r2_full'] > 0.99
    assert 'c' not in ranking(output).Field.tolist()
    derived = next(i for i in output['sections']['ranking'] if i['id'] == 'ranking-derived')
    assert [r[0] for r in derived['rows']] == ['c']
    assert all('outcome' not in c['members'] for c in output['structure']['clusters'])
    assert all(c['representative'] != 'c' for c in output['structure']['clusters'])


def test_redundant_fields_share_a_cluster(runs):
    clusters = runs['04']['structure']['clusters']
    assert [(c['members'], c['representative']) for c in clusters] == [(['x1', 'x2'], 'x1')]
    pairs = {(p['first'], p['second']): p['value'] for p in runs['04']['structure']['associations']}
    assert pairs[('x1', 'x2')] > 0.85 and abs(pairs[('x1', 'x3')]) < 0.1


def test_deterministic_missingness_is_explained_and_random_missingness_is_not(runs):
    explained = runs['16']['missing_explanations']
    assert [(e['column'], e['condition_column'], e['condition_value'], e['equivalent']) for e in explained] == [('b', 'a', '0', True)]
    assert not [e for e in explained if e['column'] == 'c']


def test_practical_effects_have_wilson_intervals_and_lift(runs):
    rates = items(runs['01'], 'service')['service-rates']
    frame = pd.DataFrame(rates['rows'], columns=[c['name'] for c in rates['columns']]).set_index('Category')
    none = frame.loc['none']
    from statsmodels.stats.proportion import proportion_confint
    low, high = proportion_confint(none['Positives'], none['Rows'], method='wilson')
    overall = frame.Positives.sum() / frame.Rows.sum()
    assert (none['95% CI low'], none['95% CI high']) == pytest.approx((low, high))
    assert none['Lift'] == pytest.approx(none['Positive rate'] / overall)
    assert none['Difference vs overall (pp)'] == pytest.approx((none['Positive rate'] - overall) * 100)


def test_power_and_config_are_recorded(runs):
    output = runs['04']
    assert output['config_hash'] and output['seed'] == 42 and output['config']['cv_folds'] == 5
    power = items(output, 'x1')['x1-power']['rows'][0]
    assert power[0] == 'ΔAUC' and power[2] == pytest.approx(power[1] + 0.5) and len(power[4].split(', ')) == 5
