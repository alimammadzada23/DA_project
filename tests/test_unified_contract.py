"""Cross-cutting product contracts, independently calculated small fixtures."""
from dataclasses import asdict
from pathlib import Path
import io
import json
import zipfile

import pytest

from insightpilot.universal import Workspace
from insightpilot.universal.context import AnalysisContext,coverage
from insightpilot.universal.engine import Engine,Filter,frame_of
from insightpilot.universal.metrics import Metric,save_metric
from insightpilot.universal.sql_lab import SQLLab
from insightpilot.universal.relationships import inspect_relationship,save_relationship
from insightpilot.universal.report import configuration,replay,html_report
from insightpilot.universal.story import plan
from insightpilot.universal.exports import selection_package
from insightpilot.universal.assistant import answer


@pytest.fixture
def model(tmp_path):
    p=tmp_path/'neutral.csv'
    p.write_text('key,v,w,outcome,group,t0,t1\nA,10,1,yes,X,2025-01-01,2025-02-01\nB,20,3,no,Y,2025-01-02,2025-02-02\nA,30,2,yes,X,2025-02-01,2025-03-01\nC,,4,,Y,2025-02-02,2025-03-02\n')
    w=Workspace.create(tmp_path/'workspace');w.add_file(p,'observations')
    roles={'key':'identifier','v':'measure','w':'quantity','outcome':'status','group':'category','t0':'date','t1':'date'}
    w.approve_semantics('observations',roles,'One observed event',units={'v':'USD','w':'items'},date_purposes={'t0':'observation','t1':'processing'},date_basis='t0')
    return w


@pytest.mark.parametrize('formula,expected',[
    ('count()',4),('distinct("key")',3),('sum("v")',60),('avg("v")',20),('median("v")',20),
    ('quantile("v",0.9)',28),('count_if("outcome","==","yes")',2),
    ('sum_if("v","group","==","X")',40),('sum("v" * "w")',130),
    ('count_if("outcome","==","yes") / count("outcome")',2/3),
    ('weighted_avg("v","w")',130/6),('sum("v") / 0',None)])
def test_safe_metric_language_matches_manual_reference(model,formula,expected):
    e=Engine(model,'observations');m=Metric('Reviewed',formula)
    value=e.metric(m)['rows'][0][0]
    if expected is None: assert value is None
    else: assert value==pytest.approx(expected)
    sql=SQLLab(e).guided(m)
    assert SQLLab(e).execute(sql)['rows'][0][0]==pytest.approx(expected) if expected is not None else SQLLab(e).execute(sql)['rows'][0][0] is None


@pytest.mark.parametrize('sql',[
    'DELETE FROM observations','UPDATE observations SET v=0','CREATE TABLE x AS SELECT * FROM observations',
    'SELECT * FROM observations; SELECT * FROM observations',"COPY observations TO '/tmp/leak.csv'",
    "INSTALL httpfs", "LOAD httpfs", "ATTACH '/tmp/other.duckdb' AS other",
    "SELECT * FROM read_csv('/etc/passwd')", "SELECT * FROM read_parquet('https://example.test/x')",
    'SELECT * FROM _catalog','SELECT * FROM _metrics','SELECT * FROM information_schema.tables',
    'SELECT * FROM other.main.observations','SELECT * FROM range(999999999)',
    "SELECT current_setting('threads') FROM observations",'SELECT getenv(\'GH_TOKEN\') FROM observations',
    "SELECT query('SELECT * FROM _catalog') FROM observations",'SELECT * FROM duckdb_secrets()',
    'WITH selected AS (SELECT * FROM observations) SELECT * FROM selected',
    'WITH observations AS (SELECT 1) SELECT * FROM observations',
    'WITH a AS (SELECT * FROM b), b AS (SELECT * FROM observations) SELECT * FROM a',
    'WITH RECURSIVE a AS (SELECT * FROM observations UNION ALL SELECT * FROM a) SELECT * FROM a',
    'SELECT * FROM observations a CROSS JOIN observations b',
    'SELECT * FROM observations a JOIN observations b ON a.key=b.key',
])
def test_sql_lab_fails_closed(model,sql):
    with pytest.raises(ValueError): SQLLab(Engine(model,'observations')).execute(sql)
    assert Engine(model,'observations').count()==4


