"""Bounded, per-browser analytical conversation. No automatic disk persistence."""
from copy import deepcopy
from dataclasses import asdict,dataclass,field,replace
import hashlib
import json
import time
import uuid

from .analyst_plan import PlanError,ClarificationRequired,validate_plan
from .analyst_execute import execute_plan
from .analyst_constraints import request_constraints
from .analyst_planner import preflight_question,planning_messages,standard_plan,validate_question_intent
from .local_ai import OllamaClient,LocalConfig,LocalAIError
from .metrics import load_metrics
from .relationships import load_relationships
from .report import replay


def _digest(value):return hashlib.sha256(json.dumps(value,default=str,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def context_signature(engine,metric):
    relationships=load_relationships(engine.workspace)
    related={}
    for name,rel in relationships.items():
        if rel['left']==engine.table:
            info=engine.workspace.info(rel['right']);related[name]={'relationship':rel,'active_related_version':info['current'],'source':info['source']['sha256']}
    return _digest({'workspace_identity':str(engine.workspace.directory),'table':engine.table,'version':engine.info['current'],
        'source':engine.info['source']['sha256'],'columns':engine.info['columns'],'roles':engine.info['roles'],
        'grain':engine.info['grain'],'semantics':engine.info.get('semantics',{}),'context':engine.context.dictionary(),
        'filters':[asdict(f) for f in engine.filters],'metric':asdict(metric),
        'saved_metric_definitions':[asdict(m) for m in load_metrics(engine.workspace,engine.table)],'relationships':related})


def assert_current(engine):
    current=engine.workspace.info(engine.table)
    for key in ('current','columns','roles','grain','semantics'):
        if current.get(key)!=engine.info.get(key):raise PlanError('The dataset version or reviewed definitions changed while planning. Refresh the context and ask again; no stale calculation was executed.')


@dataclass
class Conversation:
    signature: str=''
    entries: list=field(default_factory=list)
    last_plan: object=None
    last_response: object=None
    clarification: object=None
    pending: object=None
    last_mode: str=''
    context_reset: bool=False

    def bind(self,engine,metric,mode):
        signature=context_signature(engine,metric)
        changed=bool(self.signature) and (signature!=self.signature or mode!=self.last_mode)
        if changed:self.clear()
        self.signature=signature;self.last_mode=mode;self.context_reset=changed
        return changed

    def clear(self):
        self.entries=[];self.last_plan=None;self.last_response=None;self.clarification=None;self.pending=None

    def add(self,question,response,backend):
        entry={'id':uuid.uuid4().hex[:12],'question':question,'response':response,'backend':backend,'signature':self.signature}
        self.entries.append(entry);self.entries=self.entries[-6:]
        if response['status']=='ok':
            self.last_response=response;self.last_plan=validate_stored_plan(response['plan']);self.clarification=None
        elif response['status']=='clarify':self.clarification={'question':question,'request':response['answer']}
        return response


def validate_stored_plan(data):
    # Canonical intent only; it will still pass live semantic validation on reuse.
    from .analyst_plan import ValidatedPlan
    return ValidatedPlan(deepcopy(data))


def analyze_question(engine,profile,metric,question,conversation,*,mode='standard',config=None,client=None,field_scope=None):
    config=config or getattr(client,'config',None) or LocalConfig();mode_key=mode+(':'+_digest(asdict(config)) if mode=='local' else '')
    conversation.bind(engine,metric,mode_key)
    if conversation.entries and conversation.entries[-1]['question']==question and conversation.entries[-1]['response']['status']=='ok':
        response=deepcopy(conversation.entries[-1]['response']);response['cache_hit']=True
        return response
    started=time.perf_counter();constraints=None;backend={'backend':'Standard deterministic planner','model':None,'model_request_completed':False,'model_response_received':False,'model_attempts':0,'repair_attempted':False,'constraint_repairs':[]}
    try:
        assert_current(engine)
        observer=getattr(client,'observer',None)
        if observer is not None:observer.verify_engine(engine)
        guarded=preflight_question(engine,question)
        if guarded:raw=guarded
        elif mode=='local':
            constraints=request_constraints(engine,question,conversation.last_plan,conversation.last_response)
            effective=question
            if conversation.clarification:
                effective='Previous clarification: '+json.dumps(conversation.clarification,ensure_ascii=False)+'\nUser reply: '+question
                if len(effective)>2000:raise ClarificationRequired('Restate the complete question in fewer than 2,000 characters.')
            messages=planning_messages(engine,profile,metric,effective,conversation.last_plan,field_scope=field_scope,request_constraints=constraints.summary())
            planner=client or OllamaClient(config)
            backend.update(backend='Ollama local REST',model=config.model)
            current_messages=messages
            for attempt in range(2):
                backend.update(model_attempts=attempt+1,model_request_completed=False)
                if observer is not None:observer.begin_attempt(attempt+1)
                try:
                    raw,metadata=planner.plan(current_messages)
                    backend.update(metadata);backend['model_request_completed']=metadata.get('wire_schema_valid',True)
                    raw,repairs=constraints.repair(raw)
                    backend['constraint_repairs']=repairs
                    preliminary=validate_plan(engine,profile,raw,metric,conversation.last_plan)
                    constraints.validate(preliminary)
                    validate_question_intent(engine,question,preliminary)
                    if observer is not None:observer.decision('validated')
                    break
                except (LocalAIError,PlanError) as exc:
                    backend['model_response_received']=backend['model_response_received'] or getattr(exc,'response_received',False)
                    if observer is not None:observer.decision('rejected',reason=getattr(exc,'reason',None) or getattr(exc,'code','invalid_plan'))
                    feedback=getattr(exc,'repair_feedback',None)
                    if attempt or not feedback:
                        if attempt:raise LocalAIError(getattr(exc,'code','invalid_output'),'The local model could not produce a valid plan after one repair attempt. No calculation ran. '+str(exc)) from exc
                        raise
                    assert_current(engine)
                    if context_signature(engine,metric)!=conversation.signature:raise PlanError('The analytical context changed before repair. Ask again in the new context.')
                    remaining=config.timeout_seconds-(time.perf_counter()-started)
                    if remaining<5:raise LocalAIError('timeout','No planning time remains for a repair. Ask again explicitly; no calculation ran.') from exc
                    backend['repair_attempted']=True
                    if isinstance(planner,OllamaClient):planner=OllamaClient(replace(config,timeout_seconds=remaining),transport=planner.transport,observer=observer)
                    current_messages=messages+[{'role':'user','content':'Validation feedback: '+feedback}]
        elif mode=='standard':
            effective=question
            if conversation.clarification and len(question.split())<12:effective=conversation.clarification['question']+' '+question
            raw=standard_plan(engine,profile,metric,effective,conversation.last_plan)
        else:raise PlanError('Choose Standard analysis or Local AI explicitly; no automatic external provider routing is available.')
        validated=validate_plan(engine,profile,raw,metric,conversation.last_plan)
        if constraints is not None:constraints.validate(validated)
        validate_question_intent(engine,question,validated)
        assert_current(engine)
        if context_signature(engine,metric)!=conversation.signature:raise PlanError('The workspace context changed while the plan was being produced. Ask again in the new context.')
        cohort=()
        if constraints is not None and constraints.preserve and validated.data['action']=='analyze':
            cohort=constraints.cohort
        elif constraints is None and validated.inherited and conversation.last_response:
            old=conversation.last_response
            changed=any(validated.data.get(k)!=old['plan'].get(k) for k in ('measures','sort_by','descending','chart'))
            if validated.data.get('operation')=='aggregate' and validated.data.get('group_by')==old['plan'].get('group_by') and (changed or old.get('applied_cohort')):
                cohort=old.get('cohort',())
        response=execute_plan(engine,profile,validated,metric,cohort=cohort)
        response.update(backend=backend,cache_hit=False,elapsed_seconds=round(time.perf_counter()-started,4),context_signature=conversation.signature,applied_cohort=list(cohort))
        if response['status']=='approval_required':
            conversation.pending={'question':question,'plan':validated.dictionary(),'backend':backend,'signature':conversation.signature,'cohort':list(cohort)}
            return response
        conversation.pending=None
    except ClarificationRequired as exc:response={'status':'clarify','answer':str(exc),'evidence':[],'figures':[],'definitions':[],'backend':backend}
    except (PlanError,LocalAIError) as exc:response={'status':'error','answer':str(exc),'error_code':getattr(exc,'code','invalid_plan'),'evidence':[],'figures':[],'definitions':[],'backend':backend}
    except ValueError as exc:response={'status':'error','answer':'The calculation could not finish: '+str(exc)[:400],
        'error_code':'execution_failed','evidence':[],'figures':[],'definitions':[],'backend':backend}
    return conversation.add(question,response,backend)


def approve_pending(engine,profile,metric,conversation):
    pending=conversation.pending
    if not pending or pending['signature']!=context_signature(engine,metric):raise PlanError('The pending definition belongs to an earlier context. Ask again; no approval was applied.')
    assert_current(engine)
    validated=validate_plan(engine,profile,pending['plan'],metric)
    response=execute_plan(engine,profile,validated,metric,approved=True,cohort=pending['cohort'])
    response.update(backend=pending['backend'],context_signature=conversation.signature,approved_definition=True,applied_cohort=pending['cohort'])
    conversation.pending=None
    return conversation.add(pending['question'],response,pending['backend'])


def save_answer(workspace,name,response,*,question=None):
    if response.get('status')!='ok':raise PlanError('Only executed, validated results can be saved.')
    payload={'analysis':response['engine_manifest'],'analysis_plan':response['plan'],'evidence':response['primary'],
        'evidence_bundle':response['evidence'][1:],'applied_cohort':response.get('applied_cohort',[]),'approved_definition':bool(response.get('approved_definition')),
        'backend':response.get('backend',{}),'question_text_included':question is not None}
    if response['figures']:payload['figure']=response['figures'][0];payload['figures']=response['figures']
    if question is not None:payload['question']=question[:2000]
    workspace.save_object('block',name,payload)


def replay_answer(workspace,payload):
    from .analyst_execute import profile_for_engine
    engine,metric=replay(workspace,payload['analysis'])
    profile=profile_for_engine(engine)
    validated=validate_plan(engine,profile,payload['analysis_plan'],metric,legacy=True)
    response=execute_plan(engine,profile,validated,metric,approved=bool(payload.get('approved_definition')),cohort=payload.get('applied_cohort',[]))
    response['backend']={'backend':'Replayed saved calculation; no model invoked','model':None,'model_request_completed':False}
    return response
