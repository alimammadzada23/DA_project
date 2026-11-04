"""Reproducible question benchmark. Integration fixtures are NOT model quality.

--mode integration uses an explicit HTTP test double with manually authored plans.
--mode standard tests the finite zero-model parser and reports its failures.
--mode ollama calls a genuinely installed local model; never pulls/installs weights.
"""
import argparse
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime,timezone
import json
import math
from pathlib import Path
import platform
import resource
import sys
import time
import uuid

import httpx
import numpy as np
import pandas as pd

from local_ai_benchmark_cases import make_datasets,catalog,ROOT
from insightpilot.universal.analyst_session import Conversation,analyze_question,approve_pending
from insightpilot.universal.analyst_planner import guard_question
from insightpilot.universal.engine import Engine,Filter,frame_of
from insightpilot.universal.local_ai import LocalConfig,OllamaClient,LocalAIError


def normalize(value):
    if value is None or isinstance(value,(float,np.floating)) and not math.isfinite(value):return None
    if isinstance(value,(pd.Timestamp,datetime)):return value.date().isoformat()
    if hasattr(value,'isoformat') and not isinstance(value,str):return value.isoformat()
    if isinstance(value,np.generic):return value.item()
    return value


def mask(frame,condition):
    column=frame[condition['column']];op=condition['operator'];value=condition.get('value')
    missing=column.isna()|column.astype(str).eq('')
    if op=='is_missing':return missing
    if op=='not_missing':return ~missing
    if op in ('from','through'):
        dates=pd.to_datetime(column,utc=True,errors='coerce').dt.date;bound=pd.Timestamp(value).date()
        return dates.ge(bound) if op=='from' else dates.le(bound)
    if value is None:return missing if op=='eq' else ~missing
    if type(value) in (int,float):column=pd.to_numeric(column,errors='coerce')
    else:column=column.astype('string');value=str(value).lower() if type(value) is bool else value
    if op=='eq':return column.eq(value).fillna(False)
    if op=='ne':return column.ne(value).fillna(True)
    return {'gt':column.gt,'gte':column.ge,'lt':column.lt,'lte':column.le}[op](value).fillna(False)


def expanded_reference(case,prior):
    p=deepcopy(case['oracle_plan'])
    if p['action']!='analyze':return p
    if p.get('inherit'):
        old=deepcopy(prior['plan'])
        old.update(p);p=old
    for key,value in {'operation':'aggregate','group_by':[],'measures':[],'fields':[],'filters':[],'limit':15,'sort_by':'records','descending':True,'grain':'month','bins':25}.items():p.setdefault(key,value)
    return p


def aggregate(frame,spec,scope_size):
    agg=spec['aggregation'];name=spec.get('column')
    if agg=='records':return len(frame),{}
    if agg=='share':return len(frame)/scope_size if scope_size else None,{}
    if agg=='rate':
        den=spec['denominator'];eligible=pd.Series(True,index=frame.index)
        if den['kind']=='nonmissing':eligible=frame[den['column']].notna()&frame[den['column']].astype(str).ne('')
        elif den['kind']=='condition':eligible=mask(frame,den['condition'])
        numerator=int((mask(frame,spec['numerator'])&eligible).sum());denominator=int(eligible.sum())
        return numerator/denominator if denominator else None,{'numerator':numerator,'denominator':denominator}
    if agg=='saved':
        if spec['metric']=='Records':return len(frame),{}
        raise ValueError('Reference needs an explicit reviewed metric mapping.')
    series=frame[name]
    if agg=='count':return int((series.notna()&series.astype(str).ne('')).sum()),{}
    if agg=='distinct':return series.dropna().astype(str).replace('',np.nan).nunique(),{}
    values=pd.to_numeric(series,errors='coerce');values=values[np.isfinite(values)]
    if not len(values):return None,{}
    if agg=='quantile':return float(values.quantile(spec['quantile'])),{}
    return float({'sum':values.sum,'avg':values.mean,'median':values.median,'min':values.min,'max':values.max}[agg]()),{}


