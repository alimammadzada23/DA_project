"""Grounded interpretation of an already executed result by the local model.

The model never calculates. It reads a bounded table of values InsightPilot
already computed (aggregates only; row-level previews are never sent) and
proposes wording. Every number it writes must be reproducible from that table
- a cell, a column total or mean, a share, a difference, a ratio or a percent
change - otherwise the sentence is removed. Causal wording is removed because
observed results do not establish causes. Failures fall back to the
deterministic answer; nothing here can execute SQL, code or a new analysis.
"""
from itertools import permutations
import json
import math
import re

from jsonschema import Draft202012Validator

# Row-level previews (records, scatter points, outlier rows) are not sent.
INTERPRETABLE=('aggregate','summary','distribution','group_distribution','trend','compare_periods','missing','column_maxima')
MAX_ROWS=40
INTERPRET_SCHEMA={'type':'object','additionalProperties':False,'required':['headline','findings','caveats','follow_ups'],
    'properties':{
        'headline':{'type':'string','minLength':1,'maxLength':220},
        'findings':{'type':'array','maxItems':4,'items':{'type':'string','minLength':1,'maxLength':320}},
        'caveats':{'type':'array','maxItems':3,'items':{'type':'string','minLength':1,'maxLength':260}},
        'follow_ups':{'type':'array','maxItems':3,'items':{'type':'string','minLength':4,'maxLength':170}}}}
VALIDATOR=Draft202012Validator(INTERPRET_SCHEMA)

SYSTEM=('You are a careful business data analyst writing for a decision maker. You receive a question and a table of values '
    'that an analytics engine has ALREADY computed exactly. Write a short interpretation as JSON matching the schema.\n'
    'Rules:\n'
    '- Use only numbers that appear in the table, or that follow directly from it (a column total or average, a share of a column '
    'total, a difference, a ratio, or a percent change between two values). Never estimate, extrapolate or invent figures.\n'
    '- Copy numbers as written or rounded sensibly (e.g. 2,148 or 2.1k or 18.4%).\n'
    '- Describe what the data shows: largest/smallest, concentration, spread, gaps, direction of change. Name groups exactly as in the table.\n'
    '- Never claim causes or reasons. Do not use words like because, due to, caused, led to, drove, driven by, resulted in.\n'
    '- headline: one sentence with the single most decision-relevant observation.\n'
    '- findings: 2-4 distinct, specific observations. caveats: limits stated in the context (scope, missing values, incomplete periods, top-N only).\n'
    '- follow_ups: up to 3 short questions the user could ask next about THIS dataset using its field names (comparison, trend, distribution, breakdown).\n'
    'Return JSON only.')

CAUSAL=re.compile(r'\b(caus(?:e|ed|es|ing)|because|due to|owing to|led to|leads? to|lead to|drives?|drove|driven|driving|'
                  r'result(?:ed|s|ing)? in|as a result|impact(?:ed|s)? by|thanks to|explains? why|the reason)\b',re.I)
NUMBER=re.compile(r'(?<![\w.])([-+−]?)(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*(%|percent|pp|[kKMB]\b|bn\b|million\b|billion\b|thousand\b)?')
MULTIPLIER={'k':1e3,'K':1e3,'thousand':1e3,'M':1e6,'million':1e6,'B':1e9,'bn':1e9,'billion':1e9}


class InterpretationError(ValueError):
    pass


def _finite(value):
    if isinstance(value,bool) or value is None:return None
    try:number=float(value)
    except (TypeError,ValueError):return None
    return number if math.isfinite(number) else None


def _round(value):
    number=_finite(value)
    if number is None:return value if isinstance(value,(str,int)) or value is None else str(value)
    return float(f'{number:.6g}')


def eligible(response):
    plan=response.get('plan') or {}
    return response.get('status')=='ok' and plan.get('operation') in INTERPRETABLE and bool((response.get('primary') or {}).get('rows'))


def display_columns(response):
    """Readable column names matching the analyst table, so the model can name measures precisely."""
    primary=response['primary'];plan=response.get('plan',{});names={'records':'Source rows'}
    names.update({'group_'+str(i):name for i,name in enumerate(plan.get('group_by',[]))})
    for i,definition in enumerate(response.get('definitions',[])):
        unit=definition.get('units','');label=definition.get('label','m'+str(i))
        names['m'+str(i)]=label+(' ('+unit+')' if unit and str(unit).casefold()!=str(label).casefold() else '')
    if plan.get('operation')=='trend':names['period']='Period'
    return [names.get(c,c) for c in primary['columns']]


