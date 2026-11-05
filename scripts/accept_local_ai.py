"""Explicit real-Ollama acceptance on isolated SYNTHETIC data, never user data.

Run with the existing .venv, or double-click Check Local AI.command on a Mac.
No model installation, download, automatic approval or external provider.
"""
import argparse
from datetime import datetime,timezone
import json
import math
from pathlib import Path
import platform
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))

import duckdb
import pandas as pd
from insightpilot.universal import Workspace
from insightpilot.universal.context import AnalysisContext
from insightpilot.universal.engine import Engine,frame_of
from insightpilot.universal.metrics import Metric,save_metric
from insightpilot.universal.analyst_session import Conversation,analyze_question
from insightpilot.universal.local_ai import LocalConfig,OllamaClient,LocalAIError

ORIGINAL='Count the records in each job category. Show only the five categories with the highest counts, sorted from highest to lowest. Display the exact counts and create a bar chart.'


def fixture(directory):
    directory.mkdir(parents=True,exist_ok=True);rows=[]
    for g,n in enumerate([13,11,9,8,7,6,5,4,3,2,1,1]):
        for i in range(n):rows.append({'job':f'Category {chr(65+g)}','age':None if g==i==0 else 20+g*3+i,'education':f'Level {i%3+1}'})
    frame=pd.DataFrame(rows);source=directory/'SYNTHETIC-acceptance.csv';frame.to_csv(source,index=False)
    workspace=Workspace.create(directory/'workspace');workspace.add_file(source,'facts',provenance={'origin':'synthetic','purpose':'Explicit real-model acceptance; no real customers or business findings.'})
    workspace.approve_semantics('facts',{'job':'category','age':'measure','education':'category'},'One explicitly synthetic observation.',units={'age':'years'})
    metric=Metric('Records');save_metric(workspace,'facts',metric)
    engine=Engine(workspace,'facts',context=AnalysisContext(None,'month','job'))
    return engine,workspace.profile('facts'),metric,frame


def questions():
    # SQL below is a handwritten independent reference over original fixture
    # rows; it never uses Engine, Metric.compile or model-provided SQL.
    return [
        {'id':'count','question':'How many records are there?','sql':'SELECT count(*) AS records FROM fixture','measures':[('records',None,'records')]},
        {'id':'groups','question':'Count records by job.','sql':'SELECT job,count(*) AS records FROM fixture GROUP BY job ORDER BY records DESC,job','group':'job','measures':[('records',None,'records')],'chart':'bar'},
        {'id':'top5','question':ORIGINAL,'chain':'ranking','sql':'SELECT job,count(*) AS records FROM fixture GROUP BY job ORDER BY records DESC,job LIMIT 5','group':'job','measures':[('records',None,'records')],'limit':5,'ordered':True,'chart':'bar'},
        {'id':'top3','question':'Now show only the top three.','chain':'ranking','sql':'SELECT job,count(*) AS records FROM fixture GROUP BY job ORDER BY records DESC,job LIMIT 3','group':'job','measures':[('records',None,'records')],'limit':3,'ordered':True,'chart':'bar'},
        {'id':'chart','question':'Turn the previous result into a bar chart.','chain':'ranking','sql':'SELECT job,count(*) AS records FROM fixture GROUP BY job ORDER BY records DESC,job LIMIT 3','group':'job','measures':[('records',None,'records')],'limit':3,'ordered':True,'chart':'bar'},
        {'id':'resort','question':'Now sort those results by record count from lowest to highest.','chain':'ranking','sql':'WITH top_groups AS (SELECT job,count(*) AS records FROM fixture GROUP BY job ORDER BY records DESC,job LIMIT 3) SELECT * FROM top_groups ORDER BY records,job','group':'job','measures':[('records',None,'records')],'limit':3,'ordered':True,'chart':'bar'},
        {'id':'average','question':'Calculate average age by job.','sql':'SELECT job,avg(age) AS mean FROM fixture GROUP BY job','group':'job','measures':[('avg','age','mean')],'chart':'bar'},
        {'id':'median','question':'Show median age for each education category.','sql':'SELECT education,median(age) AS median FROM fixture GROUP BY education','group':'education','measures':[('median','age','median')],'chart':'bar'},
        {'id':'compound','question':'Compare average age and record count by job.','sql':'SELECT job,avg(age) AS mean,count(*) AS records FROM fixture GROUP BY job','group':'job','measures':[('avg','age','mean'),('records',None,'records')],'chart':'bar'},
        {'id':'filter','question':'Count records by job where age is at least 30.','sql':'SELECT job,count(*) AS records FROM fixture WHERE age>=30 GROUP BY job','group':'job','measures':[('records',None,'records')],'chart':'bar'},
        {'id':'rows','question':'Show records where age is above 40.','operation':'records','sql':'SELECT row_number() OVER () AS _row_id,* FROM raw_fixture QUALIFY try_cast(age AS DOUBLE)>40','chart':'table'},
        {'id':'missing','question':'How many records remain if you exclude missing age values?','sql':'SELECT count(*) AS records FROM fixture WHERE age IS NOT NULL','measures':[('records',None,'records')]},
        {'id':'smallest','question':'Show the ten smallest job groups by record count.','sql':'SELECT job,count(*) AS records FROM fixture GROUP BY job ORDER BY records,job LIMIT 10','group':'job','measures':[('records',None,'records')],'limit':10,'ordered':True,'chart':'bar'},
        {'id':'clarify','question':'Summarize the unavailable field called absent_field.','status':'clarify'},
        {'id':'refuse','question':'Create a new data file on disk containing this table.','status':'unsupported'},
        {'id':'guard_refuse','question':'Delete records from the dataset.','status':'unsupported'},
    ]


