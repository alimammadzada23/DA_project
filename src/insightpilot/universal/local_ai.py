"""Optional Ollama official REST adapter. Only literal loopback, no pull endpoint.

No SDK, model weights or daemon are installed or started by this module. The
HTTP transport is replaceable for tests; production always validates loopback.
"""
from dataclasses import dataclass
import json
import math
import re
import time
from urllib.parse import urlsplit

import httpx

from .analyst_plan import PLAN_SCHEMA, VALIDATOR, PlanError, decode_plan

DEFAULT_MODEL='qwen3.5:4b'
DEFAULT_ENDPOINT='http://127.0.0.1:11434'
MAX_RESPONSE=262_144


class LocalAIError(ValueError):
    def __init__(self,code,message,*,repair_feedback=None,response_received=False):
        self.code=code
        self.repair_feedback=repair_feedback
        self.response_received=response_received
        super().__init__(message)


@dataclass(frozen=True)
class LocalConfig:
    endpoint: str=DEFAULT_ENDPOINT
    model: str=DEFAULT_MODEL
    timeout_seconds: float=60
    context_tokens: int=8192
    output_tokens: int=1024

    def validated(self):
        endpoint=loopback_endpoint(self.endpoint)
        if not isinstance(self.model,str) or '://' in self.model or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,150}',self.model):
            raise LocalAIError('configuration','Choose an installed model name, not a URL or command.')
        if 'cloud' in self.model.casefold():
            raise LocalAIError('remote_model','Cloud models are unavailable in Local AI mode. Choose locally installed model weights.')
        if type(self.timeout_seconds) not in (int,float) or not math.isfinite(self.timeout_seconds) or not 5<=self.timeout_seconds<=120:
            raise LocalAIError('configuration','Set an inference timeout between 5 and 120 seconds.')
        if type(self.context_tokens) is not int or not 2048<=self.context_tokens<=8192 or type(self.output_tokens) is not int or not 256<=self.output_tokens<=1536:
            raise LocalAIError('configuration','Local inference context/output budgets exceed supported limits.')
        return LocalConfig(endpoint,self.model,self.timeout_seconds,self.context_tokens,self.output_tokens)


def loopback_endpoint(value):
    if not isinstance(value,str) or len(value)>200 or any(c.isspace() for c in value):raise LocalAIError('configuration','Use a loopback Ollama address without credentials or paths.')
    try:
        parsed=urlsplit(value);port=parsed.port or (443 if parsed.scheme=='https' else 80)
        if parsed.scheme not in ('http','https') or parsed.hostname not in ('127.0.0.1','localhost','::1') or parsed.username is not None or parsed.password is not None or parsed.path not in ('','/') or parsed.query or parsed.fragment or not 1<=port<=65535:
            raise ValueError
    except ValueError as exc:raise LocalAIError('configuration','Local AI accepts only http(s)://127.0.0.1, localhost or [::1] with a local port. Remote endpoints, paths, credentials and redirects are blocked.') from exc
    # Avoid DNS and proxy routing for localhost. This deliberately connects only
    # to the user's explicitly authorized literal loopback service.
    host='[::1]' if parsed.hostname=='::1' else '127.0.0.1'
    return f'{parsed.scheme}://{host}:{port}'


