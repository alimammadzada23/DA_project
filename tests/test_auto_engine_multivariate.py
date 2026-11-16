"""Phase 2 planted-truth fixtures (spec §9.2 #2, #4, #7, #17): leakage, grouped importance, shapes, imbalance."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from insightpilot.universal import Workspace
from insightpilot.universal import auto_analysis as aa
from insightpilot.universal.auto import multivariate as mv

FIXTURES = Path(__file__).parent / 'fixtures'


@pytest.fixture(scope='module')
def runs(tmp_path_factory):
    workspace = Workspace.create(tmp_path_factory.mktemp('engine2') / 'w'); output = {}
    for key in ('02-leakage', '04-redundancy', '07-nonlinear', '17-imbalance'):
        table = 't' + key[:2]
        workspace.add_file(FIXTURES / f'ENGINE-{key}.csv', table, provenance={'origin': 'synthetic'})
        output[key[:2]] = aa.run(workspace, table, target='outcome', target_type='binary', positive='yes', save_evidence=False)
    return output


def table(output, item_id):
    item = next(i for i in aa.iter_items(output) if i.get('id') == item_id)
    return pd.DataFrame(item['rows'], columns=[c['name'] for c in item['columns']])


def test_every_stage_completes_and_results_are_plain_json(runs):
    for output in runs.values():
        assert not [s for s in output['stages'] if s['status'] == 'failed']
        assert json.loads(json.dumps(output['multivariate'])) == output['multivariate']


def test_leakage_suspects_are_flagged_and_excluded(runs):
    output = runs['02']
    suspects = {s['field']: s['reasons'] for s in output['leakage']}
    assert set(suspects) == {'copy', 'closed_on'}
    assert any('AUC' in r for r in suspects['copy']) and any("'closed'" in r for r in suspects['closed_on'])
    assert output['multivariate']['fields'] == ['x', 'region']
    assert sum('Possible leakage' in w for w in output['warnings']) == 2
    ranking = table(output, 'ranking').set_index('Field')
    assert ranking.loc['copy', 'Leakage suspect'].startswith('yes')


def test_grouped_importance_matches_the_driver_alone(runs):
    output = runs['04']
    importance = {row[0]: row[2] for row in output['multivariate']['importance']}
    single = table(output, 'ranking').set_index('Field').loc['x1', 'Predictive power']
    assert importance['cluster: x1, x2'] == pytest.approx(single, abs=0.06)
    assert importance['x1'] < importance['cluster: x1, x2'] and importance['x2'] < 0.05
    verdicts = {v[0]: v[8] for v in output['multivariate']['verdicts']}
    assert verdicts['x1'] == 'holds' and verdicts['x2'] == 'represented by x1 in the adjusted model'


def test_model_beats_baseline_with_fold_spread(runs):
    metrics = {row[0]: row for row in runs['04']['multivariate']['metrics']}
    auc = metrics['AUC']
    assert auc[1] > 0.7 and auc[3] == 0.5 and auc[2] is not None and len(auc[4].split(', ')) == 5
    assert metrics['Accuracy'][1] > metrics['Accuracy'][3]


def test_u_shape_is_flagged_and_kept_by_the_adjusted_model(runs):
    output = runs['07']
    assert [n['field'] for n in output['nonlinear']] == ['x'] and output['nonlinear'][0]['monotone_share'] < 0.8
    bins = table(output, 'x-bins')['Positive rate'].tolist()
    middle = min(bins); assert bins[0] > 2 * middle and bins[-1] > 2 * middle and bins.index(middle) not in (0, len(bins) - 1)
    verdicts = {v[0]: v[8] for v in output['multivariate']['verdicts']}
    assert verdicts['x'] == 'holds'
    assert 'enter as equal-count bins' in next(i for i in aa.iter_items(output) if i.get('id') == 'multivariate-adjusted')['note']
    assert 'x' in output['multivariate']['ale']


def test_imbalance_is_warned_and_pr_auc_reported(runs):
    output = runs['17']
    assert any(w.startswith('Imbalanced target') for w in output['warnings'])
    metrics = {row[0]: row for row in output['multivariate']['metrics']}
    assert metrics['PR-AUC (average precision)'][3] == pytest.approx(0.011, abs=0.003)
    rates = table(output, 'group-rates').set_index('Category')
    # For rare events, intervals are wide relative to the rate itself.
    assert ((rates['95% CI high'] - rates['95% CI low']) / rates['Positive rate']).loc['a'] > 0.5


def test_vif_reports_exact_collinearity_as_infinite():
    corr = np.array([[1.0, 1.0, 0.0], [1.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    values = mv.vif(corr)
    assert values[0] == float('inf') and values[1] == float('inf') and values[2] == pytest.approx(1.0)


def test_verdict_rules():
    from insightpilot.universal.auto.config import DEFAULT
    base = {'univariate': 100.0, 'adjusted': 64.0, 'p_univariate': 1e-10, 'p_adjusted': 1e-6, 'sign_univariate': '+', 'sign_adjusted': '+'}
    assert mv.lr_verdict(base, DEFAULT) == 'holds'                                  # √0.64 = 80% retained
    assert mv.lr_verdict(base | {'adjusted': 16.0}, DEFAULT) == 'weakens substantially (>50%)'   # √0.16 = 40%
    assert mv.lr_verdict(base | {'p_adjusted': 0.2}, DEFAULT) == 'disappears'
    assert mv.lr_verdict(base | {'sign_adjusted': '−'}, DEFAULT) == 'reverses'
    assert mv.lr_verdict(base | {'p_univariate': 0.3}, DEFAULT) == 'no univariate signal'