def reference(case,dataset,prior=None):
    p=expanded_reference(case,prior)
    if case['expected_status']!='ok':return {'plan':p,'columns':[],'rows':[]}
    frame=dataset['raw'].copy()
    for condition in case.get('shared_filters',[]):frame=frame.loc[mask(frame,condition)]
    for condition in p['filters']:frame=frame.loc[mask(frame,condition)]
    scope_size=len(frame);groups=p['group_by'];op=p['operation'];cohort=[]
    if case['oracle_plan'].get('inherit') and prior and p['measures']!=prior['plan'].get('measures') and groups==prior['plan'].get('group_by'):
        cohort=[tuple(row[:len(groups)]) for row in prior['rows']]
    if cohort:
        allowed=set(cohort);frame=frame.loc[[tuple(normalize(v) for v in r) in allowed for r in frame[groups].itertuples(index=False,name=None)]]
    if op in ('aggregate','trend'):
        actual_groups=list(groups)
        if op=='trend':
            basis=dataset['engine'].context.date_basis
            dates=pd.to_datetime(frame[basis],utc=True,errors='coerce').dt.tz_convert(None)
            frame=frame.loc[dates.notna()].copy();dates=dates[dates.notna()]
            frame['period']=dates.dt.to_period({'day':'D','week':'W-SUN','month':'M','quarter':'Q','year':'Y'}[p['grain']]).dt.start_time.dt.date
            actual_groups=['period']
        grouped=frame.groupby(actual_groups,dropna=False,sort=False) if actual_groups else [((),frame)]
        rows=[];columns=[('period' if op=='trend' else 'group_'+str(i)) for i in range(len(actual_groups))]
        values_cols=['m'+str(i) for i in range(len(p['measures']))]
        rate_cols=[label+'_'+str(i) for i,m in enumerate(p['measures']) if m['aggregation']=='rate' for label in ('numerator','denominator')]
        for keys,part in grouped:
            if not isinstance(keys,tuple):keys=(keys,)
            values=[];rate_values=[]
            for spec in p['measures']:
                value,components=aggregate(part,spec,scope_size);values.append(value)
                if components:rate_values.extend([components['numerator'],components['denominator']])
            rows.append([*(normalize(k) for k in keys),*values,*rate_values,len(part)])
        columns += values_cols+rate_cols+['records'];result=pd.DataFrame(rows,columns=columns)
        if op=='trend':sort=['period'];ascending=[True]
        else:
            order='group_0' if p['sort_by']=='group' and groups else p['sort_by'] if p['sort_by'] not in ('group','period') else 'records'
            sort=[order]+[c for c in columns if c.startswith('group_') and c!=order];ascending=[not p['descending']]+[True]*(len(sort)-1)
        result=result.sort_values(sort,ascending=ascending,na_position='last',kind='stable').head(1000 if op=='trend' else p['limit'])
        return {'plan':p,'columns':columns,'rows':[[normalize(v) for v in r] for r in result.itertuples(index=False,name=None)]}
    if op in ('schema','missing'):
        rows=[]
        for column in p['fields'] or list(frame.columns):
            missing=int((frame[column].isna()|frame[column].astype(str).eq('')).sum())
            if op=='missing' and not missing:continue
            rows.append([column,missing,len(frame)])
        return {'plan':p,'columns':['field','missing_rows','selected_rows'],'rows':rows}
    field=p['fields'][0] if p['fields'] else None
    if op in ('summary','distribution','outliers','group_distribution'):
        values=pd.to_numeric(frame[field],errors='coerce');values=values[np.isfinite(values)]
    if op=='distribution':
        lo,hi=values.min(),values.max();bins=p['bins']
        if lo==hi:rows=[[lo,len(values)]]
        else:
            counts,edges=np.histogram(values[values<hi],bins=np.linspace(lo,hi,bins+1));rows=[[float(edge),int(n)] for edge,n in zip(edges[:-1],counts) if n]
            if int((values==hi).sum()):rows.append([float(hi),int((values==hi).sum())])
        return {'plan':p,'columns':['bin_start','records'],'rows':rows}
    if op=='summary':
        return {'plan':p,'columns':['records','valid','mean','sd','minimum','q1','median','q3','maximum','sample_skewness'],
            'rows':[[len(frame),len(values),values.mean(),values.std(ddof=1),values.min(),values.quantile(.25),values.median(),values.quantile(.75),values.max(),values.skew()]]}
    if op=='outliers':
        q1,q3=values.quantile(.25),values.quantile(.75);iqr=q3-q1;lo,hi=q1-1.5*iqr,q3+1.5*iqr
        unusual=values[(values<lo)|(values>hi)]
        rows=[[int(index)+1,float(v),max((lo-v)/iqr,(v-hi)/iqr,0)] for index,v in unusual.items()]
        rows=sorted(rows,key=lambda r:(-r[2],r[0]))[:1000]
        return {'plan':p,'columns':['_row_id','value','severity_iqr'],'rows':rows}
    if op=='scatter':
        fields=p['fields'];selected=frame.head(2000);rows=[[int(i)+1,*[normalize(pd.to_numeric(pd.Series([r[f]]),errors='coerce').iloc[0]) for f in fields]] for i,r in selected.iterrows()]
        return {'plan':p,'columns':['_row_id','x','y'],'rows':rows}
    if op=='group_distribution':
        rows=[]
        for key,part in frame.groupby(groups[0],dropna=False):
            series=pd.to_numeric(part[field],errors='coerce').dropna()
            rows.append([normalize(key),len(series),series.min(),series.quantile(.25),series.median(),series.quantile(.75),series.max(),len(part)])
        result=pd.DataFrame(rows,columns=['group_0','valid','minimum','q1','median','q3','maximum','records']).sort_values(['records','group_0'],ascending=[False,True]).head(p['limit'])
        return {'plan':p,'columns':list(result.columns),'rows':list(result.itertuples(index=False,name=None))}
    if op=='compare_periods':
        dates=pd.to_datetime(frame[dataset['engine'].context.date_basis]).dt.strftime('%Y-%m-%d');a,b,c,d=p['periods'];parts=[]
        for start,end in [(a,b),(c,d)]:parts.append(frame.loc[dates.between(start,end)].groupby(groups[0]).size())
        rows=[]
        for key in sorted(set(parts[0].index)|set(parts[1].index)):
            before=float(parts[0].get(key,0));after=float(parts[1].get(key,0));rows.append([key,before,after,after-before,None if not before else (after-before)/abs(before)])
        return {'plan':p,'columns':['category','earlier','later','change','relative_change'],'rows':rows,'unordered':True}
    raise ValueError('No independent reference implementation for '+op)