def same(a,b):
    if a is None or b is None or pd.isna(a) or pd.isna(b):return (a is None or pd.isna(a)) and (b is None or pd.isna(b))
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):return math.isclose(a,b,rel_tol=1e-10,abs_tol=1e-9)
    return a==b


def check(case,response,reference,*,check_charts=True):
    wanted=case.get('status','ok')
    if response['status']!=wanted:return False,'Expected '+wanted+', received '+response['status']+': '+response['answer']
    if wanted!='ok':return True,'Correct clarification/refusal; no calculation executed.'
    plan=response['plan'];actual=frame_of(response['primary']);op=case.get('operation','aggregate')
    if plan['operation']!=op:return False,'Wrong operation; the request must not become a different calculation.'
    if op=='records':
        columns=list(reference.columns)
        if not set(columns)<=set(actual.columns):return False,'Requested source rows/fields are missing.'
        rows=actual[columns].values.tolist();known=reference.values.tolist()
    else:
        group=case.get('group');groups=[group] if group else []
        if plan['group_by']!=groups:return False,'Wrong grouping field.'
        specs=case['measures'];aliases=[]
        for aggregation,column,label in specs:
            matches=[i for i,m in enumerate(plan['measures']) if (m['aggregation']==aggregation and m.get('column')==column) or (aggregation=='records' and m['aggregation']=='saved' and m.get('metric')=='Records')]
            if len(matches)!=1:return False,'Missing or different measure: '+aggregation
            aliases.append('m'+str(matches[0]))
        if len(plan['measures'])!=len(specs):return False,'The model added an unrequested measure.'
        rows=actual[(['group_0'] if group else [])+aliases].values.tolist()
        known=reference[([group] if group else [])+[s[2] for s in specs]].values.tolist()
        if case.get('limit') and plan['limit']!=case['limit']:return False,'Wrong requested top-N limit.'
        if group and not case.get('ordered'):rows.sort(key=lambda r:str(r[0]));known.sort(key=lambda r:str(r[0]))
    if len(rows)!=len(known) or any(len(a)!=len(b) or not all(same(x,y) for x,y in zip(a,b)) for a,b in zip(rows,known)):
        return False,'Executed values, cohort, number of rows or requested ordering disagree with independent DuckDB.'
    if not check_charts:return True,'Independent numerical result reconciled.'
    figures=[json.loads(v) for v in response.get('figures',[])]
    if case.get('chart')=='table' and figures:return False,'A row table was requested; an invented aggregate chart was returned.'
    if case.get('chart')=='bar':
        if len(figures)!=len(case['measures']):return False,'A calculated bar chart for each requested measure is missing.'
        for fig in figures:
            if not fig['data'] or any(t.get('type')!='bar' for t in fig['data']):return False,'Expected bar chart.'
        # Compare the actual plotted values with actual results, not model text.
        from insightpilot.universal.visuals import plotted_data
        import plotly.graph_objects as go
        for i,fig in enumerate(figures):
            plotted=plotted_data(go.Figure(fig))
            values=sorted(zip(plotted['y'],plotted['x']),key=lambda r:str(r[0]))
            calculated=sorted(zip(actual.group_0,actual['m'+str(i)]),key=lambda r:str(r[0]))
            if len(values)!=len(calculated) or not all(same(a[0],b[0]) and same(a[1],b[1]) for a,b in zip(values,calculated)):return False,'Chart categories or values disagree with the executed table.'
    return True,'Independent DuckDB values, scope, ordering and requested chart reconciled.'


