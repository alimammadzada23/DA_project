"""Untrusted intent data -> a finite, schema- and semantics-checked analysis plan.

There is deliberately no SQL, Python, URL, file path or approval field in the
model contract. Approval and conversation cohorts are application-owned state.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
import json
import math

from jsonschema import Draft202012Validator

from .metrics import Metric, NUMERIC_ROLES, load_metrics
from .sql_lab import SQLLab
from .analyst_contract import MAX_GROUPS, OPERATIONS, defaults, legacy_saved_plan, operation_variants

SCALAR={'type':['string','number','boolean','null'],'maxLength':200}
def closed_object(properties):
    return {'type':'object','additionalProperties':False,'required':list(properties),'properties':properties}

_COLUMN={'type':'string','minLength':1,'maxLength':256}
CONDITION={'oneOf':[
    {'type':'object','additionalProperties':False,'required':['column','operator'],
     'properties':{'column':_COLUMN,'operator':{'enum':['is_missing','not_missing']},'value':{'type':'null'}}},
    closed_object({'column':_COLUMN,'operator':{'enum':['eq','ne']},'value':SCALAR}),
    closed_object({'column':_COLUMN,'operator':{'enum':['gt','gte','lt','lte']},'value':{'type':'number'}}),
    closed_object({'column':_COLUMN,'operator':{'enum':['from','through']},'value':{'type':'string','pattern':r'^\d{4}-\d{2}-\d{2}$'}})]}

DENOMINATOR={'oneOf':[
    closed_object({'kind':{'const':'selected'}}),
    closed_object({'kind':{'const':'nonmissing'},'column':{'type':'string','minLength':1,'maxLength':256}}),
    closed_object({'kind':{'const':'condition'},'condition':CONDITION})]}
MEASURE_ARGUMENTS={
    **{a:{} for a in ('records','share')},
    **{a:{'column':{'type':'string','minLength':1,'maxLength':256}} for a in ('count','distinct','sum','avg','median','min','max')},
    'quantile':{'column':{'type':'string','minLength':1,'maxLength':256},'quantile':{'type':'number','minimum':0,'maximum':1}},
    'saved':{'metric':{'type':'string','minLength':1,'maxLength':100}},
    'rate':{'numerator':CONDITION,'denominator':DENOMINATOR}}
MEASURE_SCHEMAS={a:closed_object({'aggregation':{'const':a},**arguments}) for a,arguments in MEASURE_ARGUMENTS.items()}
MEASURE={'oneOf':list(MEASURE_SCHEMAS.values())}
MEASURE_VALIDATORS={a:Draft202012Validator(schema) for a,schema in MEASURE_SCHEMAS.items()}
ANALYSIS_PROPERTIES={
        'version':{'const':1},'action':{'enum':['analyze','clarify','refuse']},'table':{'type':'string','maxLength':63},
        'operation':{'enum':list(OPERATIONS)},
        'group_by':{'type':'array','items':{'type':'string','maxLength':256},'maxItems':2,'uniqueItems':True},
        'measures':{'type':'array','items':MEASURE,'maxItems':4},
        'filters':{'type':'array','items':CONDITION,'maxItems':8},
        'fields':{'type':'array','items':{'type':'string','maxLength':256},'maxItems':40,'uniqueItems':True},
        'sort_by':{'enum':['records','group','m0','m1','m2','m3','period']},'descending':{'type':'boolean'},
        'limit':{'type':['integer','null'],'minimum':1,'maximum':max(p.limit_bound for p in OPERATIONS.values())},'bins':{'type':'integer','minimum':5,'maximum':80},
        'date_basis':{'type':['string','null'],'maxLength':256},'grain':{'enum':['day','week','month','quarter','year']},
        'periods':{'type':'array','items':{'type':'string','format':'date'},'minItems':4,'maxItems':4},
        'relationship':{'type':['string','null'],'maxLength':200},'inherit':{'type':'boolean'},
        'chart':{'enum':['auto','table','bar','histogram','line','scatter','box','pie']}}
# Typed admission is separate from operation validation after intent repair.
ANALYSIS_SCHEMA={'type':'object','additionalProperties':False,
    'required':['version','action','table','operation'],
    'properties':ANALYSIS_PROPERTIES|{'action':{'const':'analyze'}}}
ACTION_SCHEMAS=[{'type':'object','additionalProperties':False,'required':['version','action','table','message'],
       'properties':{k:v for k,v in ANALYSIS_PROPERTIES.items() if k in ('version','table')}|
       {'action':{'const':action},'message':{'type':'string','minLength':1,'maxLength':400}}}
      for action in ('clarify','refuse')]
_VARIANTS,_DEFINITIONS=operation_variants(ANALYSIS_PROPERTIES)
PLAN_SCHEMA={'$schema':'https://json-schema.org/draft/2020-12/schema','$defs':_DEFINITIONS,'oneOf':[*_VARIANTS,*ACTION_SCHEMAS]}
ENVELOPE_SCHEMA={'oneOf':[ANALYSIS_SCHEMA,*ACTION_SCHEMAS]}
VALIDATOR=Draft202012Validator(PLAN_SCHEMA)
ENVELOPE_VALIDATOR=Draft202012Validator(ENVELOPE_SCHEMA)
INCOMPLETE_VALIDATOR=Draft202012Validator(ANALYSIS_SCHEMA|{'required':['version','action','table']})


class PlanError(ValueError):
    """Invalid or unauthorized model data; never an executable program."""
    code='invalid_plan'

    def __init__(self,message,*,reason=None,repair_feedback=None):
        super().__init__(message);self.reason=reason;self.repair_feedback=repair_feedback


class ClarificationRequired(ValueError):
    code='clarification'


@dataclass(frozen=True)
class ValidatedPlan:
    data: dict
    approval_required: bool=False
    inherited: bool=False

    def dictionary(self): return deepcopy(self.data)


def finite_json(value):
    if isinstance(value,float) and not math.isfinite(value): raise PlanError('Plan numbers must be finite.')
    if isinstance(value,dict):
        for v in value.values():finite_json(v)
    elif isinstance(value,list):
        for v in value:finite_json(v)


def decode_plan(raw,*,envelope=False):
    if not isinstance(raw,str) or len(raw.encode('utf-8'))>16_384:
        raise PlanError('The model plan exceeds its size limit.')
    def unique(pairs):
        d={}
        for k,v in pairs:
            if k in d:raise PlanError('Duplicate JSON plan keys are not allowed.')
            d[k]=v
        return d
    try:value=json.loads(raw,object_pairs_hook=unique)
    except (json.JSONDecodeError,RecursionError) as exc:raise PlanError('The model did not return a valid JSON plan. No calculation was run.') from exc
    validate_structure(value,envelope=envelope)
    return value


def validate_structure(value,*,envelope=False):
    try:
        if len(json.dumps(value,ensure_ascii=False).encode('utf-8'))>16_384:raise PlanError('Plan exceeds its size limit.')
        finite_json(value)
        error=next((ENVELOPE_VALIDATOR if envelope else VALIDATOR).iter_errors(value),None)
    except (TypeError,RecursionError) as exc:raise PlanError('The plan contains invalid data types or excessive nesting.') from exc
    if error:
        if isinstance(value,dict) and value.get('action')=='analyze' and isinstance(value.get('measures'),list):
            for measure in value['measures']:
                if not isinstance(measure,dict):continue
                aggregation=measure.get('aggregation')
                if isinstance(aggregation,str) and aggregation in MEASURE_SCHEMAS and not MEASURE_VALIDATORS[aggregation].is_valid(measure):
                    keys=', '.join(MEASURE_SCHEMAS[aggregation]['required'])
                    raise PlanError('Invalid arguments for '+aggregation+'; required and permitted keys: '+keys+'. No calculation ran.',reason='measure_arguments')
        if isinstance(value,dict) and value.get('action')=='analyze' and 'operation' not in value and INCOMPLETE_VALIDATOR.is_valid(value):
            raise PlanError('An analysis plan must include a supported operation. No calculation was run.',reason='missing_operation')
        if envelope and isinstance(value,dict) and value.get('action')=='analyze':
            missing={'version','table','operation'}-set(value)
            probe={'version':1,'table':'','operation':'aggregate'}|value
            if missing and ENVELOPE_VALIDATOR.is_valid(probe):
                raise PlanError('Missing required plan metadata: '+', '.join(sorted(missing))+'. No calculation ran.',reason='missing_plan_metadata',repair_feedback='Return all required root metadata: version 1, the exact active table from context, action, and a supported operation. Preserve the original request; do not change dataset or invent fields.')
        if not envelope and ENVELOPE_VALIDATOR.is_valid(value) and value.get('action')=='analyze':
            op=value['operation'];policy=OPERATIONS[op]
            feedback=f'Operation {op}: group_by needs {policy.groups[0]}–{policy.groups[1]} fields; fields needs {policy.fields[0]}–{policy.fields[1]}; measures needs {policy.measures[0]}–{policy.measures[1]}. Allowed charts: '+', '.join(policy.charts)+'. Allowed sort_by: '+', '.join(policy.sorts)+'. Null limits select the documented operation policy. Preserve the user request; clarify if these constraints cannot express it.'
            raise PlanError('The plan has incompatible arguments for '+op+'. No calculation ran.',reason='operation_arguments',repair_feedback=feedback)
        location='.'.join(map(str,error.absolute_path)) or 'plan'
        raise PlanError('Invalid structured plan at '+location+'. Unknown keys, types or operations are not accepted.')


def reviewed_numeric(engine,profile,column):
    if column not in engine.info['columns']:raise PlanError('The plan references an unavailable field.')
    item=next((c for c in profile['columns'] if c['column']==column),{})
    if not engine.info.get('semantics',{}).get('reviewed') or engine.info['roles'].get(column) not in NUMERIC_ROLES:
        raise ClarificationRequired(f'Review {column!r} as a numeric measure and confirm the row grain in Quality & Preparation before calculating it.')
    if not item.get('numeric_values'):raise ClarificationRequired(f'{column!r} has no finite numeric values. Review its type conversion first.')


def check_condition(engine,profile,condition):
    name=condition['column'];op=condition['operator'];value=condition.get('value')
    if name not in engine.info['columns']:raise PlanError('A filter references an unavailable field.')
    if op in ('is_missing','not_missing'):
        if 'value' in condition and value is not None:raise PlanError('Missing-value filters do not accept a value.')
    elif op in ('from','through'):
        if name!=engine.context.date_basis:raise ClarificationRequired('Date filters must use the shared, reviewed date basis. Select it in the sidebar first.')
        try:date.fromisoformat(value)
        except (TypeError,ValueError) as exc:raise PlanError('Date filters require valid ISO calendar dates.') from exc
    elif op in ('gt','gte','lt','lte') or type(value) in (int,float):
        if type(value) not in (int,float) or not math.isfinite(value):raise PlanError('Numeric comparisons require a finite number.')
        reviewed_numeric(engine,profile,name)
    elif op in ('eq','ne'):
        if 'value' not in condition:raise PlanError('An equality filter needs an explicit value, including NULL when intended.')
        if type(value) not in (str,bool,type(None)):raise PlanError('Use an exact category value.')


def safe_model_message(value):
    # Model clarification is displayed as untrusted plain text, never a finding.
    import re
    if re.search(r'password|api.?key|secret|token|https?://|install|execute|shell',value,re.I):
        return 'Specify the intended field, metric, grouping or eligible denominator. Do not provide credentials.'
    return value or 'Specify the intended field, metric, grouping or eligible denominator.'


def validate_plan(engine,profile,value,selected_metric,previous=None,*,legacy=False):
    validate_structure(value,envelope=True);data=deepcopy(value)
    if data['table']!=engine.table:raise PlanError('The plan cannot change the active table or workspace.')
    if data['action']!='analyze':
        if set(data)-{'version','action','table','message'}:raise PlanError('Clarification/refusal plans cannot contain executable analysis arguments.')
        data['message']=safe_model_message(data.get('message',''))
        return ValidatedPlan(data)
    inherited=bool(data.get('inherit'))
    if inherited:
        if previous is None:raise ClarificationRequired('There is no current validated analysis to follow up. Ask a complete question first.')
        old=previous.data if isinstance(previous,ValidatedPlan) else previous
        for key in ('operation','group_by','measures','fields','sort_by','descending','limit','date_basis','grain','relationship','periods','chart'):
            if key not in data and key in old:data[key]=deepcopy(old[key])
        combined=deepcopy(old.get('filters',[]))
        for f in data.get('filters',[]):
            if f not in combined:combined.append(f)
        if len(combined)>8:raise PlanError('Too many question-specific filters. Start a new conversation.')
        data['filters']=combined
    if 'operation' not in data:raise PlanError('An analytical operation is required.')
    if legacy:data=legacy_saved_plan(data,engine.context.time_grain)
    validate_structure(data)
    defaults(data,engine.context.time_grain)
    validate_structure(data)
    lab=SQLLab(engine);allowed=set(engine.info['columns'])
    if data['relationship']:
        rel=lab.relationships.get(data['relationship'])
        if not rel:raise PlanError('Choose a current validated relationship with a unique dimension key; unapproved joins are blocked.')
        related={rel['right']+'.'+c for c in engine.workspace.columns(rel['right'])}
        if related & set(engine.info['columns']):raise PlanError('Related-field labels collide with fact fields. Rename those fields before joining.')
        allowed|=related
    if set(data['group_by'])-allowed or set(data['fields'])-set(engine.info['columns']):raise PlanError('The plan references an unavailable grouping or field.')
    for condition in data['filters']:check_condition(engine,profile,condition)
    op=data['operation'];groups=data['group_by'];fields=data['fields'];measures=data['measures']
    if groups and op not in ('aggregate','group_distribution','compare_periods'):
        raise ClarificationRequired('This operation cannot preserve the requested grouping. Choose grouped aggregates or group distributions; time trends currently use one series at a time.')
    if fields and op in ('aggregate','trend','compare_periods'):raise PlanError('Specify aggregate inputs in measures; an extra field list would be ambiguous.')
    if 'periods' in data and op!='compare_periods':raise PlanError('Period windows are only valid for period comparisons.')
    if data['relationship'] and op!='aggregate':raise PlanError('Related dimensions are available for grouped aggregate analysis only.')
    if op in ('aggregate','trend','compare_periods') and not measures:
        raise PlanError('An aggregate or time plan must name its measures explicitly; no different calculation was substituted.')
    approval=False
    saved={m.name:m for m in load_metrics(engine.workspace,engine.table)}|{selected_metric.name:selected_metric,'Records':Metric('Records')}
    for m in measures:
        agg=m['aggregation']
        # The producer schema and this defensive check use the same contract.
        if set(m)!=set(MEASURE_SCHEMAS[agg]['required']):raise PlanError('Measure arguments do not match its aggregation.')
        if agg=='saved':
            if m.get('metric') not in saved:raise PlanError('The plan selected an unknown metric definition.')
            definition=saved[m['metric']];definition.compile(engine.info['columns'],engine.info['roles'])
            if definition.formula!='count()' and not engine.info.get('semantics',{}).get('reviewed'):
                raise ClarificationRequired('Review the metric definitions and row grain before using this business measure.')
        elif agg in ('sum','avg','median','min','max','quantile'):
            if 'column' not in m:raise PlanError('A numeric measure requires a field.')
            reviewed_numeric(engine,profile,m['column'])
            if agg=='quantile' and 'quantile' not in m:raise PlanError('Specify a quantile probability between zero and one.')
        elif agg in ('count','distinct'):
            if m.get('column') not in engine.info['columns']:raise PlanError('A count measure requires an available field.')
        elif agg=='share':
            if op!='aggregate' or not groups:raise ClarificationRequired('Choose a grouping for shares of selected records. These shares include missing categories and are not business conversion rates.')
        elif agg=='rate':
            if 'numerator' not in m or 'denominator' not in m:raise ClarificationRequired('Specify the numerator condition and eligible denominator; a count is not a rate.')
            check_condition(engine,profile,m['numerator']);den=m['denominator']
            if not engine.info.get('semantics',{}).get('reviewed') or engine.info['roles'].get(m['numerator']['column'],'unassigned')=='unassigned':
                raise ClarificationRequired('Review the outcome field and row grain before defining a proportion.')
            if den['kind']=='nonmissing' and den.get('column') not in engine.info['columns']:raise PlanError('The eligible denominator requires an available field.')
            if den['kind']=='condition':
                if 'condition' not in den:raise PlanError('Specify the denominator eligibility condition.')
                check_condition(engine,profile,den['condition'])
            if set(den)-({'kind'} if den['kind']=='selected' else {'kind','column'} if den['kind']=='nonmissing' else {'kind','condition'}):raise PlanError('Denominator arguments are incompatible with its kind.')
            approval=True
    if op in ('summary','distribution','outliers','group_distribution'):
        if len(fields)!=1:raise ClarificationRequired('Choose one numeric field for this analysis.')
        reviewed_numeric(engine,profile,fields[0])
        if op=='group_distribution' and len(groups)!=1:raise ClarificationRequired('Choose one grouping dimension for the distribution comparison.')
    if op=='scatter':
        if len(fields)!=2:raise ClarificationRequired('Choose two numeric fields for the relationship view.')
        for field in fields:reviewed_numeric(engine,profile,field)
    if op in ('trend','compare_periods'):
        if not engine.context.date_basis:raise ClarificationRequired('Select a reviewed date basis in the sidebar. Month names or numbers alone do not establish a calendar timeline.')
        if data['date_basis'] not in (None,engine.context.date_basis):raise ClarificationRequired('The proposed time field differs from the shared date basis. Change the shared date basis explicitly before asking this question.')
        if data['grain']!=engine.context.time_grain:raise ClarificationRequired('The requested time grain differs from the shared calendar grain. Change it in the sidebar to keep pages consistent.')
        if not next((c.get('recognized_dates') for c in profile['columns'] if c['column']==engine.context.date_basis),0):raise ClarificationRequired('The date basis has no usable ISO dates; review date conversion first.')
        data['date_basis']=engine.context.date_basis
        if len(measures)!=1:raise PlanError('Time analyses currently use one explicit metric at a time.')
        if op=='compare_periods' and (len(groups)!=1 or len(data.get('periods',[]))!=4):raise ClarificationRequired('Specify a grouping and two equally long, nonoverlapping complete periods.')
        if op=='compare_periods':
            try:a,b,c,d=map(date.fromisoformat,data['periods'])
            except ValueError as exc:raise ClarificationRequired('Specify valid ISO calendar dates for both periods.') from exc
            if not a<=b<c<=d or (b-a)!=(d-c):raise ClarificationRequired('Use nonoverlapping, equally long inclusive periods, earlier first.')
    if op not in ('aggregate','trend','compare_periods') and measures:raise PlanError('This operation uses explicit fields, not aggregate measure definitions.')
    if data['sort_by'].startswith('m') and int(data['sort_by'][1:])>=len(measures):
        raise PlanError('The ordering references an unavailable measure.',reason='operation_arguments',repair_feedback='Use an existing measure alias from the proposed measures, or a supported record/group ordering, preserving the question. Do not add a measure merely to satisfy an invalid sort index.')
    if op=='aggregate' and not groups and (data['sort_by']=='group' or data['chart'] in ('bar','pie')):
        raise PlanError('A scalar aggregate uses a table and has no grouping to rank.',reason='operation_arguments',repair_feedback='The request is an ungrouped aggregate: use chart auto/table and sort_by records or an existing measure. Do not invent a grouping.')
    if data['chart']=='line' and op!='trend':raise PlanError('A line chart requires a validated calendar trend.')
    if data['chart']=='histogram' and op!='distribution':raise PlanError('A histogram requires calculated numeric bins.')
    if data['chart']=='scatter' and op!='scatter':raise PlanError('A scatter plot requires a paired numeric calculation.')
    if data['chart']=='box' and op!='group_distribution':raise PlanError('A box plot requires calculated group quantiles.')
    if op=='records' and data['chart'] not in ('auto','table'):raise PlanError('Source records use a table; request a separate aggregate for a chart.')
    if data['chart']=='pie' and (op!='aggregate' or len(groups)!=1 or len(measures)!=1 or measures[0]['aggregation'] not in ('records','share') or data['limit'] is not None and data['limit']>6):
        raise PlanError('Composition charts need one count/share, one group and at most six categories.')
    data['inherit']=False  # Canonical plans can be independently revalidated/replayed.
    return ValidatedPlan(data,approval,inherited)
