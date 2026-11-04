import json
from pathlib import Path
import pandas as pd
import pytest
import httpx

from insightpilot.universal import Workspace
from insightpilot.universal.assistant import answer
from insightpilot.universal.connectors import import_api, import_postgres
from insightpilot.universal.engine import Engine, Filter
from insightpilot.universal.metrics import Metric
from insightpilot.universal.report import configuration, replay
from insightpilot.universal.report import csv_export
from insightpilot.universal.advanced import outliers, time_anomalies
from insightpilot.universal import visuals
from insightpilot.universal.insights import discover
from insightpilot.universal.segmentation import cluster
from insightpilot.universal.statistics import categorical_association
from insightpilot.universal.relationships import suggest_relationships
from insightpilot.universal.investigation import proportion_change


@pytest.fixture
def engine(tmp_path):
    w=Workspace.create(tmp_path/'ws'); p=tmp_path/'records.csv'
    p.write_text('id,date,amount,status\n001,2025-01-01,10,Yes\n002,2025-01-02,20,No\n003,2025-01-03,bad,Unknown\n004,2025-01-04,,\n')
    w.add_file(p,'records'); return Engine(w,'records')


def test_conditional_rates_use_explicit_known_denominators(engine):
    metric=Metric('Known Yes share','100*count_if("status","==","Yes")/(count_if("status","==","Yes")+count_if("status","==","No"))')
    assert engine.metric(metric)['rows'][0][0]==50
    assert engine.metric(Metric('Delay-like rate','100*count_if("amount",">",15)/valid("amount")'))['rows'][0][0]==50
    with pytest.raises(ValueError): engine.metric(Metric('Bad','count_if("amount", ">", "bad")'))


def test_saved_manifest_replays_previous_version_and_rejects_other_source(engine):
    metric=Metric('Amount','sum("amount")'); config=configuration(engine,metric)
    config['filters']=[{'column':'status','operator':'eq','value':'Yes'}]
    engine.workspace.transform('records',{'kind':'rename','column':'amount','new_name':'revenue'})
    restored,m=replay(engine.workspace,config)
    assert restored.metric(m)['rows'][0][0]==10
    assert engine.workspace.columns('records')[-2]=='revenue'
    config['source']['sha256']='not-the-source'
    with pytest.raises(ValueError,match='hash'): replay(engine.workspace,config)


def test_explicit_missing_markers_and_date_parts(engine):
    w=engine.workspace
    p=w.directory/'parts.csv'; p.write_text('year,month,day,value\n2024,2,29,NA\n2023,2,29,NULL\n')
    w.add_file(p,'parts')
    profile=w.profile('parts')
    assert profile['columns'][-1]['missing']==0
    assert profile['columns'][-1]['potential_missing_markers']==2
    w.transform('parts',{'kind':'nullify_many','columns':['value'],'markers':['NA']})
    assert w.profile('parts')['columns'][-1]['missing']==1
    version=w.transform('parts',{'kind':'date_parts','year':'year','month':'month','day':'day','new_name':'date'})
    assert version['errors']==1
    assert w.conversion_errors('parts').original_value.tolist()==['2023-02-29']


def test_network_and_llm_are_opt_in(engine,monkeypatch):
    def forbidden(*args,**kwargs): raise AssertionError('No network call authorized')
    monkeypatch.setattr(httpx,'Client',forbidden)
    assert answer(engine,'How many records?',Metric('Rows'))['evidence']['rows'][0][0]==4
    with pytest.raises(ValueError,match='consent'): answer(engine,'Summarize',Metric('Rows'),use_provider=True)
    monkeypatch.delenv('INSIGHTPILOT_POSTGRES_DSN',raising=False)
    with pytest.raises(ValueError,match='securely'): import_postgres(engine.workspace,'public','source','source')


@pytest.mark.parametrize('plan',[{'analysis':'summary'},{'analysis':'summary','sql':'DROP TABLE records'},
    {'analysis':'read_file'},{'analysis':'summary','shell':'cat /etc/passwd'}])
def test_provider_can_only_select_validated_plans(engine,monkeypatch,plan):
    monkeypatch.setenv('INSIGHTPILOT_LLM_API_KEY','synthetic-test-key')
    monkeypatch.setenv('INSIGHTPILOT_LLM_MODEL','mock-model')
    real_client=httpx.Client
    def respond(request):
        content=request.content.decode()
        assert 'PRIVATE-SOURCE-BYTES' not in content
        assert '"001"' not in content and '"Unknown"' not in content and '"2025-01-01"' not in content
        assert 'retained' not in content and 'source_sha256' not in content
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(plan)}}],
                                       'usage':{'total_tokens':12}})
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:real_client(transport=httpx.MockTransport(respond)))
    if set(plan)=={'analysis'} and plan['analysis']=='summary':
        result=answer(engine,'Summarize selected metric',Metric('Rows'),use_provider=True,consent=True)
        assert result['evidence']['rows'][0][0]==4 and result['usage']['total_tokens']==12
    else:
        with pytest.raises(ValueError,match='unsupported'): answer(engine,'Summarize',Metric('Rows'),use_provider=True,consent=True)


