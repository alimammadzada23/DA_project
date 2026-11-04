"""Ollama protocol verification with explicit HTTP test doubles, no real model."""
import json
import pytest
import httpx

from insightpilot.universal.local_ai import LocalConfig,OllamaClient,LocalAIError,loopback_endpoint
from insightpilot.universal.analyst_plan import decode_plan,PlanError

PLAN={'version':1,'action':'analyze','table':'facts','operation':'aggregate','measures':[{'aggregation':'records'}]}
MODEL='test-double:latest'


def transport_for(response=None,*,tags=None,show=None,fail=None,calls=None):
    def handler(request):
        if calls is not None:calls.append(request)
        assert request.url.host in ('127.0.0.1','::1')
        assert request.headers.get('Authorization') is None
        if fail:raise fail
        if request.url.path=='/api/tags':return httpx.Response(200,json=tags if tags is not None else {'models':[{'name':MODEL,'size':123,'digest':'test-digest'}]})
        if request.url.path=='/api/show':return httpx.Response(200,json=show if show is not None else {'capabilities':['completion','thinking'],'details':{'family':'test-double','quantization_level':'TEST'}})
        assert request.url.path=='/api/chat'
        payload=json.loads(request.content)
        assert payload['stream'] is False and all(v['additionalProperties'] is False for v in payload['format']['oneOf'])
        assert payload['options']['num_ctx']<=8192 and payload['options']['num_predict']<=1536
        return response(request) if callable(response) else httpx.Response(200,json=response if response is not None else {'model':MODEL,'message':{'role':'assistant','content':json.dumps(PLAN)},'done':True,'done_reason':'stop','load_duration':100,'eval_count':20})
    return httpx.MockTransport(handler)


@pytest.mark.parametrize('endpoint',['https://example.test','http://127.0.0.1.example.test:11434','http://user:pass@127.0.0.1:11434','http://127.0.0.1:11434/api','http://127.0.0.1:11434?x=1','http://127.0.0.1:11434#x','file:///tmp/model','http://169.254.169.254','http://2130706433:11434','http://127.1:11434','http://localhost.:11434','http://0.0.0.0:11434','http://127.0.0.1:99999','http://127.0.0.1\n:11434'])
def test_remote_ambiguous_and_credential_endpoints_are_rejected(endpoint):
    with pytest.raises(LocalAIError):OllamaClient(LocalConfig(endpoint=endpoint))


@pytest.mark.parametrize('endpoint,expected',[('http://localhost:11434','http://127.0.0.1:11434'),('http://127.0.0.1:11434/','http://127.0.0.1:11434'),('http://[::1]:11434','http://[::1]:11434')])
def test_only_canonical_literal_loopback_is_used(endpoint,expected):assert loopback_endpoint(endpoint)==expected


def test_success_uses_official_structured_rest_and_does_not_download():
    calls=[];client=OllamaClient(LocalConfig(model=MODEL),transport=transport_for(calls=calls))
    result,metadata=client.plan([{'role':'user','content':'Count selected rows'}])
    assert result==PLAN and metadata['model']==MODEL and metadata['server_metrics']['eval_count']==20
    assert [r.url.path for r in calls]==['/api/tags','/api/show','/api/chat']
    payload=json.loads(calls[-1].content)
    assert payload['think'] is False and payload['keep_alive']=='2m'
    assert metadata['numerical_authority'].startswith('InsightPilot engine')


def test_running_empty_server_is_distinguished_from_missing_selected_model():
    client=OllamaClient(LocalConfig(model=MODEL),transport=transport_for(tags={'models':[]}))
    assert client.discover()['status']=='no_models'
    with pytest.raises(LocalAIError) as exc:client.plan([{'role':'user','content':'Count'}])
    assert exc.value.code=='missing_model'


@pytest.mark.parametrize('model',['gpt-cloud:latest','qwen:4b-cloud','https://example.test/model','model;install','x\ny'])
def test_remote_model_references_and_commands_are_rejected(model):
    with pytest.raises(LocalAIError):OllamaClient(LocalConfig(model=model))