def test_sql_cte_windows_explain_paging_and_shared_context(model):
    e=Engine(model,'observations',[Filter('group','eq','X')],context=AnalysisContext('t0','month','group'))
    l=SQLLab(e)
    assert l.execute('WITH x AS (SELECT * FROM observations) SELECT sum(try_cast(v AS DOUBLE)),count(*) FROM x')['rows']==[(40,2)]
    r=l.execute('SELECT key,row_number() OVER (ORDER BY _row_id) n FROM selected ORDER BY _row_id',size=1)
    assert r['rows']==[('A',1)] and r['truncated']
    assert l.execute('SELECT key FROM observations ORDER BY _row_id',page=1,size=1)['rows']==[('A',)]
    assert 'physical_plan' in str(l.execute('SELECT count(*) FROM observations',explain=True)['rows'])
    assert r['context']==e.context.dictionary() and r['filters']==[asdict(f) for f in e.filters]
    l.save('reviewed query','SELECT count(*) FROM observations')
    restored,q=SQLLab(Engine(model,'observations')).reopen('reviewed query')
    assert restored.execute(q)['rows']==[(2,)]


def test_approved_join_retains_unmatched_and_rejects_wrong_keys_and_multiplication(model,tmp_path):
    p=tmp_path/'lookup.csv';p.write_text('code,label\nA,Alpha\nB,Beta\nD,Delta\n');model.add_file(p,'entities')
    rel=inspect_relationship(model,'observations','key','entities','code')
    assert rel['left_unmatched_rows']==1 and rel['right_unmatched_rows']==1 and rel['left_join_rows']==4
    save_relationship(model,'entity',rel)
    e=Engine(model,'observations');lab=SQLLab(e)
    result=lab.execute(lab.guided(Metric('Amount','sum("v")'),['entities.label'],relationship='entity'))
    assert result['rows']==[('Alpha',40.,2),('Beta',20.,1),(None,None,1)]
    with pytest.raises(ValueError):lab.execute('SELECT * FROM observations f LEFT JOIN entities d ON f.group=d.code')
    p.write_text('code,label\nA,Alpha\nA,Other\n');model.add_file(p,'repeated')
    rel=inspect_relationship(model,'observations','key','repeated','code')
    with pytest.raises(ValueError):save_relationship(model,'bad',rel)
    save_relationship(model,'acknowledged',rel,allow_multiplication=True)
    with pytest.raises(ValueError):SQLLab(e).execute('SELECT * FROM observations f LEFT JOIN repeated d ON f.key=d.code')


def test_ambiguous_dates_are_withheld_until_meaning_is_reviewed(model):
    info=model.info('observations');report=model.profile('observations')
    info['semantics']={'reviewed':False};info['roles']={}
    with model.connect(write=True) as con:model._save_info(con,'observations',info)
    assert coverage(info,report)['date_ambiguity']
    assert not any(c['kind']=='trend' for c in plan(Engine(model,'observations'),report,Metric('Rows')))
    with pytest.raises(ValueError):Engine(model,'observations',context=AnalysisContext('t1'))


def test_reports_replay_preserve_semantics_and_undo(model):
    e=Engine(model,'observations',[Filter('group','eq','X')],context=AnalysisContext('t0','month','group'))
    m=Metric('Value','sum("v")','Observed total','USD')
    config=configuration(e,m);before=e.info['current']
    model.save_object('view','review',{'analysis':config,'layout':{'page':'Explore'}})
    model.transform('observations',{'kind':'rename','column':'v','new_name':'arbitrary'})
    assert not model.info('observations')['semantics']['reviewed']
    restored,metric=replay(model,config)
    assert restored.metric(metric)['rows'][0][:2]==(40,2)
    assert restored.context.date_basis=='t0' and restored.info['current']==before
    html=html_report(restored,metric)
    assert 'sum(' in html and e.info['source']['sha256'] in html and 'context' in html
    model.activate('observations',before)
    assert model.info('observations')['roles']['v']=='measure' and model.info('observations')['semantics']['date_basis']=='t0'


def test_metric_units_guard_and_formula_injection(model):
    with pytest.raises(ValueError,match='Incompatible units'):save_metric(model,'observations',Metric('Bad','sum("v") + sum("w")'))
    with pytest.raises(ValueError):Metric('Bad','sum("key")').compile(model.columns('observations'),model.info('observations')['roles'])
    for expr in ['__import__("os").system("id")','sum("v");DELETE FROM x','sum("v") ** 100','quantile("v",2)']:
        with pytest.raises(ValueError):Metric('Bad',expr).compile(model.columns('observations'))


def test_ai_and_full_selection_export_use_same_scope(model):
    e=Engine(model,'observations',[Filter('group','eq','X')],context=AnalysisContext('t0','month','group'))
    m=Metric('Value','sum("v")','Observed total','USD')
    r=answer(e,'Summarize the selected metric',m)
    assert r['evidence']['rows'][0][:2]==(40,2)
    assert r['evidence']['context']==e.context.dictionary()
    package=selection_package(e,m)
    with zipfile.ZipFile(io.BytesIO(package)) as z:
        assert len(z.read('selected-records.csv').decode().splitlines())==3
        assert json.loads(z.read('analysis-manifest.json'))['context']['date_basis']=='t0'
        assert 'PowerQuery.m' in z.namelist()
    with pytest.raises(ValueError):selection_package(e,m,max_rows=1)


