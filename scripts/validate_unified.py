"""Initial authentic evaluations. These definitions are test inputs, not app presets.

The held-out source is deliberately absent here. Select it only after the engine
and these initial workflows have passed, and exercise its semantics through UI.
"""
from dataclasses import asdict
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import time
import uuid

import pandas as pd
import numpy as np
import plotly.graph_objects as go

from insightpilot.universal import Workspace
from insightpilot.universal.context import AnalysisContext,coverage
from insightpilot.universal.engine import Engine,Filter,frame_of
from insightpilot.universal.metrics import Metric,save_metric
from insightpilot.universal.sql_lab import SQLLab
from insightpilot.universal.report import configuration,replay,html_report
from insightpilot.universal.story import plan
from insightpilot.universal.insights import discover
from insightpilot.universal import visuals
from insightpilot.universal.assistant import answer
from insightpilot.universal.statistics import mean_interval,regression,welch_test
from insightpilot.universal.advanced import time_analysis,forecast
from insightpilot.universal.relationships import inspect_relationship,save_relationship
from insightpilot.universal.segmentation import cluster

ROOT=Path(__file__).resolve().parents[1]
SOURCES=ROOT/'data/unified-evaluation/sources'
INITIAL={
 'taxis.csv':{'url':'https://raw.githubusercontent.com/mwaskom/seaborn-data/71e2436a092d714350de0fc409ca8a8714e7e78f/taxis.csv',
    'sha256':'08d6d71784dbaa2651fee37fc03389754194c05d72d2d19cbc2c799dea6ac09d','rows':6433,
    'license':'NYC TLC public-use trip records; source usage terms apply. Seaborn mirror provides no separate dataset license. Not redistributed in this repository.',
    'citation':'NYC Taxi & Limousine Commission, March 2019 sample distributed by seaborn-data. Sample selection and modifications are not fully documented; no population inference.'},
 'airquality.csv':{'url':'https://raw.githubusercontent.com/vincentarelbundock/Rdatasets/1dcc2bf5f955cc1224a3e1307256e1fe86b68dae/csv/datasets/airquality.csv',
    'sha256':'65d2c4afd976c169af9bb0bd97e9e78e1e8a185f1b52e2e3153e30f90c7fb5f8','rows':153,
    'license':'R datasets package: GPL-2 or GPL-3; source documentation and attribution retained locally. No raw data redistributed in repository.',
    'citation':'R airquality, Chambers et al. (1983); daily New York observations May–September 1973. Ozone Roosevelt Island, other measurements at different stations; no city-wide exposure estimate.'}}


def receipt(path,details):
    assert hashlib.sha256(path.read_bytes()).hexdigest()==details['sha256']
    return {'origin':'verified_public_source_extract','retrieved_at':datetime.now(timezone.utc).isoformat(),**details}


def assert_close(actual,expected):
    if pd.isna(expected): assert actual is None or pd.isna(actual)
    else: assert abs(actual-expected)<max(1e-8,abs(expected)*1e-10),(actual,expected)


def independent_histogram(engine,column,values,bins=25):
    values=pd.to_numeric(values,errors='coerce');values=values[np.isfinite(values)]
    lo,hi=values.min(),values.max()
    if hi==lo: expected={0:len(values)}
    else:
        counts,_=np.histogram(values[values<hi],bins=np.linspace(lo,hi,bins+1))
        expected={i:int(n) for i,n in enumerate([*counts,int((values==hi).sum())]) if n}
    result=engine.distribution(column,bins)
    actual={int(round((start-lo)/(hi-lo)*bins)) if hi!=lo else 0:int(n) for start,n in result['rows']}
    assert actual==expected,(actual,expected)
    return {'field':column,'bins':bins,'finite_values':len(values),'reference':'Independent NumPy histogram with the documented separate exact-maximum boundary bin.','matched':True}