def test_cloud_alias_metadata_cannot_route_a_local_request_remotely():
    calls=[];client=OllamaClient(LocalConfig(model=MODEL),transport=transport_for(show={'remote_host':'https://example.test','remote_model':'secret','capabilities':['completion']},calls=calls))
    with pytest.raises(LocalAIError) as exc:client.plan([{'role':'user','content':'Count'}])
    assert exc.value.code=='remote_model' and all(r.url.path!='/api/chat' for r in calls)


@pytest.mark.parametrize('capabilities',[[],['embedding'],None,'completion'])
def test_incompatible_models_do_not_generate_queries(capabilities):
    with pytest.raises(LocalAIError) as exc:OllamaClient(LocalConfig(model=MODEL),transport=transport_for(show={'capabilities':capabilities})).inspect_model()
    assert exc.value.code=='incompatible'


@pytest.mark.parametrize('failure,code',[(httpx.ConnectError('closed'),'unavailable'),(httpx.ReadTimeout('slow'),'timeout')])
def test_connection_failures_are_actionable_without_fallback(failure,code):
    with pytest.raises(LocalAIError) as exc:OllamaClient(LocalConfig(model=MODEL),transport=transport_for(fail=failure)).discover()
    assert exc.value.code==code


def test_redirects_are_never_followed():
    calls=[]
    def redirect(request):calls.append(request.url);return httpx.Response(302,headers={'Location':'https://example.test'})
    with pytest.raises(LocalAIError) as exc:OllamaClient(transport=httpx.MockTransport(redirect)).discover()
    assert exc.value.code=='redirect' and len(calls)==1


@pytest.mark.parametrize('response',[
    {'model':MODEL,'done':False,'message':{'content':'{}'}},
    {'model':'different-model','done':True,'message':{'content':json.dumps(PLAN)}},
    {'model':MODEL,'done':True,'done_reason':'length','message':{'content':json.dumps(PLAN)}},
    {'model':MODEL,'done':True,'message':{'tool_calls':[{'name':'run_sql'}],'content':json.dumps(PLAN)}},
    {'model':MODEL,'done':True,'message':{'content':'```json\n{}\n```'}},
    {'model':MODEL,'done':True,'message':{'content':'{"version":1,"action":"analyze","table":"facts","sql":"DROP TABLE facts"}'}},
    {'model':MODEL,'done':True,'message':{'content':'{"version":1,"version":2,"action":"refuse","table":"facts"}'}},
])
def test_malformed_incomplete_or_unsafe_outputs_never_become_plans(response):
    with pytest.raises(LocalAIError) as exc:OllamaClient(LocalConfig(model=MODEL),transport=transport_for(response)).plan([{'role':'user','content':'Count'}])
    assert exc.value.code=='invalid_output'


def test_response_and_prompt_size_budgets():
    with pytest.raises(LocalAIError) as exc:OllamaClient(LocalConfig(model=MODEL),transport=transport_for(lambda r:httpx.Response(200,content=b' '*300_000))).plan([{'role':'user','content':'Count'}])
    assert exc.value.code=='invalid_output'
    with pytest.raises(LocalAIError) as exc:OllamaClient().plan([{'role':'user','content':'x'*30_000}])
    assert exc.value.code=='context_limit'


@pytest.mark.parametrize('text',['not json','[]','{"version":1,"action":"analyze","table":"facts","limit":NaN}','{"version":1,"action":"analyze","table":"facts","limit":true}'])
def test_json_decoding_fails_closed(text):
    with pytest.raises(PlanError):decode_plan(text)


def test_remote_inference_reported_only_in_chat_response_is_rejected():
    response={'model':MODEL,'remote_model':'upstream','remote_host':'https://example.test','done':True,'message':{'content':json.dumps(PLAN)}}
    with pytest.raises(LocalAIError) as exc:OllamaClient(LocalConfig(model=MODEL),transport=transport_for(response)).plan([{'role':'user','content':'Count'}])
    assert exc.value.code=='remote_model'
