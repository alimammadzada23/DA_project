"""Independent references and failure cases for the general analytics workflow."""
from datetime import date
import json
import math
from pathlib import Path
import sqlite3

import duckdb
import pandas as pd
import pytest
from scipy import stats

from insightpilot.universal import Workspace
from insightpilot.universal.advanced import cohorts, forecast, funnel, outliers, rfm, text_terms, time_analysis
from insightpilot.universal.connectors import database_tables, import_database
from insightpilot.universal.engine import Engine, Filter, frame_of
from insightpilot.universal.metrics import Metric, load_metrics, save_metric
from insightpilot.universal.relationships import inspect_relationship, join_preview, related_groups, save_relationship
from insightpilot.universal.report import excel_export, html_report
from insightpilot.universal.statistics import holm_adjust, mean_interval, regression, welch_test


@pytest.fixture
def workspace(tmp_path):
    return Workspace.create(tmp_path/'workspace')


def add(workspace,tmp_path,frame,name='records'):
    path=tmp_path/(name+'.csv'); frame.to_csv(path,index=False)
    workspace.add_file(path,name)
    return Engine(workspace,name)


@pytest.fixture
def orders(workspace,tmp_path):
    return add(workspace,tmp_path,pd.DataFrame({
        'order_id':['001','002','003','004','005','006'],
        'date':['2025-01-01','2025-01-02','2025-01-03','2025-02-01','2025-02-02','2025-02-03'],
        'customer':['A','A','B','A','B','C'], 'region':['East','East','West','East','West','West'],
        'amount':[10,20,30,15,30,45],'quantity':[1,2,3,1,2,3]}),'orders')


@pytest.mark.parametrize('suffix',['csv','tsv','json','jsonl','parquet','xlsx'])
def test_essential_formats_preserve_source(workspace,tmp_path,suffix):
    frame=pd.DataFrame({'id':['001','002'],'name':['Hello','World'],'number':[1,2]})
    path=tmp_path/('input.'+suffix)
    if suffix in ('csv','tsv'): frame.to_csv(path,index=False,sep='\t' if suffix=='tsv' else ',')
    elif suffix in ('json','jsonl'): frame.to_json(path,orient='records',lines=suffix=='jsonl')
    elif suffix=='parquet': frame.to_parquet(path,index=False)
    else: frame.to_excel(path,index=False)
    before=path.read_bytes(); info=workspace.add_file(path,'sample')
    assert info['rows']==2
    assert frame_of(Engine(workspace,'sample').records())['id'].tolist()==['001','002']
    assert path.read_bytes()==before
    assert (workspace.directory/info['source']['retained']).read_bytes()==before


@pytest.mark.parametrize('content', ['a,a\n1,2\n','a,A\n1,2\n','a,b\n1,2,3\n','a,b\n','_row_id,a\n1,x\n'])
def test_invalid_csv_is_atomic_and_receipted(workspace,tmp_path,content):
    p=tmp_path/'bad.csv'; p.write_text(content)
    with pytest.raises((ValueError,duckdb.Error)): workspace.add_file(p,'bad')
    assert workspace.tables()=={}
    assert list((workspace.directory/'sources').glob('*.rejected.json'))


def test_nested_json_rejected(workspace,tmp_path):
    p=tmp_path/'bad.json'; p.write_text('[{"name":"a","nested":{"x":1}}]')
    with pytest.raises(ValueError,match='nested'): workspace.add_file(p,'bad')


def test_profile_is_uncertain_and_preserves_identifiers(orders):
    p=orders.workspace.profile('orders'); cols={c['column']:c for c in p['columns']}
    assert cols['order_id']['inferred_type']=='identifier'
    assert cols['order_id']['statistics'] is None
    assert any(c['confidence']=='ambiguous' for c in cols['amount']['candidates'])
    assert cols['amount']['statistics']['mean']==25
    assert p['exact_duplicate_rows']==0
    assert orders.info['roles']=={}


def test_reversible_preparation_and_error_rows(workspace,tmp_path):
    engine=add(workspace,tmp_path,pd.DataFrame({'value':['1,200.5','bad','2.0',None],'date':['01/02/2025','bad','03/02/2025',None]}))
    original=engine.info['current']
    version=workspace.transform('records',{'kind':'numeric','column':'value','thousands':','})
    assert version['errors']==1
    assert workspace.conversion_errors('records').to_dict('records')==[{'_row_id':2,'original_value':'bad'}]
    assert Engine(workspace,'records').metric(Metric('Amount','sum("value")'))['rows'][0][0]==1202.5
    workspace.transform('records',{'kind':'date','column':'date','format':'%d/%m/%Y'})
    assert str(frame_of(Engine(workspace,'records').records()).date.iloc[0])=='2025-02-01'
    workspace.activate('records',original)
    assert frame_of(Engine(workspace,'records').records()).value.iloc[0]=='1,200.5'
    assert len(workspace.info('records')['versions'])==3


