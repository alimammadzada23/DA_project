"""Synthetic deterministic/HTTP-mock regressions, never real Qwen evidence."""
from copy import deepcopy
from types import SimpleNamespace

import duckdb
import pandas as pd
import pytest

from insightpilot.universal import Workspace
from insightpilot.universal.analyst_constraints import explicit_limit, request_constraints
from insightpilot.universal.analyst_plan import ClarificationRequired
from insightpilot.universal.analyst_session import Conversation, analyze_question, save_answer, replay_answer
from insightpilot.universal.context import AnalysisContext
from insightpilot.universal.engine import Engine, frame_of
from insightpilot.universal.metrics import Metric
from test_local_ai_reliability import client_for


@pytest.fixture(params=[('region', 'revenue'), ('customer tier', 'net_cost')])
def model(tmp_path, request):
    group, value = request.param; rows = []
    for name, count, amount in [('A', 9, 10), ('B', 7, 20), ('C', 6, 40), ('D', 4, 60), ('E', 3, 80), ('F', 1, 1000), (None, 3, 120)]:
        for i in range(count): rows.append({group: name, value: None if name == 'A' and i == 0 else amount + i, 'latency': 2000 - amount - i})
    raw = pd.DataFrame(rows); source = tmp_path/'SYNTHETIC.csv'; raw.to_csv(source, index=False)
    workspace = Workspace.create(tmp_path/'workspace'); workspace.add_file(source, 'facts', provenance={'origin': 'synthetic'})
    workspace.approve_semantics('facts', {group: 'category', value: 'measure', 'latency': 'measure'}, 'One synthetic observation.', units={value: 'units', 'latency': 'seconds'})
    engine = Engine(workspace, 'facts', context=AnalysisContext(None, 'month', group))
    return engine, workspace.profile('facts'), Metric('Records'), raw, group, value


def plan(model, **changes):
    return {'version': 1, 'action': 'analyze', 'table': 'facts', 'operation': 'aggregate',
            'group_by': [model[4]], 'measures': [{'aggregation': 'records'}], 'sort_by': 'records',
            'limit': 3, 'descending': True, 'chart': 'bar'} | changes


def ask(model, client, question, conversation=None):
    return analyze_question(*model[:3], question, conversation or Conversation(), mode='local', client=client)


def reference(model, query):
    with duckdb.connect() as connection:
        connection.register('original', model[3]); return connection.execute(query).df()


def quoted(name): return '"' + name.replace('"', '""') + '"'


@pytest.mark.parametrize('question', [
    'Now sort those results by record count from lowest to highest.',
    'Reorder these results by record count in ascending order.',
    'Now order the previous results by record count, increasing.',
])
def test_captured_sort_shape_preserves_membership_without_model_inheritance(model, question):
    # Reconstruct the reported properties; this envelope is a test double,
    # not a new real-model run or a claim to possess omitted response fields.
    bad = plan(model, limit=10); bad.pop('descending')
    client, chats = client_for([plan(model), bad]); conversation = Conversation()
    first = ask(model, client, f'Show the top three {model[4]} groups by record count.', conversation)
    result = ask(model, client, question, conversation)
    group = quoted(model[4]); expected = reference(model, f'WITH selected AS (SELECT {group} AS g,count(*) AS n FROM original GROUP BY 1 ORDER BY n DESC,g ASC NULLS LAST LIMIT 3) SELECT * FROM selected ORDER BY n,g ASC NULLS LAST')
    actual = frame_of(result['primary'])
    assert result['status'] == 'ok' and len(chats) == 2
    assert list(actual.group_0) == list(expected.g) and list(actual.m0) == list(expected.n)
    assert set(actual.group_0) == set(frame_of(first['primary']).group_0)
    assert result['plan']['limit'] == 3 and result['plan']['descending'] is False
    assert result['applied_cohort'] and {'inherit', 'limit', 'descending'} <= set(result['backend']['constraint_repairs'])
    workspace = model[0].workspace; info = workspace.info('facts')
    save_answer(workspace, 'sorted selection', result)
    fresh = Workspace(workspace.directory)
    replay = replay_answer(fresh, fresh.saved_objects('block')['sorted selection'])
    assert replay['primary']['rows'] == result['primary']['rows'] and fresh.info('facts') == info