class OllamaClient:
    def __init__(self,config=LocalConfig(),*,transport=None,observer=None):
        self.config=config.validated();self.transport=transport;self.observer=observer

    def _request(self,method,path,payload=None,*,inference=False):
        timeout=httpx.Timeout(self.config.timeout_seconds if inference else 2,connect=2,write=5,pool=2)
        started=time.monotonic()
        try:
            with httpx.Client(timeout=timeout,follow_redirects=False,trust_env=False,transport=self.transport) as client:
                with client.stream(method,self.config.endpoint+path,json=payload) as response:
                    if 300<=response.status_code<400:raise LocalAIError('redirect','Ollama redirected the request. Local AI does not follow redirects.')
                    if response.status_code==404 and inference:raise LocalAIError('missing_model','The selected model is no longer installed. Refresh models or return to Standard analysis.')
                    if response.status_code>=400:raise LocalAIError('request_failed',f'The local Ollama request failed (HTTP {response.status_code}). Check the selected model and local server; Standard analysis remains available.')
                    chunks=[];size=0
                    for chunk in response.iter_bytes():
                        size+=len(chunk)
                        if size>MAX_RESPONSE:raise LocalAIError('invalid_output','The local server response exceeded its size limit. No analysis was executed.')
                        if time.monotonic()-started>self.config.timeout_seconds:raise LocalAIError('timeout','Local inference exceeded its time budget. Try a shorter question or a smaller context; Standard analysis remains available.')
                        chunks.append(chunk)
            value=json.loads(b''.join(chunks))
            if not isinstance(value,dict):raise ValueError
            return value
        except httpx.TimeoutException as exc:raise LocalAIError('timeout','Ollama timed out. The model may be loading or short of memory. No cloud fallback was used; retry explicitly or choose Standard analysis.') from exc
        except httpx.RequestError as exc:raise LocalAIError('unavailable','Ollama is not reachable. It may not be installed or running. Start the Ollama app on this computer, then refresh; Standard analysis works without it.') from exc
        except (ValueError,UnicodeError,RecursionError) as exc:
            if isinstance(exc,LocalAIError):raise
            raise LocalAIError('invalid_output','Ollama returned an invalid response. No analytical calculation was executed.') from exc

    def discover(self):
        response=self._request('GET','/api/tags');models=response.get('models')
        if not isinstance(models,list) or len(models)>200:raise LocalAIError('invalid_output','Ollama model discovery returned an invalid list.')
        local=[];excluded=0
        for item in models:
            if not isinstance(item,dict) or not isinstance(item.get('name'),str):raise LocalAIError('invalid_output','Ollama model metadata is invalid.')
            name=item['name']
            if item.get('remote_host') or item.get('remote_model') or 'cloud' in name.casefold():excluded+=1;continue
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,150}',name):continue
            local.append({'name':name,'disk_bytes':item.get('size'),'digest':str(item.get('digest',''))[:100]})
        return {'status':'available' if local else 'no_models','models':local,'excluded_remote_models':excluded,
            'message':'Ollama is reachable. Choose installed local weights.' if local else 'Ollama is running, but no eligible local model was found. Install the recommended model yourself or use Standard analysis.'}

    def inspect_model(self):
        models=self.discover()['models']
        if self.config.model not in {m['name'] for m in models}:raise LocalAIError('missing_model','The selected local model is not installed. Download it explicitly with Ollama, choose another installed model, or use Standard analysis.')
        metadata=self._request('POST','/api/show',{'model':self.config.model})
        if metadata.get('remote_host') or metadata.get('remote_model') or metadata.get('cloud'):
            raise LocalAIError('remote_model','The selected model routes to a remote service. Local AI mode requires locally installed weights.')
        capabilities=metadata.get('capabilities')
        if not isinstance(capabilities,list) or 'completion' not in capabilities:
            raise LocalAIError('incompatible','This model/server does not advertise text completion. Choose a compatible local model and a current Ollama version.')
        details=metadata.get('details',{})
        return {'name':self.config.model,'capabilities':capabilities,'family':details.get('family') if isinstance(details,dict) else None,
                'quantization':details.get('quantization_level') if isinstance(details,dict) else None,'status':'available'}

    def plan(self,messages):
        if not isinstance(messages,list) or len(messages)>3 or len(json.dumps(messages,ensure_ascii=False).encode())>28_000:
            raise LocalAIError('context_limit','The planning context is too large. Select fewer fields or ask a shorter question.')
        model=self.inspect_model();started=time.perf_counter()
        payload={'model':self.config.model,'messages':messages,'format':PLAN_SCHEMA,'stream':False,'keep_alive':'2m',
                 'truncate':False,'shift':False,
                 'options':{'temperature':0,'seed':42,'num_ctx':self.config.context_tokens,'num_predict':self.config.output_tokens}}
        if 'thinking' in model['capabilities']:payload['think']=False
        response=self._request('POST','/api/chat',payload,inference=True)
        if response.get('remote_host') or response.get('remote_model'):
            raise LocalAIError('remote_model','Ollama reported remote inference. The response was rejected; Local AI requires local weights.')
        if response.get('done') is not True:raise LocalAIError('invalid_output','The model response was incomplete. No analysis was executed.')
        if response.get('model')!=self.config.model:raise LocalAIError('invalid_output','Ollama returned a different model identifier; the response was rejected.')
        if response.get('done_reason')=='length':raise LocalAIError('invalid_output','The model reached its output limit before finishing a plan. Ask a simpler question.')
        message=response.get('message')
        if not isinstance(message,dict) or message.get('tool_calls') or not isinstance(message.get('content'),str):
            raise LocalAIError('invalid_output','The model returned tools or an invalid message; no analysis was executed.')
        if self.observer is not None:self.observer.capture_response(message['content'])
        try:plan=decode_plan(message['content'],envelope=True)
        except PlanError as exc:
            if self.observer is not None:self.observer.decision('rejected_schema',reason=exc.reason or 'invalid_schema')
            feedback=exc.repair_feedback or ('Missing required property: operation for action analyze. Return a complete plan with a supported operation; preserve the original question and context.' if exc.reason=='missing_operation' else None)
            raise LocalAIError('invalid_output',str(exc),repair_feedback=feedback,response_received=True) from exc
        stats={}
        for key in ('load_duration','total_duration','eval_duration','prompt_eval_count','eval_count'):
            v=response.get(key)
            if type(v) in (int,float) and math.isfinite(v) and v>=0:stats[key]=v
        return plan,{'backend':'Ollama local REST','model':self.config.model,'endpoint':self.config.endpoint,
                    'wire_schema_valid':VALIDATOR.is_valid(plan),
                    'model_response_received':True,
                    'planning_seconds':round(time.perf_counter()-started,4),'server_metrics':stats,'numerical_authority':'InsightPilot engine; model proposed intent only'}

    def interpret(self,messages):
        """Word an already computed result. Same loopback/size/model checks as planning; no tools, no plan."""
        from .analyst_interpret import INTERPRET_SCHEMA
        if not isinstance(messages,list) or len(messages)>2 or len(json.dumps(messages,ensure_ascii=False).encode())>20_000:
            raise LocalAIError('context_limit','The computed result is too large to interpret locally. The verified table is still shown.')
        model=self.inspect_model();started=time.perf_counter()
        capabilities=model.get('capabilities') if isinstance(model,dict) and isinstance(model.get('capabilities'),list) else []
        payload={'model':self.config.model,'messages':messages,'format':INTERPRET_SCHEMA,'stream':False,'keep_alive':'2m',
                 'truncate':False,'shift':False,
                 'options':{'temperature':0,'seed':42,'num_ctx':self.config.context_tokens,'num_predict':self.config.output_tokens}}
        if 'thinking' in capabilities:payload['think']=False
        response=self._request('POST','/api/chat',payload,inference=True)
        if response.get('remote_host') or response.get('remote_model'):
            raise LocalAIError('remote_model','Ollama reported remote inference. The interpretation was rejected.')
        if response.get('done') is not True or response.get('model')!=self.config.model:
            raise LocalAIError('invalid_output','The local interpretation was incomplete or came from another model; it was discarded.')
        if response.get('done_reason')=='length':raise LocalAIError('invalid_output','The model reached its output limit before finishing the interpretation.')
        message=response.get('message')
        if not isinstance(message,dict) or message.get('tool_calls') or not isinstance(message.get('content'),str):
            raise LocalAIError('invalid_output','The model returned tools or an invalid message; the interpretation was discarded.')
        try:value=json.loads(message['content'])
        except (ValueError,RecursionError) as exc:raise LocalAIError('invalid_output','The interpretation was not valid JSON; it was discarded.') from exc
        stats={k:v for k in ('load_duration','total_duration','eval_duration','prompt_eval_count','eval_count')
               if type(v:=response.get(k)) in (int,float) and math.isfinite(v) and v>=0}
        return value,{'model':self.config.model,'planning_seconds':round(time.perf_counter()-started,4),'server_metrics':stats}