@pytest.mark.parametrize('mode',['success','rate_limit','nested','never_ends'])
def test_api_pagination_limits_and_atomicity(engine,monkeypatch,mode):
    real_client=httpx.Client; requests=[]
    def respond(request):
        requests.append(request)
        page=int(request.url.params['page'])
        if mode=='rate_limit': return httpx.Response(429)
        if mode=='nested': return httpx.Response(200,json=[{'nested':{'x':1}}])
        if mode=='never_ends' or page<3: return httpx.Response(200,json=[{'id':f'{page:03}','value':page*10}])
        return httpx.Response(200,json=[])
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:real_client(transport=httpx.MockTransport(respond)))
    monkeypatch.setattr('insightpilot.universal.connectors.time.sleep',lambda seconds:None)
    if mode=='success':
        info=import_api(engine.workspace,'https://example.test/data','api',page_size=1,max_pages=3)
        assert info['rows']==2 and len(requests)==3
        assert info['source']['pagination']['terminal_empty_page']
        assert len(info['source']['raw_pages'])==3
        first=engine.workspace.directory/info['source']['raw_pages'][0]['retained']
        assert json.loads(first.read_bytes())==[{'id':'001','value':10}]
        assert Engine(engine.workspace,'api').records()['rows'][0][1]=='001'
    else:
        with pytest.raises(ValueError): import_api(engine.workspace,'https://example.test/data','api',max_pages=3)
        assert 'api' not in engine.workspace.tables()


@pytest.mark.parametrize('url',['http://example.test','https://user:pass@example.test/data','https://example.test/data?token=secret','file:///etc/passwd'])
def test_api_rejects_unsafe_url_shapes(engine,url):
    with pytest.raises(ValueError,match='HTTPS'): import_api(engine.workspace,url,'api')


def test_outlier_records_and_chart_gaps_are_traceable(engine):
    p=engine.workspace.directory/'outliers.csv'
    pd.DataFrame({'value':list(range(20))+[1000]}).to_csv(p,index=False)
    engine.workspace.add_file(p,'outliers')
    result=outliers(Engine(engine.workspace,'outliers'),'value')
    assert result['evidence']['rows'][0][0]==21 and result['evidence']['rows'][0][1]==1000
    trend={'columns':['period','value'],'rows':[('2025-01-01',1),('2025-03-01',3)],'method':{'time_grain':'month'}}
    figure=visuals.trend(trend,'Missing calendar periods','records')
    assert len(figure.data[0].x)==3 and pd.isna(figure.data[0].y[1]) and figure.data[0].connectgaps is False


def test_insight_changes_have_equal_windows_and_full_executed_evidence(engine):
    p=engine.workspace.directory/'changes.csv'
    dates=pd.date_range('2025-01-01','2025-03-01',freq='D')
    pd.DataFrame({'date':dates.strftime('%Y-%m-%d'),'amount':[1 if d.month==1 else 2 for d in dates]}).to_csv(p,index=False)
    engine.workspace.add_file(p,'changes')
    results=discover(Engine(engine.workspace,'changes'),metric=Metric('Amount','sum("amount")'),date_column='date')
    change=next(f for f in results if '28-day windows' in f['finding'])
    assert change['magnitude']==1
    assert change['evidence']['earlier']['rows'][0][:2]==(28,28)
    assert change['evidence']['later']['rows'][0][:2]==(56,28)
    empty=Engine(engine.workspace,'records',[Filter('id','eq','not present')])
    assert discover(empty)==[]


def test_key_suggestions_do_not_treat_mirror_row_numbers_as_business_keys(engine):
    p=engine.workspace.directory/'related.csv'; p.write_text('id,segment,rownames\n001,A,1\n002,B,2\n003,A,3\n004,B,4\n')
    engine.workspace.add_file(p,'related')
    suggestions=suggest_relationships(engine.workspace)
    assert any(s['left_key']=='id' and s['right_key']=='id' for s in suggestions)
    assert all(s['left_key']!='rownames' for s in suggestions)


def test_categorical_association_checks_small_expected_cells(engine):
    p=engine.workspace.directory/'categories.csv'
    pd.DataFrame({'x':['A']*30+['B']*30,'y':['Yes']*24+['No']*6+['Yes']*6+['No']*24}).to_csv(p,index=False)
    engine.workspace.add_file(p,'categories')
    result=categorical_association(Engine(engine.workspace,'categories'),'x','y')
    assert result['cramers_v']==pytest.approx(.6)
    assert result['minimum_expected_count']==15 and result['n_complete']==60
    p=engine.workspace.directory/'sparse.csv'
    pd.DataFrame({'x':['A']*3+['B']*3,'y':['Yes']*3+['Yes','No','No']}).to_csv(p,index=False)
    engine.workspace.add_file(p,'sparse')
    small=categorical_association(Engine(engine.workspace,'sparse'),'x','y')
    assert small['method'].startswith('Fisher exact')