def test_deduplicate_rename_and_select_keep_source(workspace,tmp_path):
    engine=add(workspace,tmp_path,pd.DataFrame({'a':[1,1,2],'b':['x','x','y']}))
    original=engine.info['current']
    workspace.transform('records',{'kind':'deduplicate'})
    assert Engine(workspace,'records').count()==2
    workspace.transform('records',{'kind':'rename','column':'a','new_name':'amount'})
    workspace.transform('records',{'kind':'select','columns':['amount']})
    assert workspace.columns('records')==['amount']
    workspace.activate('records',original)
    assert Engine(workspace,'records').count()==3


@pytest.mark.parametrize('formula',['__import__("os")','sum("amount");DROP TABLE records','sum(amount)','sum("missing")',
    'sum("order_id")','avg("order_id")','(lambda: 5)()','sum("amount").real','count() ** 100','42','open("/etc/passwd")'])
def test_metric_expressions_reject_unsafe_or_invalid_inputs(orders,formula):
    with pytest.raises(ValueError): Metric('Bad',formula).compile(orders.info['columns'])


def test_weighted_ratio_not_average_of_ratios_and_zero_denominator(orders):
    metric=Metric('Unit amount','sum("amount") / sum("quantity")',units='user currency/unit')
    assert orders.metric(metric)['rows'][0][0]==12.5  # 150/12, independently constructed
    assert orders.metric(Metric('Undefined','count() / (count()-count())'))['rows'][0][0] is None
    save_metric(orders.workspace,'orders',metric)
    assert load_metrics(orders.workspace,'orders')==[metric]


def test_shared_filters_group_and_record_drilldown(orders):
    e=Engine(orders.workspace,'orders',[Filter('region','eq','East'),Filter('date','from','2025-01-02')])
    assert e.count()==2
    assert e.metric(Metric('Amount','sum("amount")'))['rows'][0][0]==35
    assert [r[0] for r in e.records()['rows']]==[2,4]
    assert e.groups('region')['rows']==[('East',2,2)]
    assert all(item['source_sha256']==orders.info['source']['sha256'] for item in [e.records(),e.groups('region')])
    assert Engine(orders.workspace,'orders',[Filter('region','eq',"x' OR 1=1 --")]).count()==0


def test_additive_contribution_reconciles_and_rejects_mismatched_windows(orders):
    metric=Metric('Amount','sum("amount")')
    result=orders.compare_periods('region',metric,'date',('2025-01-01','2025-01-03'),('2025-02-01','2025-02-03'))
    assert sum(r[3] for r in result['rows'])==30
    with pytest.raises(ValueError): orders.compare_periods('region',metric,'date',('2025-01-01','2025-01-03'),('2025-02-01','2025-02-04'))
    with pytest.raises(ValueError): orders.compare_periods('region',Metric('Average','avg("amount")'),'date',('2025-01-01','2025-01-03'),('2025-02-01','2025-02-03'))


def test_relationship_coverage_and_safe_relational_metric(orders,tmp_path):
    w=orders.workspace
    add(w,tmp_path,pd.DataFrame({'customer':['A','B'],'segment':['Gold','Silver']}),'customers')
    rel=inspect_relationship(w,'orders','customer','customers','customer')
    assert rel['cardinality']=='many-to-one' and rel['left_unmatched_rows']==1
    assert rel['left_join_rows']==6 and rel['inner_join_rows']==5
    save_relationship(w,'customer_orders',rel)
    assert len(join_preview(w,rel))==6
    evidence=related_groups(orders,rel,'segment',Metric('Amount','sum("amount")'))
    assert sum(r[1] for r in evidence['rows'])==150
    w.transform('customers',{'kind':'trim','column':'segment'})
    with pytest.raises(ValueError,match='Revalidate'): related_groups(orders,rel,'segment',Metric('Records'))


def test_many_to_many_multiplication_requires_acknowledgment(orders,tmp_path):
    w=orders.workspace
    add(w,tmp_path,pd.DataFrame({'customer':['A','A','B'],'segment':['X','Y','Z']}),'duplicates')
    rel=inspect_relationship(w,'orders','customer','duplicates','customer')
    assert rel['cardinality']=='many-to-many' and rel['left_join_rows']>6
    with pytest.raises(ValueError,match='multiplies'): save_relationship(w,'bad',rel)
    save_relationship(w,'explicit',rel,allow_multiplication=True)
    with pytest.raises(ValueError,match='double counting'): related_groups(orders,rel,'segment',Metric('Rows'))


@pytest.mark.parametrize('kind',['sqlite','duckdb'])
def test_read_only_database_connectors(workspace,tmp_path,kind):
    p=tmp_path/('source.'+kind)
    con=sqlite3.connect(p) if kind=='sqlite' else duckdb.connect(str(p))
    con.execute('CREATE TABLE source (id VARCHAR, amount DOUBLE)')
    con.execute("INSERT INTO source VALUES ('001',10),('002',20)")
    if kind=='sqlite': con.commit()
    con.close(); original=p.read_bytes()
    assert database_tables(p)==['source']
    import_database(workspace,p,'source','imported')
    assert Engine(workspace,'imported').metric(Metric('Amount','sum("amount")'))['rows'][0][0]==30
    assert p.read_bytes()==original