@pytest.mark.parametrize('word,aggregation', [('average', 'avg'), ('median', 'median'), ('sum', 'sum'), ('minimum', 'min'), ('maximum', 'max'), ('count', 'count'), ('counts of', 'count'), ('count nonmissing', 'count'), ('count distinct', 'distinct'), ('90th percentile', 'quantile')])
def test_explicit_group_measure_repairs_distribution_operation_on_other_fields(model, word, aggregation):
    measure = {'aggregation': aggregation, 'column': model[5]}
    if aggregation == 'quantile': measure['quantile'] = .9
    bad = plan(model, operation='group_distribution', measures=[measure]); bad.pop('limit'); client, chats = client_for([bad])
    result = ask(model, client, f'Show {word} {model[5]} for each {model[4]} category.')
    function = {'count': f'count({quoted(model[5])})', 'distinct': f'count(DISTINCT {quoted(model[5])})', 'quantile': f'quantile_cont({quoted(model[5])},.9)'}.get(aggregation, f'{aggregation}({quoted(model[5])})')
    expected = reference(model, f'SELECT {quoted(model[4])} AS g,{function} AS value FROM original GROUP BY 1 ORDER BY g ASC NULLS LAST')
    actual = frame_of(result['primary']).sort_values('group_0', na_position='last')
    assert result['status'] == 'ok' and len(chats) == 1 and result['plan']['operation'] == 'aggregate'
    assert actual.group_0.fillna('NULL').tolist() == expected.g.fillna('NULL').tolist()
    assert list(actual.m0) == pytest.approx(list(expected.value))
    assert result['backend']['constraint_repairs'] == ['operation']


@pytest.mark.parametrize('wording,limit', [('ten smallest', 10), ('4 lowest', 4), ('twenty-one smallest', 21), ('bottom two', 2)])
def test_smallest_groups_enforce_both_missing_limit_and_direction(model, wording, limit):
    bad = plan(model); bad.pop('limit'); bad.pop('descending')
    client, chats = client_for([bad]); result = ask(model, client, f'Show the {wording} {model[4]} groups by record count.')
    expected = reference(model, f'SELECT {quoted(model[4])} AS g,count(*) AS n FROM original GROUP BY 1 ORDER BY n,g ASC NULLS LAST LIMIT {limit}')
    actual = frame_of(result['primary'])
    assert result['status'] == 'ok' and len(chats) == 1
    assert result['plan']['limit'] == limit and result['plan']['descending'] is False
    assert actual.group_0.fillna('NULL').tolist() == expected.g.fillna('NULL').tolist() and list(actual.m0) == list(expected.n)


def test_subject_named_ranking_uses_the_requested_group_and_measure(model):
    bad = plan(model, operation='group_distribution', measures=[{'aggregation':'avg','column':model[5]}]); bad.pop('limit')
    client, _ = client_for([bad])
    result = ask(model, client, f'Which {model[4]} has the lowest average {model[5]}?')
    expected = reference(model, f'SELECT {quoted(model[4])} AS g,avg({quoted(model[5])}) AS v FROM original GROUP BY 1 ORDER BY v,g ASC NULLS LAST')
    assert result['status'] == 'ok' and result['plan']['sort_by'] == 'm0'
    actual = frame_of(result['primary'])
    assert actual.group_0.fillna('NULL').tolist() == expected.g.fillna('NULL').tolist() and list(actual.m0) == pytest.approx(list(expected.v))


def test_sort_existing_mean_reuses_the_same_selection_and_measure(model):
    spec = {'aggregation': 'avg', 'column': model[5]}
    first = plan(model, measures=[spec], sort_by='m0')
    second = plan(model, measures=[spec], limit=10, sort_by='records')
    client, _ = client_for([first, second]); conversation = Conversation()
    before = ask(model, client, f'Show the top three {model[4]} groups by average {model[5]}.', conversation)
    after = ask(model, client, f'Now sort those results by average {model[5]} ascending.', conversation)
    assert after['status'] == 'ok' and after['plan']['sort_by'] == 'm0'
    assert set(frame_of(after['primary']).group_0) == set(frame_of(before['primary']).group_0)
    assert list(frame_of(after['primary']).m0) == sorted(frame_of(before['primary']).m0)


def test_limit_only_followup_binds_previous_inputs_even_when_model_omits_inherit(model):
    bad = {'version': 1, 'action': 'analyze', 'table': 'facts', 'operation': 'aggregate', 'limit': 10}
    client, _ = client_for([plan(model), bad]); conversation = Conversation()
    before = ask(model, client, f'Show the top three {model[4]} groups by record count.', conversation)
    after = ask(model, client, 'Now show only the top two.', conversation)
    assert after['status'] == 'ok' and after['plan']['group_by'] == [model[4]]
    assert list(frame_of(after['primary']).group_0) == list(frame_of(before['primary']).group_0)[:2]
    assert after['applied_cohort']