def common(w,table,metric,context,reference,run,key,filters=()):
    e=Engine(w,table,filters,context=context);info=e.info;report=w.profile(table)
    save_metric(w,table,metric)
    result=e.metric(metric);assert_close(result['rows'][0][0],reference)
    lab=SQLLab(e);query=lab.guided(metric)
    sql=lab.execute(query);assert_close(sql['rows'][0][0],reference)
    ai=answer(e,'Summarize the selected metric',metric,dimension=context.comparison,date_column=context.date_basis)
    assert_close(ai['evidence']['rows'][0][0],reference)
    config=configuration(e,metric);restored,m=replay(w,config);assert_close(restored.metric(m)['rows'][0][0],reference)
    w.save_object('view','Evaluation view',{'analysis':config,'layout':{'page':'Overview'}})
    lab.save('Evaluation aggregate',query)
    charts=[];results=[];chart_types=[]
    for p in plan(e,report,metric):
        if p['kind']=='trend':r=e.trend(p['field'],metric,context.time_grain);f=visuals.trend(r,p['title'],metric.units)
        elif p['kind']=='ranked':r=e.groups(p['field'],metric);f=visuals.ranked(r,p['title'],metric.units)
        else:r=e.distribution(p['field']);f=visuals.histogram(r,p['title'])
        results.append(r);charts.append(f);chart_types.append(p['kind'])
    for i,(r,f) in enumerate(zip(results,charts)):
        (run/(key+'-chart-'+str(i)+'.html')).write_text(f.to_html(include_plotlyjs=True))
        w.save_object('block',key+' chart '+str(i),{'analysis':config,'evidence':r,'figure':f.to_json()})
    findings=discover(e,context.comparison,metric,context.date_basis)
    html=html_report(e,metric,findings,results,charts,key+' · verified analytical report')
    (run/(key+'-report.html')).write_text(html)
    (run/(key+'-manifest.json')).write_text(json.dumps(config,default=str,indent=2))
    assert info['source']['sha256'] in html and 'source_sha256' in html and metric.name in html
    evidence={'metric':result,'sql':sql,'ai':ai['evidence']}
    (run/(key+'-evidence.json')).write_text(json.dumps(evidence,default=str,indent=2))
    sample=e.records(); assert len(sample['rows'])==min(100,e.count())
    return {'workspace':str(w.directory.relative_to(ROOT)), 'table':table,'rows':info['rows'],'selected_rows':e.count(),
        'source':{k:v for k,v in info['source'].items() if k!='retained'},'grain':info['grain'],'roles':info['roles'],
        'context':context.dictionary(),'metric':metric.dictionary(info),'value':result['rows'][0][0],
        'independent_pandas_value':reference,'sql_value':sql['rows'][0][0],'replay_value':restored.metric(m)['rows'][0][0],
        'filter_consistency':True,'chart_types':chart_types,'findings':[f['finding'] for f in findings],
        'report':str((run/(key+'-report.html')).relative_to(ROOT)), 'evidence':str((run/(key+'-evidence.json')).relative_to(ROOT)),
        'status':'passed API/reference evaluation; browser evaluation recorded separately'}


def taxis(run):
    path=SOURCES/'taxis.csv';raw=pd.read_csv(path);w=Workspace.create(run/'transactions')
    w.add_file(path,'observations',provenance=receipt(path,INITIAL['taxis.csv']))
    dates=['pickup','dropoff'];p=w.profile('observations')
    assert len([c for c in p['columns'] if c['recognized_dates']])==2
    assert coverage(w.info('observations'),p)['date_ambiguity']
    assert not plan(Engine(w,'observations'),p,Metric('Records'))
    w.approve_semantics('observations',{'pickup':'date','dropoff':'date','total':'measure','fare':'measure','tip':'measure','distance':'measure','color':'category','payment':'category','pickup_borough':'geography'},
        'One sampled completed taxi trip, not one rider; repeated riders are not identifiable.',
        units={'total':'USD','fare':'USD','tip':'USD','distance':'miles'},date_purposes={'pickup':'recorded local pickup calendar time (timezone absent in extract)','dropoff':'recorded local drop-off calendar time'},date_basis='pickup')
    metric=Metric('Recorded trip total','sum("total")','Sum of recorded total charges for selected trips; components are not re-added.','USD','Sampled observed charges, not profit or city-wide revenue.',currency='USD')
    filters=[Filter('color','eq','yellow')]
    result=common(w,'observations',metric,AnalysisContext('pickup','day','payment'),float(raw.loc[raw.color=='yellow','total'].sum()),run,'transactions',filters)
    e=Engine(w,'observations',filters,context=AnalysisContext('pickup','day','payment'))
    result['histogram_reference']=independent_histogram(e,'total',raw.loc[raw.color=='yellow','total'])
    r=regression(e,'distance','total');selected=raw.loc[raw.color=='yellow',['distance','total']].dropna()
    assert_close(r['pearson_r'],selected.corr().iloc[0,1]);result['pearson_r']=r['pearson_r']
    trend=frame_of(e.trend('pickup',metric,'day'))
    reference=raw[raw.color=='yellow'].groupby(pd.to_datetime(raw.loc[raw.color=='yellow','pickup']).dt.date).total.sum()
    for row in trend.itertuples():assert_close(row.value,reference.loc[row.period])
    result['daily_reference_reconciled']=len(trend)
    earlier=('2019-03-01','2019-03-07');later=('2019-03-15','2019-03-21')
    change=e.compare_periods('payment',metric,'pickup',earlier,later)
    dates=pd.to_datetime(raw.pickup).dt.strftime('%Y-%m-%d')
    before=raw.loc[(raw.color=='yellow') & dates.between(*earlier),'total'].sum()
    after=raw.loc[(raw.color=='yellow') & dates.between(*later),'total'].sum()
    assert_close(sum(r[3] for r in change['rows']),after-before)
    result['investigation_reference']={'earlier':earlier,'later':later,'delta':sum(r[3] for r in change['rows']),
        'independent_delta':float(after-before),'same_shared_filter':change['filters']==[asdict(f) for f in filters]}
    segmentation=cluster(e,['distance','total'],3)
    result['exploratory_segmentation']={k:v for k,v in segmentation.items() if k not in ('data','evidence','centroids')}
    result['exploratory_segmentation']['centroids']=segmentation['centroids'].to_dict('records')
    result['exploratory_segmentation']['causal_or_business_validation']=False
    return result


