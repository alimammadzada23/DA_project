"""Constructed regressions for reported Qwen failures; these are NOT real Qwen outputs."""
import json
import os
from pathlib import Path
import sys

import duckdb
import pytest
from jsonschema import Draft202012Validator

from insightpilot.universal.analyst_plan import PLAN_SCHEMA,MEASURE,PlanError,decode_plan,validate_plan
from insightpilot.universal.analyst_session import analyze_question,Conversation
from insightpilot.universal.engine import frame_of
from insightpilot.universal.metrics import Metric
from insightpilot.universal import Workspace
from test_local_ai_reliability import data,ask,client_for,counted,accept

VALID={
 'records':{'aggregation':'records'},'share':{'aggregation':'share'},
 **{a:{'aggregation':a,'column':'age'} for a in ('count','distinct','sum','avg','median','min','max')},
 'quantile':{'aggregation':'quantile','column':'age','quantile':.5},
 'saved':{'aggregation':'saved','metric':'Records'},
 'rate':{'aggregation':'rate','numerator':{'column':'age','operator':'gte','value':30},'denominator':{'kind':'selected'}}}
EXTRAS={'column':'age','metric':'Records','quantile':.5,'numerator':{'column':'age','operator':'gt','value':30},'denominator':{'kind':'selected'}}
BAD=[(a,key,value) for a,spec in VALID.items() for key,value in EXTRAS.items() if key not in spec]


@pytest.mark.parametrize('aggregation',list(VALID))
def test_legal_measure_arguments_pass_schema_and_live_validation(data,aggregation):
    e,p,m,_=data;measure=VALID[aggregation];value=counted(measures=[measure])
    assert Draft202012Validator(MEASURE).is_valid(measure)
    validated=validate_plan(e,p,value,m)
    assert validated.data['measures']==[measure]
    assert validated.approval_required==(aggregation=='rate')


@pytest.mark.parametrize('aggregation,key,value',BAD)
def test_irrelevant_known_properties_are_rejected_by_the_producer_schema(aggregation,key,value):
    measure=VALID[aggregation]|{key:value};value=counted(measures=[measure])
    assert not Draft202012Validator(MEASURE).is_valid(measure)
    assert not Draft202012Validator(PLAN_SCHEMA).is_valid(value)
    with pytest.raises(PlanError):decode_plan(json.dumps(value))


@pytest.mark.parametrize('aggregation',list(VALID))
def test_required_arguments_cannot_be_omitted(aggregation):
    for key in VALID[aggregation]:
        value=dict(VALID[aggregation]);value.pop(key)
        assert not Draft202012Validator(MEASURE).is_valid(value)


@pytest.mark.parametrize('denominator',[
 {'kind':'selected','column':'age'}, {'kind':'selected','condition':{'column':'age','operator':'eq','value':30}},
 {'kind':'nonmissing'}, {'kind':'condition'}, {'kind':'nonmissing','column':'age','condition':{'column':'age','operator':'eq','value':30}},
])
def test_rate_denominators_have_exact_variant_arguments(denominator):
    with pytest.raises(PlanError):decode_plan(json.dumps(counted(measures=[VALID['rate']|{'denominator':denominator}])))


@pytest.mark.parametrize('operation',['missing','schema','records'])
def test_missing_exclusion_wrong_operation_is_never_executed_and_gets_one_repair(data,operation):
    question='How many records remain if you exclude missing age values?'
    wrong=counted(operation=operation,group_by=[],measures=[],fields=['age'] if operation!='records' else [],chart='table')
    correct=counted(group_by=[],filters=[{'column':'age','operator':'not_missing'}],chart='table')
    client,chats=client_for([wrong,correct]);result=ask(data,client,question)
    with duckdb.connect() as con:
        con.register('fixture',data[3]);expected=con.execute('SELECT count(*) FROM fixture WHERE age IS NOT NULL').fetchone()[0]
    assert result['status']=='ok' and result['plan']['operation']=='aggregate'
    assert result['primary']['rows'][0][0]==expected==69 and len(chats)==2
    assert result['selected_rows']==69 and result['backend']['repair_attempted']


def test_repeated_wrong_missing_scope_fails_without_a_calculation(data,monkeypatch):
    wrong=counted(operation='missing',group_by=[],measures=[],fields=['age'],chart='table')
    client,chats=client_for([wrong,wrong])
    monkeypatch.setattr('insightpilot.universal.engine.Engine.run',lambda *a,**k:pytest.fail('Wrong calculation ran'))
    result=ask(data,client,'How many records remain if you exclude missing age values?')
    assert result['status']=='error' and not result['evidence'] and len(chats)==2


@pytest.mark.parametrize('question',[
 'Create a new data file on disk containing this table.', 'Write the results to /tmp/report',
 'Save the selected rows to output.csv.', 'Overwrite an existing file with this table.',
 'Append these rows to a JSON file.', 'Modify the configuration file.',
 'Rename this file.', 'The file should be deleted.',
 'Create a bar chart file.', 'Create a bar chart at /tmp/chart.png',
])
def test_write_requests_are_refused_before_model_or_calculator(data,monkeypatch,question):
    client,chats=client_for([])
    monkeypatch.setattr('insightpilot.universal.engine.Engine.run',lambda *a,**k:pytest.fail('Write request became a calculation'))
    result=ask(data,client,question)
    assert result['status']=='unsupported' and not chats and not result['evidence']


