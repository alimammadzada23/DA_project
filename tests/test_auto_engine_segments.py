"""Phase 3 planted-truth fixtures (spec §9.2 #5, #6, #8): confounding, interactions and validated segments."""
from pathlib import Path

import pytest

from insightpilot.universal import Workspace
from insightpilot.universal import auto_analysis as aa
from insightpilot.universal.auto import confounding, segments

FIXTURES = Path(__file__).parent / 'fixtures'


@pytest.fixture(scope='module')
def runs(tmp_path_factory):
    workspace = Workspace.create(tmp_path_factory.mktemp('engine3') / 'w'); output = {}
    for key in ('05-simpson', '06-interaction', '08-null'):
        table = 't' + key[:2]
        workspace.add_file(FIXTURES / f'ENGINE-{key}.csv', table, provenance={'origin': 'synthetic'})
        output[key[:2]] = aa.run(workspace, table, target='outcome', target_type='binary', positive='yes', save_evidence=False)
    return output


def test_every_stage_completes(runs):
    for output in runs.values():
        assert not [s for s in output['stages'] if s['status'] == 'failed']


def test_simpson_reversal_is_flagged_with_its_strata(runs):
    output = runs['05']
    check = next(c for c in output['confounding'] if c['field'] == 'treatment' and c['stratifier'] == 'stratum')
    assert check['flag'] == 'reverses'
    assert (check['crude'] - 1) * (check['stratified'] - 1) < 0 and (check['low'] > 1 or check['high'] < 1)
    assert len(check['strata']) == 2
    for stratum, exposed, other, odds in check['strata']:
        assert (odds - 1) * (check['stratified'] - 1) > 0      # every stratum points the same way as the pooled estimate
    assert not [c for c in output['confounding'] if c['flag'] and 'noise' in (c['field'], c['stratifier'])]


def test_xor_interaction_is_found_and_segments_capture_it(runs):
    output = runs['06']
    reported = [(i['first'], i['second']) for i in output['interactions'] if i['reported']]
    assert [set(pair) for pair in reported] == [{'a', 'b'}]
    rules = {s['rule']: s for s in output['segments']}
    assert any(s['direction'] == 'lower' and 'a = off' in r and 'b = down' in r for r, s in rules.items())
    assert any(s['direction'] == 'lower' and 'a = on' in r and 'b = up' in r for r, s in rules.items())
    high = [s for s in output['segments'] if s['direction'] == 'higher']
    assert high and high[0]['rate'] > 0.7 and 'c' not in high[0]['rule'].replace('c ', '')


def test_null_data_has_no_signal_no_interactions_and_no_segments(runs):
    output = runs['08']
    assert output['multivariate']['no_signal'] is True
    assert not [i for i in output['interactions'] if i['reported']]
    assert output['segments'] == []
    assert not [c for c in output['confounding'] if c['flag'] == 'reverses']
    ranking = next(i for i in output['sections']['ranking'] if i['id'] == 'ranking')
    assert min(row[-3] for row in ranking['rows']) > 0.05        # Benjamini–Hochberg adjusted p


def test_kept_segments_meet_the_validation_rules(runs):
    from insightpilot.universal.auto.config import DEFAULT
    for output in runs.values():
        for s in output['segments']:
            assert s['held_out_rows'] >= DEFAULT.small_group_rows
            assert s['held_out_lift'] >= DEFAULT.segment_lift_minimum or s['held_out_lift'] <= 1 / DEFAULT.segment_lift_minimum
            assert s['rows'] > 0 and s['low'] <= s['rate'] <= s['high']


def test_odds_ratio_and_change_flags():
    assert confounding.odds_ratio([[20, 10], [10, 20]]) == pytest.approx(4.0)
    assert confounding.odds_ratio([[5, 0], [3, 4]]) == pytest.approx((5.5 * 4.5) / (0.5 * 3.5))
    assert confounding.change_flag(1.0, -0.5, True, True) == 'reverses'
    assert confounding.change_flag(1.0, 0.4, True, True) == 'drops by more than half'
    assert confounding.change_flag(1.0, 0.8, True, True) == ''


def test_rule_text_and_sql_are_consistent():
    conditions = segments.simplify([('Contract', '≠', 'Month-to-month'), ('Contract', '≠', 'One year'), ('tenure', '≤', 20.0, False),
                                    ('tenure', '≤', 10.0, False), (('a', 'b'), 'not both', ('x', 'y')), (('a', 'b'), 'both', ('z', 'w'))])
    assert segments.describe(conditions) == 'Contract not Month-to-month or One year AND a = z AND b = w AND tenure ≤ 10'
    sql = segments.to_sql(conditions, lambda c: f'"{c}" IS NULL')
    assert sql.count('Month-to-month') == 1 and '<= 10.0' in sql and 'NOT' in sql