def test_xes_enters_same_catalog_and_process_evidence(database,tmp_path):
    from insightpilot.universal.process import import_xes,analyze_events
    w=Workspace.create(tmp_path/'event-model')
    imported=import_xes(w,database.parent/'synthetic.xes','stream')
    assert set(w.tables())=={'stream_cases','stream_events'}
    assert w.info('stream_events')['rows']==10
    assert w.columns('stream_cases')==['case_id','source_attributes']
    assert all('original_xes_sha256' in i['source'] for i in imported)
    w.approve_semantics('stream_events',{'case_id':'identifier','event_time':'date','activity':'category'},'One recorded event; traces can be incomplete.',date_purposes={'event_time':'recorded event timestamp'},date_basis='event_time')
    e=Engine(w,'stream_events',context=AnalysisContext('event_time'))
    results=analyze_events(e,'case_id','activity')
    assert results['coverage']['rows'][0][0]==10
    assert results['transitions']['rows'] and results['variants']['rows']
    assert results['transitions']['context']['date_basis']=='event_time'


@pytest.mark.parametrize('format',['csv','tsv','jsonl'])
def test_neutral_names_reorder_sparse_duplicates_and_ambiguous_dates_generalize(model,tmp_path,format):
    import pandas as pd
    original=model.info('observations');frame=frame_of(Engine(model,'observations').records()).drop(columns='_row_id')
    rename={c:f'z{index:02}' for index,c in enumerate(frame.columns)}
    frame=frame.rename(columns=rename)
    frame[rename['group']]=frame[rename['group']].replace({'X':'segment-q','Y':'segment-r'})
    frame['z90']=['01/02/2025','03/04/2025',None,'05/06/2025']
    frame=frame[list(frame.columns)[::-1]]
    frame=pd.concat([frame,frame.iloc[[0]]],ignore_index=True)
    path=tmp_path/('unseen-'+format+'.'+format)
    if format=='jsonl':frame.to_json(path,orient='records',lines=True)
    else:frame.to_csv(path,index=False,sep='\t' if format=='tsv' else ',')
    w=Workspace.create(tmp_path/('unseen-'+format));w.add_file(path,'unknown')
    profile=w.profile('unknown')
    assert next(c for c in profile['columns'] if c['column']=='z90')['recognized_dates']==0
    assert profile['exact_duplicate_rows']==1
    roles={rename[k]:v for k,v in original['roles'].items()}
    purposes={rename[k]:v for k,v in original['semantics']['date_purposes'].items()}
    w.approve_semantics('unknown',roles,original['grain'],date_purposes=purposes,date_basis=rename['t0'])
    e=Engine(w,'unknown',context=AnalysisContext(rename['t0'],'month',rename['group']))
    metric=Metric('Observed value','sum("'+rename['v']+'")')
    assert e.metric(metric)['rows'][0][0]==70
    planned=plan(e,profile,metric)
    assert [p['kind'] for p in planned]==['trend','ranked','histogram']
    assert SQLLab(e).execute(SQLLab(e).guided(metric))['rows'][0][0]==70
    w.transform('unknown',{'kind':'date','column':'z90','format':'%d/%m/%Y'})
    assert w.conversion_errors('unknown').empty


def test_no_example_dataset_controls_generic_defaults():
    root=Path(__file__).parents[1]/'src/insightpilot/universal'
    import re
    for path in root.glob('*.py'):
        assert not re.search(r'\b(?:is_canceled|adr|hotel|cfpb|complaint|sales|flight|tailnum)\b',path.read_text(),re.I),path


def test_negative_condition_and_null_chart_drill_identity(model):
    from insightpilot.universal import visuals
    assert Engine(model,'observations').metric(Metric('Negative threshold','count_if("v", ">", -1)'))['rows'][0][0]==3
    result=Engine(model,'observations').groups('outcome',Metric('Rows'))
    fig=visuals.ranked(result,'Outcome records','records','scope')
    assert any(row[0] is None for row in fig.data[0].customdata)
    assert 'Missing' in list(fig.data[0].y)


def test_approved_measure_overrides_identifier_name_in_exploration(model):
    from insightpilot.universal.ui import fields
    report=model.profile('observations')
    col=next(c for c in report['columns'] if c['column']=='v');col['inferred_type']='identifier'
    report['approved_roles']=model.info('observations')['roles']
    assert 'v' in fields(report)[0]


def test_distribution_axes_use_reviewed_field_units(model):
    from insightpilot.universal.visuals import histogram
    result=Engine(model,'observations').distribution('v')
    assert result['method']['units']=='USD'
    figure=histogram(result,'Observed values')
    assert figure.layout.xaxis.title.text=='Value · USD'  # bars span each bin, so the axis is the value scale
    assert figure.layout.yaxis.title.text=='Finite source rows'