def interpretation_context(question,response):
    if not eligible(response):raise InterpretationError('This result type is not sent for interpretation.')
    primary=response['primary'];plan=response['plan']
    rows=[[_round(v) for v in row] for row in primary['rows'][:MAX_ROWS]]
    method=primary.get('method') if isinstance(primary.get('method'),dict) else {}
    context={'question':str(question)[:600],'analysis':plan.get('operation'),
        'grouping':plan.get('group_by',[]),'date_basis':plan.get('date_basis'),'grain':plan.get('grain'),
        'measures':[{'label':d.get('label'),'units':d.get('units'),'definition':str(d.get('formula') or d.get('aggregation') or '')[:200]} for d in response.get('definitions',[])][:4],
        'selected_source_rows':response.get('selected_rows'),
        'result_scope':method.get('result_scope','complete'),
        'rows_shown':len(rows),'rows_total':len(primary['rows']),'truncated':bool(primary.get('truncated')) or len(primary['rows'])>MAX_ROWS,
        'deterministic_summary':str(response.get('answer',''))[:600],
        'limitations':[str(v)[:300] for v in response.get('limitations',[])][:4],
        'table':{'columns':display_columns(response),'rows':rows}}
    payload=json.dumps(context,ensure_ascii=False,default=str,separators=(',',':'))
    if len(payload.encode())>16_000:
        context['table']['rows']=rows[:15];context['rows_shown']=15;context['truncated']=True
        payload=json.dumps(context,ensure_ascii=False,default=str,separators=(',',':'))
    return [{'role':'system','content':SYSTEM},{'role':'user','content':payload}],context


def reference_values(context):
    """Every number the narrative may cite: cells and simple, explicit derivations from them."""
    table=context['table'];rows=table['rows'];values=set();percents=set();years=set()
    columns=list(zip(*rows)) if rows else []
    for column in columns:
        numbers=[n for n in (_finite(v) for v in column) if n is not None]
        for v in column:
            if isinstance(v,str):years.update(int(y) for y in re.findall(r'\b(19\d{2}|20\d{2}|21\d{2})\b',v))
        values.update(numbers)
        if not numbers:continue
        total=sum(numbers);values.add(total);values.add(total/len(numbers))
        if 0<=min(numbers) and max(numbers)<=1:percents.update(n*100 for n in numbers)
        if total:percents.update(n/total*100 for n in numbers)
        for a,b in permutations(numbers[:MAX_ROWS],2):
            values.add(a-b)
            if b:values.add(a/b);percents.add((a-b)/abs(b)*100)
    for key in ('selected_source_rows','rows_shown','rows_total'):
        if _finite(context.get(key)) is not None:values.add(float(context[key]))
    values.add(float(len(rows)))
    return values,percents,years


def _matches(number,decimals,scale,pool):
    # Accept the value as written or rounded to the precision the sentence shows.
    tolerance=max(.5*10**(-decimals)*scale,1e-9)
    for candidate in pool:
        if abs(abs(candidate)-abs(number))<=max(tolerance,abs(candidate)*.005):return True
    return False


def unsupported_numbers(text,context,references=None):
    values,percents,years=references or reference_values(context)
    pool=sorted(values,key=abs);percent_pool=sorted(percents|{v*100 for v in values if abs(v)<=1},key=abs)
    problems=[]
    for match in NUMBER.finditer(text):
        sign,whole,fraction,suffix=match.groups()
        number=float(whole.replace(',','')+('.'+fraction if fraction else ''))
        decimals=len(fraction or '')
        if suffix in ('%','percent','pp'):
            if not _matches(number,decimals,1,percent_pool):problems.append(match.group(0).strip())
            continue
        scale=MULTIPLIER.get(suffix or '',1);value=number*scale
        if not suffix and not fraction and ',' not in whole and int(whole) in years:continue
        if not suffix and not fraction and number<=12:continue  # ordinals and small counts ("top 3", "2 groups")
        if not _matches(value,decimals,scale,pool):problems.append(match.group(0).strip())
    return problems


def _clean(text):
    text=re.sub(r'[`*_#>\[\]<]','',str(text)).strip()
    return re.sub(r'\s+',' ',text)


def verify(raw,context):
    """Return the checked interpretation; drop sentences with unverified numbers or causal claims."""
    if not isinstance(raw,dict) or not VALIDATOR.is_valid(raw):raise InterpretationError('The interpretation did not match its schema.')
    references=reference_values(context);removed=[];checked=0
    def admit(text,kind):
        nonlocal checked
        text=_clean(text)
        if not text:return None
        if CAUSAL.search(text):removed.append({'kind':kind,'reason':'causal wording'});return None
        bad=unsupported_numbers(text,context,references)
        if bad:removed.append({'kind':kind,'reason':'number not in computed result','values':bad[:3]});return None
        checked+=len(NUMBER.findall(text));return text
    headline=admit(raw['headline'],'headline')
    findings=[t for t in (admit(t,'finding') for t in raw['findings']) if t]
    caveats=[t for t in (admit(t,'caveat') for t in raw['caveats']) if t]
    follow_ups=[]
    for question in raw['follow_ups']:
        question=_clean(question)
        if question and not re.search(r'https?://|www\.|```',question,re.I) and question not in follow_ups:follow_ups.append(question[:170])
    if not headline and not findings:raise InterpretationError('No interpretation sentence could be verified against the computed result.')
    return {'status':'ok','headline':headline or findings.pop(0),'findings':findings,'caveats':caveats,'follow_ups':follow_ups[:3],
            'numbers_checked':checked,'removed':removed}


def interpret(client,question,response):
    """One bounded local inference. Returns a verified interpretation or raises."""
    messages,context=interpretation_context(question,response)
    raw,metadata=client.interpret(messages)
    result=verify(raw,context)
    result['backend']={k:metadata.get(k) for k in ('model','planning_seconds','server_metrics') if k in metadata}
    result['rows_sent']=context['rows_shown']
    return result