def observations(run):
    path=SOURCES/'airquality.csv';raw=pd.read_csv(path);w=Workspace.create(run/'time-observations')
    # Original source lacks a year column. An explicit source-documented constant
    # is derived outside the app; this is recorded, not inferred from a filename.
    converted=run/'airquality-with-documented-year.parquet'
    augmented=raw.assign(documented_year=1973);augmented.to_parquet(converted,index=False)
    details=receipt(path,INITIAL['airquality.csv']);details.update(original_sha256=details['sha256'],derived_sha256=hashlib.sha256(converted.read_bytes()).hexdigest(),derivation='Lossless Parquet conversion plus documented constant year 1973; original CSV preserved.')
    w.add_file(converted,'observations',provenance=details)
    w.transform('observations',{'kind':'nullify_many','markers':['NA']})
    w.transform('observations',{'kind':'date_parts','year':'documented_year','month':'Month','day':'Day','new_name':'observation_day'})
    w.approve_semantics('observations',{'rownames':'identifier','Ozone':'measure','Solar.R':'measure','Wind':'measure','Temp':'measure','Month':'category','Day':'category','documented_year':'category','observation_day':'date'},
        'One calendar day with station-specific measurements; unavailable measurements are missing, not zero.',units={'Ozone':'ppb','Solar.R':'Langleys','Wind':'mph','Temp':'deg F'},date_purposes={'observation_day':'documented observation day, 1973'},date_basis='observation_day')
    metric=Metric('Mean observed ozone','avg("Ozone")','Arithmetic mean over finite available ozone measurements.','ppb','37 days lack ozone; stations differ by measure and source coverage is limited.')
    result=common(w,'observations',metric,AnalysisContext('observation_day','month','Month'),float(raw.Ozone.mean()),run,'time-observations')
    e=Engine(w,'observations',context=AnalysisContext('observation_day','month','Month'));ci=mean_interval(e,'Ozone')
    result['histogram_reference']=independent_histogram(e,'Ozone',raw.Ozone)
    assert_close(ci['mean'],raw.Ozone.mean());result['confidence_interval']= {k:v for k,v in ci.items() if k!='evidence'}
    trend=frame_of(e.trend('observation_day',metric));ref=raw.groupby('Month').Ozone.mean()
    for row in trend.itertuples():assert_close(row.value,ref.loc[row.period.month])
    result['monthly_reference_reconciled']=len(trend)
    changes=time_analysis(e,'observation_day',metric);result['time_analysis_rows']=len(changes['data'])
    comparison=welch_test(e,'Ozone','Month',5,8)
    from scipy.stats import ttest_ind
    independent=ttest_ind(raw.loc[raw.Month==5,'Ozone'].dropna(),raw.loc[raw.Month==8,'Ozone'].dropna(),equal_var=False)
    assert_close(comparison['p_value'],float(independent.pvalue))
    result['welch_reference']={k:v for k,v in comparison.items() if k!='evidence'}
    result['welch_reference']['independent_scipy_p']=float(independent.pvalue)
    result['welch_reference']['independence_verified']=False
    try:forecast(e,'observation_day',metric)
    except ValueError as exc:result['forecast_guard']=str(exc)
    return result