@pytest.mark.parametrize('question',[
 'Create a bar chart of counts by file type.', 'Create a chart of disk usage by category.',
 'How many files were created last month?', 'Compare file modification events by category.',
 'Create a histogram of file sizes.',
])
def test_readonly_file_metadata_and_chart_requests_are_not_classified_as_writes(question):
    from insightpilot.universal.analyst_planner import guard_question
    assert guard_question(question,'facts') is None


def test_explicit_unavailable_field_is_clarified_before_model_or_count(data,monkeypatch):
    client,chats=client_for([])
    monkeypatch.setattr('insightpilot.universal.engine.Engine.run',lambda *a,**k:pytest.fail('Absent field became a row count'))
    result=ask(data,client,'Summarize the unavailable field called absent_field.')
    assert result['status']=='clarify' and not chats and not result['evidence']


def test_legacy_external_entrypoint_also_refuses_file_creation(data,monkeypatch):
    from insightpilot.universal.assistant import answer
    monkeypatch.setattr('insightpilot.universal.assistant.provider_plan',lambda *a,**k:pytest.fail('File request reached external provider'))
    with pytest.raises(ValueError,match='read-only'):answer(data[0],'Create a data file on disk.',Metric('Records'),use_provider=True,consent=True)


def diagnostic(data):
    sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
    from synthetic_plan_diagnostics import SyntheticPlanDiagnostics,CASE_IDS
    path=data[0].workspace.directory.parent.parent/'rejected-plans.jsonl'
    return SyntheticPlanDiagnostics(data[0],path,CASE_IDS)


def test_opt_in_records_actual_mock_argument_shape_and_rejection(data):
    observer=diagnostic(data);observer.begin_case('top5')
    # Deliberately constructed: never attributed to the user's real Qwen run.
    bad=counted(measures=[{'aggregation':'records','column':'job','metric':'Records','quantile':.5}])
    client,_=client_for([bad]);client.observer=observer;result=ask(data,client)
    events=[json.loads(line) for line in observer.path.read_text().splitlines()]
    captured=next(e for e in events if e['event']=='model_response')
    assert captured['plan_projection']['measures']==bad['measures']
    assert captured['measure_signatures'][0]['unexpected_known_keys']==['column','metric','quantile']
    assert any(e.get('reason')=='measure_arguments' for e in events)
    assert result['status']=='error' and not result['evidence']
    assert result['backend']['model_response_received'] is True and result['backend']['model_request_completed'] is False
    assert os.stat(observer.path).st_mode&0o777==0o600


def test_diagnostics_redact_unknown_values_keys_and_free_text(data):
    observer=diagnostic(data);observer.begin_case('count');observer.begin_attempt(1)
    marker='private_test_marker_never_log'
    observer.capture_response(json.dumps({'version':1,'action':'analyze','table':'facts','operation':'aggregate','measures':[{'aggregation':'records','metric':marker}],marker:marker,'message':marker,'filters':[{'column':marker,'operator':'eq','value':marker}]}))
    observer.capture_response('invalid JSON '+marker)
    assert marker not in observer.path.read_text()
    assert '<text redacted>' in observer.path.read_text() and 'invalid JSON omitted' in observer.path.read_text()


def test_diagnostics_cannot_be_enabled_on_real_or_merely_labelled_synthetic_data(tmp_path):
    sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
    from synthetic_plan_diagnostics import SyntheticPlanDiagnostics,CASE_IDS
    directory=tmp_path/'fixture';directory.mkdir();source=directory/'private.csv'
    source.write_text('job,age,education\nprivate_test_marker,40,Private\n')
    workspace=Workspace.create(directory/'workspace');workspace.add_file(source,'facts',provenance={'origin':'synthetic'})
    from insightpilot.universal.engine import Engine
    output=tmp_path/'rejected-plans.jsonl'
    with pytest.raises(ValueError,match='byte-verified'):SyntheticPlanDiagnostics(Engine(workspace,'facts'),output,CASE_IDS)
    assert not output.exists()


def test_normal_local_analysis_creates_no_diagnostic_log(data):
    client,_=client_for([counted()]);assert client.observer is None
    assert ask(data,client)['status']=='ok'
    assert not list(data[0].workspace.directory.parent.parent.rglob('*.jsonl'))


def test_diagnostic_companion_report_does_not_restore_redacted_model_free_text(tmp_path):
    marker='do_not_persist_87123'
    client,_=client_for([{'version':1,'action':'clarify','table':'facts','message':marker} for _ in range(13)])
    code,report,path=accept.run(client.config,client=client,directory=tmp_path/'run',diagnostics=True)
    assert code==1 and report['failed']>0
    assert marker not in path.read_text() and marker not in (path.parent/'rejected-plans.jsonl').read_text()