def run(config,*,client=None,directory=None,diagnostics=False):
    directory=directory or ROOT/'outputs/local-ai-acceptance'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:6])
    directory.mkdir(parents=True,exist_ok=True,mode=0o700);path=directory/'report.json'
    report={'model':config.model,'endpoint':config.endpoint,'platform':platform.platform(),'actual_model_test':client is None,
        'data':'Isolated SYNTHETIC fixture; existing user workspaces are never selected or changed.','status':'running','results':[],'cases_run':0,
        'diagnostics_enabled':diagnostics,'diagnostics_file':None}
    def save():path.write_text(json.dumps(report,default=str,indent=2)+'\n')
    try:
        planner=client or OllamaClient(config);report['model_metadata']=planner.inspect_model()
    except LocalAIError as exc:
        report.update(status='model_unavailable',reason=str(exc),actual_model_executed=False,actual_model_quality_measured=False);save()
        print(str(exc)+'\nNo model cases executed. Report: '+str(path));return 2,report,path
    engine,profile,metric,frame=fixture(directory/'fixture');conversations={}
    observer=None
    if diagnostics:
        # Load the trusted sibling explicitly, including when this runner is
        # imported programmatically rather than executed as a CLI script.
        import importlib.util
        spec=importlib.util.spec_from_file_location('insightpilot_synthetic_diagnostics',Path(__file__).with_name('synthetic_plan_diagnostics.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        observer=module.SyntheticPlanDiagnostics(engine,directory/'rejected-plans.jsonl',[c['id'] for c in questions()])
        planner.observer=observer;report['diagnostics_file']='rejected-plans.jsonl'
        report['model']=config.model if config.model=='qwen3.5:4b' else '<model tag redacted>'
        report['model_metadata']={'status':'available','name':report['model'],'other_metadata':'omitted in diagnostic mode'}
        print('Explicit diagnostics enabled for the byte-verified synthetic fixture only. Unknown values/free text are redacted; no real user data or credentials are captured.',flush=True)
    with duckdb.connect() as con:
        con.register('fixture',frame)
        # Row views retain original CSV cell text; references must retain those
        # same source types instead of silently recasting displayed records.
        con.read_csv(str(engine.workspace.directory.parent/'SYNTHETIC-acceptance.csv'),header=True,all_varchar=True).create_view('raw_fixture')
        try:
            for case in questions():
                if observer is not None:observer.begin_case(case['id'])
                print('Checking '+case['id']+': '+case['question'],flush=True)
                reference=con.execute(case['sql']).df() if 'sql' in case else None
                conversation=conversations.setdefault(case.get('chain',case['id']),Conversation())
                response=analyze_question(engine,profile,metric,case['question'],conversation,mode='local',config=config,client=planner)
                # The companion report must not undo diagnostic redaction by
                # storing a free-text model message or a raw plan inside evidence.
                if observer is not None and response.get('backend',{}).get('model_attempts') and response['status']!='ok':
                    response=dict(response,answer='No executed answer; free text omitted. See status and redacted plan diagnostics.')
                passed,reason=check(case,response,reference)
                numbers_correct=check(case,response,reference,check_charts=False)[0] if reference is not None else None
                backend=response.get('backend',{})
                if observer is not None:observer.decision('result_'+response['status'])
                reported_plan=response.get('plan');reported_actual=response.get('primary');reported_backend=backend
                if observer is not None:
                    reported_plan=observer.project_plan(reported_plan)
                    reported_actual=None if reported_actual is None else {k:reported_actual.get(k) for k in ('columns','rows','version','source_sha256')}
                    reported_backend={k:backend.get(k) for k in ('model_attempts','repair_attempted','model_response_received','model_request_completed')}
                report['results'].append({'id':case['id'],'question':case['question'],'passed':passed,'reason':reason,'status':response['status'],
                    'model_attempts':backend.get('model_attempts',0),'repair_attempted':backend.get('repair_attempted',False),
                    'model_response_received':backend.get('model_response_received',False),
                    'handling':'local_model' if backend.get('model_attempts') else 'application_guard',
                    'valid_model_output':backend.get('model_request_completed') if backend.get('model_attempts') else None,
                    'plan_validated':bool(response.get('plan')),'execution_succeeded':response['status']=='ok',
                    'correct_numbers':numbers_correct,'appropriate_chart':passed if case.get('chart') else None,'chart_count':len(response.get('figures',[])),
                    'clarification':response['status']=='clarify','refusal':response['status']=='unsupported',
                    'validated_plan':reported_plan,'answer':response['answer'],'backend':reported_backend,
                    'actual':reported_actual,'independent_duckdb_reference':None if reference is None else reference.to_dict('split')})
                report['cases_run']=len(report['results']);save();print(('PASS ' if passed else 'FAIL ')+reason,flush=True)
        except KeyboardInterrupt:
            report['status']='interrupted';save();print('Stopped; existing workspaces were untouched. Report: '+str(path));return 130,report,path
    attempted=any(r['model_attempts'] for r in report['results'])
    confirmed=client is None and any(r['model_response_received'] for r in report['results'])
    report.update(status='completed',passed=sum(r['passed'] for r in report['results']),failed=sum(not r['passed'] for r in report['results']),
        actual_model_executed=True if confirmed else None if client is None and attempted else False,
        actual_model_quality_measured=confirmed)
    save();print(f"{report['passed']}/{report['cases_run']} passed. Report: {path}",flush=True)
    if report['failed']:print('Inspect the first failure and, if explicitly enabled, its redacted synthetic diagnostic plan. Keep qwen3.5:4b and record the Ollama version. No failure was treated as success or replaced with a different calculation.')
    return (1 if report['failed'] else 0),report,path


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',default='qwen3.5:4b');parser.add_argument('--endpoint',default='http://127.0.0.1:11434')
    parser.add_argument('--timeout',type=float,default=90)
    parser.add_argument('--diagnostics',action='store_true',help='Explicitly capture redacted model-plan projections for the built-in synthetic fixture only.')
    args=parser.parse_args(argv)
    try:config=LocalConfig(endpoint=args.endpoint,model=args.model,timeout_seconds=args.timeout).validated()
    except LocalAIError as exc:print(str(exc));return 2
    return run(config,diagnostics=args.diagnostics)[0]


if __name__=='__main__':raise SystemExit(main())
