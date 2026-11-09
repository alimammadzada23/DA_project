"""fill_missing and batch preparation: every strategy, condition, indicator, ledger and refusal."""
import pandas as pd
import pytest

from insightpilot.universal import Workspace


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / 'customers.csv'
    path.write_text('id,tenure,charges,plan,score\n'
                    '1,0,,basic,3\n'
                    '2,5,10.5,premium,\n'
                    '3,0, ,basic,5\n'
                    '4,7,20.5,,5\n'
                    '5,2,30,basic,4\n')
    workspace = Workspace.create(tmp_path / 'w')
    workspace.add_file(path, 'customers')
    workspace.transform('customers', {'kind': 'batch', 'steps': [
        {'kind': 'numeric', 'column': 'tenure'}, {'kind': 'numeric', 'column': 'charges'}, {'kind': 'numeric', 'column': 'score'}]})
    return workspace


def values(workspace, *columns):
    with workspace.connect() as con:
        return con.execute(f'SELECT {",".join(columns)} FROM "{workspace.physical("customers")}" ORDER BY _row_id').fetchall()


def fill(workspace, **operation):
    return workspace.transform('customers', {'kind': 'fill_missing', **operation})


def test_batch_creates_one_version_with_a_receipt_per_step(workspace):
    info = workspace.info('customers')
    assert len(info['versions']) == 2
    version = info['versions'][-1]
    assert [step['column'] for step in version['operation']['steps']] == ['tenure', 'charges', 'score']
    assert all(step['errors'] == 0 for step in version['operation']['steps'])
    assert values(workspace, 'charges') == [(None,), (10.5,), (None,), (20.5,), (30.0,)]


def test_batch_ledgers_conversion_errors_by_step(tmp_path):
    path = tmp_path / 'x.csv'; path.write_text('a,b\n1, x \n2,3\nbad,4\n')
    workspace = Workspace.create(tmp_path / 'w'); workspace.add_file(path, 'x')
    version = workspace.transform('x', {'kind': 'batch', 'steps': [
        {'kind': 'trim', 'column': 'b'}, {'kind': 'numeric', 'column': 'a'}, {'kind': 'numeric', 'column': 'b'}]})
    assert version['errors'] == 2 and [s['errors'] for s in version['operation']['steps']] == [0, 1, 1]
    ledger = workspace.conversion_errors('x')
    assert ledger.to_dict('records') == [{'_row_id': 1, 'original_value': 'x', 'column_name': 'b', 'step': 3},
                                         {'_row_id': 3, 'original_value': 'bad', 'column_name': 'a', 'step': 2}]


@pytest.mark.parametrize('steps,message', [([], '1–500'), ([{'kind': 'deduplicate'}], 'only numeric'),
                                           ([{'kind': 'fill_missing', 'column': 'tenure'}], 'only numeric')])
def test_batch_refusals(workspace, steps, message):
    with pytest.raises(ValueError, match=message):
        workspace.transform('customers', {'kind': 'batch', 'steps': steps})


@pytest.mark.parametrize('strategy,expected', [('median', 20.5), ('mean', (10.5 + 20.5 + 30) / 3)])
def test_median_and_mean_use_nonmissing_values(workspace, strategy, expected):
    version = fill(workspace, column='charges', strategy=strategy)
    assert version['operation']['fill_value'] == pytest.approx(expected) and version['filled'] == 2
    assert values(workspace, 'charges')[0][0] == pytest.approx(expected) and values(workspace, 'charges')[2][0] == pytest.approx(expected)
    assert workspace.filled_rows('customers')['_row_id'].tolist() == [1, 3]


def test_mode_breaks_ties_by_value_and_records_it(workspace):
    version = fill(workspace, column='score', strategy='mode')
    assert version['operation']['fill_value'] == '5.0' and version['operation']['mode_tied'] is False
    assert values(workspace, 'score')[1] == (5.0,)


def test_text_mode_and_constant(workspace):
    version = fill(workspace, column='plan', strategy='mode')
    assert version['operation']['fill_value'] == 'basic' and values(workspace, 'plan')[3] == ('basic',)
    fill(workspace, column='charges', strategy='constant', value='0')
    assert values(workspace, 'charges') == [(0.0,), (10.5,), (0.0,), (20.5,), (30.0,)]