def test_statistics_against_independent_reference(workspace,tmp_path):
    x=[1,2,3,4,5,6,7,8]; y=[2,4,6,8,10,12,14,16]
    e=add(workspace,tmp_path,pd.DataFrame({'x':x,'y':y,'group':['A']*4+['B']*4}))
    interval=mean_interval(e,'x')
    half=2.364624251*math.sqrt(6)/math.sqrt(8)
    assert interval['mean']==4.5
    assert interval['lower']==pytest.approx(4.5-half,rel=1e-8)
    test=welch_test(e,'x','group','A','B')
    assert test['mean_difference']==-4
    assert test['p_value']==pytest.approx(stats.ttest_ind(x[:4],x[4:],equal_var=False).pvalue)
    fit=regression(e,'x','y')
    assert fit['slope']==2 and fit['intercept']==0 and fit['r_squared']==1
    assert holm_adjust([.01,.04,.03])==pytest.approx([.03,.06,.06])


def test_zero_variance_small_samples_and_bad_p_values(orders):
    with pytest.raises(ValueError): mean_interval(Engine(orders.workspace,'orders',[Filter('order_id','eq','001')]),'amount')
    with pytest.raises(ValueError): holm_adjust([1.1])
    with pytest.raises(ValueError): forecast(orders,'date',Metric('Rows'))
    with pytest.raises(ValueError): outliers(orders,'amount')


def test_time_gaps_are_not_zero_or_previous_period(workspace,tmp_path):
    e=add(workspace,tmp_path,pd.DataFrame({'date':['2025-01-01','2025-03-01','2025-04-01'],'amount':[10,30,40]}))
    data=time_analysis(e,'date',Metric('Amount','sum("amount")'))['data']
    assert len(data)==4 and pd.isna(data.value.iloc[1])
    assert pd.isna(data.absolute_change.iloc[2])


def test_forecast_has_real_holdout_and_conservative_edges(workspace,tmp_path):
    months=pd.date_range('2020-01-01',periods=50,freq='MS')
    e=add(workspace,tmp_path,pd.DataFrame({'date':months.strftime('%Y-%m-%d'),'amount':[10+(i%12) for i in range(50)]}))
    result=forecast(e,'date',Metric('Amount','sum("amount")'),3)
    assert result['model']=='seasonal_naive' and result['holdout_mae']['seasonal_naive']==0
    assert result['holdout_months']==12 and len(result['data'])==3


def test_cohorts_include_observed_zeros_but_not_censored_cells(workspace,tmp_path):
    e=add(workspace,tmp_path,pd.DataFrame({'customer':['A','A','B','C'],'date':['2025-01-01','2025-03-01','2025-01-02','2025-03-02']}))
    data=cohorts(e,'customer','date','2025-04-01')['data']
    jan=data[data.cohort==pd.Timestamp('2025-01-01')]
    assert jan.retention.tolist()==[1,0,.5]
    march=data[data.cohort==pd.Timestamp('2025-03-01')]
    assert march.month_offset.tolist()==[0]


def test_funnel_order_and_window_censoring(workspace,tmp_path):
    e=add(workspace,tmp_path,pd.DataFrame({'customer':['A','A','B','B','C'],
        'event':['start','buy','buy','start','start'],
        'time':['2025-01-01T10:00:00Z','2025-01-02T10:00:00Z','2025-01-01T10:00:00Z','2025-01-02T10:00:00Z','2025-01-09T10:00:00Z']}))
    result=funnel(e,'customer','event','time',['start','buy'],3,'2025-01-10')
    assert result['data'].entities.tolist()==[2,1]
    assert result['data'].median_hours_from_start.iloc[1]==24
    assert result['censored_starts']==1


def test_rfm_requires_revenue_confirmation_and_uses_distinct_orders(orders):
    with pytest.raises(ValueError,match='revenue'): rfm(orders,'customer','order_id','date','amount','2025-02-04')
    orders.workspace.approve_roles('orders',{'amount':'revenue'},'One line per distinct order in this fixture')
    result=rfm(Engine(orders.workspace,'orders'),'customer','order_id','date','amount','2025-02-04')
    a=result['data'].set_index('entity').loc['A']
    assert a.transaction_frequency==3 and a.monetary_value==45 and a.recency_days==3


def test_text_and_safe_report_exports(workspace,tmp_path):
    e=add(workspace,tmp_path,pd.DataFrame({'text':['<script>alert(1)</script>','payment delayed payment','=HYPERLINK("bad")']}))
    assert dict(text_terms(e,'text')['terms'])['payment']==2
    report=html_report(e,Metric('Rows'),evidence=[e.records()],title='<script>bad</script>')
    assert '<script>bad</script>' not in report and '&lt;script&gt;bad' in report
    result=e.records(); data=excel_export(result)
    output=tmp_path/'result.xlsx'; output.write_bytes(data)
    from openpyxl import load_workbook
    assert load_workbook(output).active.cell(4,2).data_type=='s'