def test_wrong_requested_measure_gets_at_most_one_model_repair_and_no_wrong_execution(model, monkeypatch):
    bad = plan(model, measures=[{'aggregation': 'avg', 'column': model[5]}])
    good = plan(model, measures=[{'aggregation': 'median', 'column': model[5]}])
    client, chats = client_for([bad, good]); calls = []
    import insightpilot.universal.analyst_session as session
    execute = session.execute_plan
    def capture(*args, **kwargs):
        calls.append(deepcopy(args[2].data)); return execute(*args, **kwargs)
    monkeypatch.setattr(session, 'execute_plan', capture)
    result = ask(model, client, f'Show median {model[5]} by {model[4]}.')
    assert result['status'] == 'ok' and len(chats) == 2 and len(calls) == 1
    assert calls[0]['measures'] == good['measures'] and result['backend']['repair_attempted']


def test_repeated_measure_conflict_is_rejected_without_execution(model, monkeypatch):
    bad = plan(model, measures=[{'aggregation': 'sum', 'column': model[5]}])
    client, chats = client_for([bad, bad])
    monkeypatch.setattr('insightpilot.universal.analyst_session.execute_plan', lambda *a, **k: pytest.fail('Contradictory plan executed'))
    result = ask(model, client, f'Show median {model[5]} by {model[4]}.')
    assert result['status'] == 'error' and not result['evidence'] and len(chats) == 2


@pytest.mark.parametrize('change', ['filter', 'group'])
def test_sort_cannot_add_a_filter_or_replace_grouping(model, monkeypatch, change):
    bad = plan(model, **({'filters':[{'column': model[5], 'operator': 'gt', 'value': 100}]} if change == 'filter' else {'group_by':['latency']}))
    client, chats = client_for([plan(model), bad, bad]); conversation = Conversation()
    ask(model, client, f'Show top three {model[4]} groups by record count.', conversation)
    monkeypatch.setattr('insightpilot.universal.analyst_session.execute_plan', lambda *a, **k: pytest.fail('Invented filter executed'))
    result = ask(model, client, 'Now sort those results by record count ascending.', conversation)
    assert result['status'] == 'error' and len(chats) == 3 and not result['evidence']


def test_referenced_selection_cannot_expand(model):
    client, chats = client_for([plan(model)]); conversation = Conversation()
    ask(model, client, f'Show top three {model[4]} groups by record count.', conversation)
    result = ask(model, client, 'Now show the top ten of those results.', conversation)
    assert result['status'] == 'clarify' and len(chats) == 1 and not result['evidence']


def test_explicit_new_ranking_can_expand_without_reusing_cohort(model):
    client, _ = client_for([plan(model), plan(model, limit=5, inherit=True)]); conversation = Conversation()
    ask(model, client, f'Show top three {model[4]} groups by record count.', conversation)
    result = ask(model, client, f'Show the top five {model[4]} groups by record count.', conversation)
    assert result['status'] == 'ok' and len(result['primary']['rows']) == 5 and not result['applied_cohort']


def test_measure_only_followup_preserves_selection_without_model_inherit(model):
    client, _ = client_for([plan(model), plan(model, measures=[{'aggregation': 'avg', 'column': model[5]}], sort_by='m0')]); conversation = Conversation()
    before = ask(model, client, f'Show top three {model[4]} groups by record count.', conversation)
    after = ask(model, client, f'Instead compare average {model[5]}.', conversation)
    assert after['status'] == 'ok' and after['applied_cohort']
    assert set(frame_of(after['primary']).group_0) == set(frame_of(before['primary']).group_0)


def test_empty_selection_never_becomes_an_unrestricted_followup(model):
    first = plan(model, filters=[{'column': model[4], 'operator': 'eq', 'value': 'not present'}])
    client, chats = client_for([first]); conversation = Conversation()
    result = ask(model, client, f'Count records by {model[4]} where {model[4]} is "not present".', conversation)
    assert result['status'] == 'ok' and not result['primary']['rows']
    after = ask(model, client, 'Now sort those results by record count ascending.', conversation)
    assert after['status'] == 'clarify' and len(chats) == 1 and not after['evidence']


def test_repaired_operation_does_not_approve_unreviewed_numbers(model):
    workspace = model[0].workspace; info = workspace.info('facts'); info['semantics'] = {'reviewed': False}
    with workspace.connect(write=True) as connection: workspace._save_info(connection, 'facts', info)
    model = (Engine(workspace, 'facts', context=model[0].context), *model[1:])
    client, _ = client_for([plan(model, operation='group_distribution', measures=[{'aggregation': 'median', 'column': model[5]}])])
    result = ask(model, client, f'Show median {model[5]} for each {model[4]} category.')
    assert result['status'] == 'ok' and result['evidence'] and workspace.info('facts')['semantics']['reviewed'] is False
    actual=frame_of(result['primary']).sort_values('group_0',na_position='last')
    expected=model[3].groupby(model[4],dropna=False)[model[5]].median()
    assert list(actual.m0)==pytest.approx(list(expected))
    assert result['definitions'][0]['semantic_status']=='technical_descriptive'