def equal(actual,expected):
    if actual.get('status')!='ok':return False,'Expected an executed result, got '+actual.get('status','unknown')
    result=actual['primary'];columns=expected['columns']
    if set(columns)-set(result['columns']):return False,'Expected result columns are missing.'
    rows=[[normalize(r[result['columns'].index(c)]) for c in columns] for r in result['rows']]
    ref=[[normalize(v) for v in row] for row in expected['rows']]
    if expected.get('unordered'):
        rows.sort(key=lambda r:str(r[0]));ref.sort(key=lambda r:str(r[0]))
    if len(rows)!=len(ref):return False,f'Row count {len(rows)} != {len(ref)}'
    for row,known in zip(rows,ref):
        for a,b in zip(row,known):
            if type(a) in (int,float) and type(b) in (int,float):
                if not math.isclose(a,b,rel_tol=1e-9,abs_tol=1e-8):return False,'Independent numerical reference mismatch.'
            elif a!=b:return False,'Grouping, ordering, missing-value or field mismatch.'
    return True,'Independent calculation reconciled.'


def field_set(plan):
    fields=set(plan.get('fields',[]))|set(plan.get('group_by',[]))
    for m in plan.get('measures',[]):
        if m.get('column'):fields.add(m['column'])
        if m.get('numerator'):fields.add(m['numerator']['column'])
        d=m.get('denominator',{})
        if d.get('column'):fields.add(d['column'])
        if d.get('condition'):fields.add(d['condition']['column'])
    fields|={f['column'] for f in plan.get('filters',[])}
    return fields


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=['integration','standard','ollama'],default='integration')
    parser.add_argument('--model',default='qwen3.5:4b');parser.add_argument('--endpoint',default='http://127.0.0.1:11434')
    parser.add_argument('--approve-benchmark-definitions',action='store_true');parser.add_argument('--output',type=Path,default=Path('outputs/local-ai-benchmark.json'))
    args=parser.parse_args();cases=catalog();started=time.perf_counter()
    actual_scores={key:None for key in ['valid_plan_generation','correct_field_selection','correct_operation_selection','correct_clarification_refusal','end_to_end_answers','incorrect_plans','unsupported_behavior']}
    meta={'mode':args.mode,'model':args.model if args.mode=='ollama' else 'test-double:latest' if args.mode=='integration' else None,
        'actual_model_quality_measured':args.mode=='ollama','automatic_weight_download':False,'case_count':len(cases),
        'reference':'Independent Pandas/NumPy calculations on the original input frames; manually specified reference intents. Mock plans prove software integration, not language understanding.',
        'platform':platform.platform(),'python':platform.python_version(),
        'apple_silicon_tested':platform.system()=='Darwin' and platform.machine().lower() in ('arm64','aarch64')}
    if args.mode=='ollama':
        try:OllamaClient(LocalConfig(args.endpoint,args.model)).inspect_model()
        except LocalAIError as exc:
            report=meta|{'status':'actual_model_unavailable','actual_model_quality_measured':False,'reason':str(exc),'code':exc.code,'model_quality':actual_scores,'cases_run':0,'model_memory_mib':None}
            args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2));raise SystemExit(2)
    run=ROOT/'outputs/local-ai-benchmark-runs'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:6]);run.mkdir(parents=True)
    datasets=make_datasets(run);conversations={};references={};results=[]
    for case in cases:
        dataset=datasets[case['dataset']];engine=dataset['engine'];profile=dataset['profile'];metric=dataset['metric']
        if case.get('shared_filters'):engine=Engine(engine.workspace,engine.table,[Filter(f['column'],f['operator'],f.get('value')) for f in case['shared_filters']],context=engine.context)
        key=case['chain'] or case['id'];conversation=conversations.setdefault(key,Conversation());prior=references.get(key)
        expected=reference(case,dataset,prior);request_plan=deepcopy(case['oracle_plan'])
        if args.mode=='integration':
            def handler(request):
                if request.url.path=='/api/tags':return httpx.Response(200,json={'models':[{'name':'test-double:latest','size':0}]})
                if request.url.path=='/api/show':return httpx.Response(200,json={'capabilities':['completion'],'details':{'family':'explicit-test-double'}})
                assert request.url.path=='/api/chat'
                return httpx.Response(200,json={'model':'test-double:latest','done':True,'message':{'role':'assistant','content':json.dumps(request_plan)}})
            config=LocalConfig(model='test-double:latest');client=OllamaClient(config,transport=httpx.MockTransport(handler));mode='local'
        elif args.mode=='ollama':config=LocalConfig(args.endpoint,args.model);client=None;mode='local'
        else:config=None;client=None;mode='standard'
        begin=time.perf_counter();response=analyze_question(engine,profile,metric,case['question'],conversation,mode=mode,config=config,client=client)
        if response['status']=='approval_required' and args.approve_benchmark_definitions:
            proposed=response['plan']['measures']
            if proposed==expected['plan'].get('measures'):response=approve_pending(engine,profile,metric,conversation)
        elapsed=time.perf_counter()-begin
        if case['expected_status']=='ok':passed,reason=equal(response,expected)
        else:passed=response['status']==case['expected_status'];reason='Correct clarification/refusal.' if passed else 'Unexpected clarification/refusal behavior.'
        plan=response.get('plan',{})
        field_correct=field_set(plan)==field_set(expected['plan']) if case['expected_status']=='ok' else None
        operation_correct=(plan.get('operation')==expected['plan'].get('operation') and [m['aggregation'] for m in plan.get('measures',[])]==[m['aggregation'] for m in expected['plan'].get('measures',[])]) if case['expected_status']=='ok' else None
        model_attempted=mode=='local' and guard_question(case['question'],engine.table) is None
        results.append({'id':case['id'],'dataset':case['dataset'],'heldout':dataset['heldout'],'category':case['category'],'question':case['question'],
            'expected_status':case['expected_status'],'status':response['status'],'passed':passed,'reason':reason,'field_selection_correct':field_correct,
            'operation_selection_correct':operation_correct,'model_request_attempted':model_attempted,'seconds':round(elapsed,4),
            'answer':response['answer'],'validated_plan':plan,'backend':response.get('backend',{}),
            'error_code':response.get('error_code'),
            'actual':{k:response.get('primary',{}).get(k) for k in ('columns','rows','filters','context','source_sha256','version')},
            'evidence_id':response.get('primary',{}).get('evidence_id'),'reference':{'columns':expected['columns'],'rows':[[normalize(v) for v in row] for row in expected['rows'][:100]]}})
        if case['expected_status']=='ok':references[key]=expected
    def metric_for(eligible,good):
        selected=[r for r in results if eligible(r)];n=sum(bool(good(r)) for r in selected)
        return {'numerator':n,'denominator':len(selected),'fraction':n/len(selected) if selected else None}
    integration_scores={
        'executed_or_correctly_clarified':metric_for(lambda r:True,lambda r:r['passed']),
        'field_selection':metric_for(lambda r:r['expected_status']=='ok',lambda r:r['field_selection_correct']),
        'operation_selection':metric_for(lambda r:r['expected_status']=='ok',lambda r:r['operation_selection_correct']),
        'clarification_refusal':metric_for(lambda r:r['expected_status']!='ok',lambda r:r['passed']),
        'heldout':metric_for(lambda r:r['heldout'],lambda r:r['passed'])}
    if args.mode=='ollama':
        actual_scores={
            'valid_plan_generation':metric_for(lambda r:r['model_request_attempted'],lambda r:r['status'] in ('ok','clarify','unsupported','approval_required') and bool(r['validated_plan'])),
            'correct_field_selection':metric_for(lambda r:r['model_request_attempted'] and r['expected_status']=='ok',lambda r:r['field_selection_correct']),
            'correct_operation_selection':metric_for(lambda r:r['model_request_attempted'] and r['expected_status']=='ok',lambda r:r['operation_selection_correct']),
            'correct_clarification_refusal':metric_for(lambda r:r['model_request_attempted'] and r['expected_status']!='ok',lambda r:r['passed']),
            'end_to_end_answers':integration_scores['executed_or_correctly_clarified'],
            'incorrect_plans':metric_for(lambda r:r['model_request_attempted'] and r['status']=='ok',lambda r:not r['passed']),
            'unsupported_behavior':metric_for(lambda r:r['model_request_attempted'] and r['expected_status']=='unsupported',lambda r:r['status']=='unsupported')}
    peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024 if platform.system()=='Linux' else 1024**2)
    report=meta|{'status':'completed','generated_at':datetime.now(timezone.utc).isoformat(),'run_directory':str(run.relative_to(ROOT)),
        'cases_run':len(results),'passed':sum(r['passed'] for r in results),'failed':sum(not r['passed'] for r in results),
        'metric_scope':'Model-only field/operation/clarification/refusal scores exclude application-guarded requests. End-to-end answers include the whole pipeline. Incorrect plans means accepted model plans that fail independent result reconciliation, including filters, definitions and ordering. Zero eligible cases have null fractions.',
        'integration_scores':integration_scores,'model_quality':actual_scores,'latency_scope':'Application integration latency; mock timings are not model inference speed.',
        'elapsed_seconds':round(time.perf_counter()-started,3),'client_process_peak_rss_mib':round(peak,1),'model_memory_mib':None,
        'initialization_time_scope':'Server load_duration when returned by a real model; no fabricated initialization or model RAM result.',
        'datasets':{name:{'rows':len(d['raw']),'columns':list(d['raw'].columns),'origin':d['provenance'],'heldout':d['heldout']} for name,d in datasets.items()},'results':results}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,default=normalize,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('results','datasets')},indent=2))
    for r in results:
        if not r['passed']:print('FAILED',r['id'],r['status'],r['reason'],r['answer'])
    if report['failed']:raise SystemExit(1)

if __name__=='__main__':main()