def test_clustering_has_validation_and_source_row_assignments(engine):
    import numpy as np
    rng=np.random.default_rng(7)
    points=np.vstack([rng.normal(-10,.1,(30,2)),rng.normal(10,.1,(30,2))])
    p=engine.workspace.directory/'cluster.csv'
    pd.DataFrame(points,columns=['x','y']).to_csv(p,index=False)
    engine.workspace.add_file(p,'clusters')
    result=cluster(Engine(engine.workspace,'clusters'),['x','y'],2)
    assert result['silhouette']>.98 and result['complete_rows']==60
    assert result['data']._row_id.tolist()==list(range(1,61))
    assert sorted(result['data'].groupby('cluster').size().tolist())==[30,30]
    with pytest.raises(ValueError): cluster(Engine(engine.workspace,'clusters'),['x'],2)


def test_cluster_constant_features_are_rejected(engine):
    p=engine.workspace.directory/'constant.csv'
    pd.DataFrame({'a':[1]*30,'b':list(range(30))}).to_csv(p,index=False)
    engine.workspace.add_file(p,'constant')
    with pytest.raises(ValueError,match='Constant'): cluster(Engine(engine.workspace,'constant'),['a','b'],2)


def test_rate_mix_identity_against_independent_reference(engine):
    p=engine.workspace.directory/'rates.csv'
    p.write_text('date,segment,positive,exposure\n2025-01-01,A,10,100\n2025-01-01,B,30,100\n2025-02-01,A,20,100\n2025-02-01,B,80,200\n')
    engine.workspace.add_file(p,'rates'); e=Engine(engine.workspace,'rates')
    num=Metric('Positive','sum("positive")'); den=Metric('Exposure','sum("exposure")')
    result=proportion_change(e,'segment','date',num,den,('2025-01-01','2025-01-01'),('2025-02-01','2025-02-01'))
    assert result['rate_earlier']==.2 and result['rate_later']==pytest.approx(1/3)
    assert result['mix_contribution']==pytest.approx(1/30)
    assert result['rate_contribution']==pytest.approx(.1)
    assert abs(result['reconciliation_residual'])<1e-15
    assert Metric('Known','count_if("status","==","Yes") + count_if("status","==","No")').additive
    assert not Metric('Adjusted','count()+1').additive


def test_ambiguous_average_is_clarified_instead_of_returning_a_count(engine):
    with pytest.raises(ValueError,match='arithmetic mean'): answer(engine,'Average sales?',Metric('Rows'))


def test_chart_data_and_csv_exports_preserve_plotted_values_and_escape_formulas():
    import plotly.graph_objects as go
    fig=go.Figure(go.Bar(x=[10,20],y=['A','B']))
    data=visuals.plotted_data(fig)
    assert data.x.tolist()==[10,20] and data.y.tolist()==['A','B']
    box=go.Figure(go.Box(x=['Group'],q1=[1],median=[2],q3=[3],lowerfence=[0],upperfence=[4]))
    assert visuals.plotted_data(box)['median'].tolist()==[2]
    heat=go.Figure(go.Heatmap(x=['X','Y'],y=['A'],z=[[2,3]]))
    assert visuals.plotted_data(heat).value.tolist()==[2,3]
    result={'columns':['=unsafe','number'],'rows':[('=HYPERLINK("bad")',-5)]}
    exported=csv_export(result)
    assert "'=unsafe" in exported and "'=HYPERLINK" in exported
    assert ',-5' in exported


def test_locale_grouping_and_utc_dates_are_explicit_not_silent_coercion(engine):
    p=engine.workspace.directory/'locale.csv'
    pd.DataFrame({'amount':['1,200.5','1,23','.5'],
        'when':['2025-01-01T23:30:00-03:00','2025-01-02T00:30:00+03:00','invalid']}).to_csv(p,index=False)
    engine.workspace.add_file(p,'locale')
    numeric=engine.workspace.transform('locale',{'kind':'numeric','column':'amount','thousands':','})
    assert numeric['errors']==1
    assert engine.workspace.conversion_errors('locale').original_value.tolist()==['1,23']
    engine.workspace.transform('locale',{'kind':'date','column':'when'})
    data=Engine(engine.workspace,'locale').records()['rows']
    assert str(data[0][2])=='2025-01-02' and str(data[1][2])=='2025-01-01' and data[2][2] is None
    assert data[0][1]==1200.5 and data[2][1]==.5


def test_malformed_provider_protocol_is_rejected_without_execution(engine,monkeypatch):
    monkeypatch.setenv('INSIGHTPILOT_LLM_API_KEY','synthetic-test-key')
    monkeypatch.setenv('INSIGHTPILOT_LLM_MODEL','mock-model')
    real_client=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:real_client(transport=httpx.MockTransport(
        lambda request:httpx.Response(200,json={'choices':[]}))))
    with pytest.raises(ValueError,match='response format'):
        answer(engine,'Summarize',Metric('Rows'),use_provider=True,consent=True)