def test_new_rate_still_requires_human_approval(model, monkeypatch):
    rate = {'aggregation': 'rate', 'numerator': {'column': model[5], 'operator': 'gt', 'value': 20}, 'denominator': {'kind': 'selected'}}
    client, _ = client_for([plan(model, measures=[rate])])
    monkeypatch.setattr('insightpilot.universal.engine.Engine.run', lambda *a, **k: pytest.fail('Unapproved rate calculated'))
    result = ask(model, client, f'Show the proportion above 20 for {model[5]} by {model[4]}.')
    assert result['status'] == 'approval_required' and not result['evidence']


def test_all_repair_reasons_share_two_total_model_attempts(model, monkeypatch):
    bad = plan(model, measures=[{'aggregation': 'avg', 'column': model[5]}])
    second = plan(model, measures=[{'aggregation': 'median', 'column': model[5]}]); second.pop('operation')
    client, chats = client_for([bad, second])
    monkeypatch.setattr('insightpilot.universal.analyst_session.execute_plan', lambda *a, **k: pytest.fail('Invalid plan executed'))
    result = ask(model, client, f'Show median {model[5]} for each {model[4]} category.')
    assert result['status'] == 'error' and len(chats) == 2 and not result['evidence']


@pytest.mark.parametrize('limit', ['0', '-3', '101', '3.5', 'one hundred one', 'one hundred and one', 'two hundred'])
def test_unsupported_explicit_limits_are_never_clamped_or_defaulted(limit):
    with pytest.raises(ClarificationRequired): explicit_limit('Show top ' + limit + ' groups.', ['segment'])


@pytest.mark.parametrize('word,expected', [('7', 7), ('seventeen', 17), ('twenty one', 21), ('twenty-one', 21), ('ninety nine', 99), ('one hundred', 100)])
def test_numeric_and_written_limits(word, expected):
    assert explicit_limit('Show the top ' + word + ' groups.', ['segment']) == expected


@pytest.mark.parametrize('phrase', ['ascending', 'lowest to highest', 'smallest to largest', 'increasing'])
def test_explicit_order_directions_are_general(phrase):
    constraints = request_constraints(SimpleNamespace(info={'columns': ['segment', 'value']}), f'Count records by segment, {phrase}.')
    assert constraints.descending is False


def test_conflicting_limits_and_selection_order_require_clarification():
    with pytest.raises(ClarificationRequired): explicit_limit('Show top five groups, limit to ten.', ['segment'])
    with pytest.raises(ClarificationRequired): request_constraints(SimpleNamespace(info={'columns': ['segment']}), 'Show top five segment groups sorted lowest to highest.')


def test_filter_literals_are_not_interpreted_as_analytical_constraints():
    constraints = request_constraints(SimpleNamespace(info={'columns': ['segment', 'value']}), 'Count records by segment where value is "top ten median".')
    assert constraints.limit is None and constraints.descending is None
    assert constraints.measures == [{'aggregation': 'records'}]
    quoted = request_constraints(SimpleNamespace(info={'columns': ['segment', 'value']}), 'Compare mean "value" by segment only for segment "top ten median".')
    assert quoted.limit is None and quoted.descending is None
    assert quoted.measures == [{'aggregation': 'avg', 'column': 'value'}]
    filtered = request_constraints(SimpleNamespace(info={'columns':['segment','value']}), 'What is the average value for segment "top ten median"?')
    assert filtered.limit is None and filtered.groups is None and filtered.measures == [{'aggregation':'avg','column':'value'}]


def test_period_comparison_is_not_reinterpreted_as_a_grouped_aggregate():
    constraints = request_constraints(SimpleNamespace(info={'columns': ['segment', 'value']}), 'Compare median value by segment between April and August.')
    assert constraints.operation is None and constraints.measures is None


@pytest.mark.parametrize('question', ['Which segment categories have the most records, and what is their average value?', 'Compare counts and average value by segment.'])
def test_compound_measures_include_requested_counts(question):
    constraints = request_constraints(SimpleNamespace(info={'columns': ['segment', 'value']}), question)
    assert constraints.measures == [{'aggregation': 'records'}, {'aggregation': 'avg', 'column': 'value'}]
    if 'most' in question: assert constraints.sort_records


@pytest.mark.parametrize('question', ['Show average records by segment.', 'Compare mean value and latency by segment.'])
def test_unresolved_requested_statistics_or_inputs_are_not_silently_discarded(question):
    with pytest.raises(ClarificationRequired):
        request_constraints(SimpleNamespace(info={'columns': ['segment', 'value', 'latency']}), question)