def relational(run):
    from reproduce_public_cases import SOURCES as OLD
    root=ROOT/'data/real-cases';raw=pd.read_csv(root/'flights.csv');right=pd.read_csv(root/'airlines.csv')
    w=Workspace.create(run/'relational')
    for file,table in [('flights.csv','events'),('airlines.csv','entities')]:
        w.add_file(root/file,table,provenance=receipt(root/file,OLD[file]))
    w.transform('events',{'kind':'nullify_many','markers':['NA']})
    w.transform('events',{'kind':'date_parts','year':'year','month':'month','day':'day','new_name':'observed_day'})
    w.approve_semantics('events',{'rownames':'identifier','flight':'identifier','tailnum':'identifier','carrier':'company','origin':'geography','dest':'geography','arr_delay':'duration','dep_delay':'duration','distance':'measure','observed_day':'date','time_hour':'date'},
        'One scheduled NYC departure record in 2013; flight number alone is not unique.',units={'arr_delay':'minutes','dep_delay':'minutes','distance':'miles'},date_purposes={'observed_day':'scheduled departure calendar day from reviewed year/month/day','time_hour':'scheduled departure hour, not arrival or outcome time'},date_basis='observed_day')
    metric=Metric('Recorded arrival delay','avg("arr_delay")','Mean over finite recorded arrival delays; negative values are early arrivals.','minutes','Missing arrival delay is excluded; this is not a cancellation rate.')
    result=common(w,'events',metric,AnalysisContext('observed_day','month','carrier'),float(raw.arr_delay.mean()),run,'relational')
    result['histogram_reference']=independent_histogram(Engine(w,'events'),'arr_delay',raw.arr_delay)
    rel=inspect_relationship(w,'events','carrier','entities','carrier');save_relationship(w,'operator',rel)
    lab=SQLLab(Engine(w,'events',context=AnalysisContext('observed_day','month','carrier')))
    sql=lab.guided(metric,['entities.name'],relationship='operator');joined=lab.execute(sql)
    ref=raw.merge(right,on='carrier',how='left').groupby('name').arr_delay.mean()
    for row in joined['rows']:assert_close(row[1],ref.loc[row[0]])
    result['relationship']=rel;result['joined_groups']=len(joined['rows'])
    # A genuine related table has many unmatched entities (older aircraft).
    # The extra source is acquired separately by --download before this run.
    planes=SOURCES/'planes.csv'
    if planes.exists():
        w.add_file(planes,'equipment',provenance={'origin':'verified_public_source_extract','url':OLD['flights.csv']['url'].replace('flights.csv','planes.csv'),'license':'CC0, nycflights13','sha256':hashlib.sha256(planes.read_bytes()).hexdigest()})
        equipment=inspect_relationship(w,'events','tailnum','equipment','tailnum');save_relationship(w,'equipment',equipment)
        assert equipment['left_unmatched_rows']>0
        result['unmatched_relationship']=equipment
        equipment_lab=SQLLab(Engine(w,'events',context=AnalysisContext('observed_day','month','carrier')))
        query=equipment_lab.guided(metric,['equipment.manufacturer'],relationship='equipment')
        joined=equipment_lab.execute(query)
        equipment_frame=pd.read_csv(planes)
        reference=raw.merge(equipment_frame,on='tailnum',how='left').groupby('manufacturer',dropna=False).arr_delay.mean()
        for category,value,n in joined['rows']:
            expected=reference.loc[category] if category is not None else reference.loc[reference.index.isna()].iloc[0]
            assert_close(value,expected)
        assert sum(r[2] for r in joined['rows'])==len(raw)
        result['equipment_join_reference']={'groups':len(joined['rows']),'source_rows_retained':sum(r[2] for r in joined['rows']),
            'unmatched_left':equipment['left_unmatched_rows'],'reference':'Pandas left merge and grouped means, retaining missing manufacturer.'}
        (run/'relational-joined-report.html').write_text(html_report(equipment_lab.engine,metric,evidence=[joined],title='Validated related-table mean delay'))
    try:lab.execute('SELECT count(*) FROM events a JOIN entities b ON a.origin=b.carrier')
    except ValueError as exc:result['invalid_join_guard']=str(exc)
    else:raise AssertionError('Invalid join accepted')
    return result


def main():
    run=ROOT/'outputs/unified-evaluation'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:6]);run.mkdir(parents=True)
    results=[]
    for fn in [taxis,observations,relational]:
        started=time.perf_counter();r=fn(run);r['evaluation_seconds']=round(time.perf_counter()-started,3);results.append(r);print(fn.__name__,'passed',flush=True)
    payload={'generated_at':datetime.now(timezone.utc).isoformat(),'baseline_commit':'156a1169fb982fd3af6de35f52a5fb31abf31c0a','run_directory':str(run.relative_to(ROOT)),
        'reference_method':'Independent Pandas imports and calculations; no production expression/parser reused by reference. Explicit human-reviewed evaluation definitions, not runtime mappings.','initial_cases':results}
    out=ROOT/'docs/unified-initial-evaluation.json';out.write_text(json.dumps(payload,default=str,indent=2)+'\n');print(out)

if __name__=='__main__':main()