def test_condition_fills_only_matching_rows(workspace):
    version = fill(workspace, column='charges', strategy='constant', value='0', condition={'column': 'tenure', 'value': '0'})
    assert version['filled'] == 2 and workspace.filled_rows('customers')['_row_id'].tolist() == [1, 3]
    workspace.transform('customers', {'kind': 'batch', 'steps': [{'kind': 'nullify', 'column': 'plan', 'value': 'premium'}]})
    version = fill(workspace, column='plan', strategy='constant', value="o'brien", condition={'column': 'id', 'value': '2'})
    assert version['filled'] == 1 and values(workspace, 'plan')[1] == ("o'brien",)


def test_condition_values_are_data_not_sql(workspace):
    with pytest.raises(ValueError, match='Nothing to fill'):
        fill(workspace, column='plan', strategy='constant', value='x', condition={'column': 'id', 'value': "1' OR '1'='1"})


def test_indicator_is_computed_before_filling(workspace):
    fill(workspace, column='charges', strategy='constant', value='0', condition={'column': 'tenure', 'value': '0'}, indicator=True)
    assert workspace.columns('customers')[-1] == 'charges_was_missing'
    assert values(workspace, 'charges_was_missing') == [(1,), (0,), (1,), (0,), (0,)]


def test_integer_column_gets_a_fractional_fill_only_with_a_recorded_type_change(tmp_path):
    path = tmp_path / 'n.parquet'
    pd.DataFrame({'n': pd.array([1, 2, None], dtype='Int64')}).to_parquet(path)
    workspace = Workspace.create(tmp_path / 'w'); workspace.add_file(path, 'n')
    version = workspace.transform('n', {'kind': 'fill_missing', 'column': 'n', 'strategy': 'mean'})
    assert version['operation']['type_change'] == 'BIGINT → DOUBLE' and version['operation']['fill_value'] == 1.5


def test_original_version_stays_available(workspace):
    before = workspace.info('customers')['current']
    fill(workspace, column='charges', strategy='median')
    workspace.activate('customers', before)
    assert values(workspace, 'charges')[0] == (None,)


@pytest.mark.parametrize('operation,message', [
    ({'column': 'missing_column', 'strategy': 'mean'}, 'existing column'),
    ({'column': 'charges', 'strategy': 'interpolate'}, 'fill strategy'),
    ({'column': 'tenure', 'strategy': 'mean'}, 'Nothing to fill'),
    ({'column': 'plan', 'strategy': 'median'}, 'needs a numeric column'),
    ({'column': 'plan', 'strategy': 'mean'}, 'needs a numeric column'),
    ({'column': 'charges', 'strategy': 'constant'}, 'constant value'),
    ({'column': 'charges', 'strategy': 'constant', 'value': 'abc'}, 'not a valid value'),
    ({'column': 'charges', 'strategy': 'constant', 'value': '1', 'condition': {'column': 'charges', 'value': '1'}}, 'different existing column'),
    ({'column': 'charges', 'strategy': 'constant', 'value': '1', 'condition': {'column': 'tenure', 'value': ''}}, 'value to compare'),
    ({'column': 'charges', 'strategy': 'constant', 'value': '1', 'condition': {'column': 'tenure', 'value': 'x'}}, 'must be a number'),
    ({'column': 'charges', 'strategy': 'constant', 'value': '1', 'condition': {'column': 'tenure', 'value': '99'}}, 'where the condition holds'),
])
def test_refusals_are_explained_and_change_nothing(workspace, operation, message):
    before = workspace.info('customers')
    with pytest.raises(ValueError, match=message):
        fill(workspace, **operation)
    assert workspace.info('customers') == before


def test_indicator_name_collision_is_refused(workspace):
    fill(workspace, column='charges', strategy='constant', value='0', condition={'column': 'tenure', 'value': '0'}, indicator=True)
    workspace.transform('customers', {'kind': 'batch', 'steps': [{'kind': 'nullify', 'column': 'charges', 'value': '10.5'}]})
    with pytest.raises(ValueError, match='already exists'):
        fill(workspace, column='charges', strategy='constant', value='1', indicator=True)
