"""DAX workbench: every measure is checked against an independent pandas calculation."""
import numpy as np
import pandas as pd
import pytest

from insightpilot.universal import Workspace
from insightpilot.universal.dax import DAXError, Field, Model, evaluate, parse, slicer, check, distinct_values
from insightpilot.universal.engine import Engine, Filter


@pytest.fixture
def setup(tmp_path):
    rng=np.random.default_rng(3);rows=[]
    dates=pd.date_range('2024-01-01','2025-06-30',freq='D')
    for i in range(1200):
        rows.append({'order_id':f'O{i//2}','day':dates[rng.integers(len(dates))].date().isoformat(),
                     'region':'' if i%17==0 else ['North','South','East','West'][rng.integers(4)],
                     'qty':int(rng.integers(1,6)),'price':float(rng.choice([4.5,9.0,12.25])),})
    frame=pd.DataFrame(rows);frame['amount']=frame.qty*frame.price
    path=tmp_path/'sales.csv';frame.to_csv(path,index=False)
    w=Workspace.create(tmp_path/'ws');w.add_file(path,'sales')
    w.approve_semantics('sales',{'day':'date','region':'geography','amount':'revenue','qty':'quantity','price':'price','order_id':'transaction'},
                        'One order line',units={'amount':'EUR'},date_purposes={'day':'Order date'},date_basis='day')
    engine=Engine(w,'sales')
    frame['region']=frame.region.replace('',None);frame['day']=pd.to_datetime(frame.day)
    model=Model.from_engine(engine,measures=[
        {'name':'Total','expression':"SUM(Sales[amount])"},
        {'name':'Rows','expression':'COUNTROWS(Sales)'},
        {'name':'Orders','expression':'DISTINCTCOUNT(Sales[order_id])'},
        {'name':'Share','expression':'DIVIDE([Total], CALCULATE([Total], ALL(Sales[region])))'},
        {'name':'North','expression':'CALCULATE([Total], Sales[region] = "North")'},
        {'name':'Revenue X','expression':'SUMX(Sales, Sales[qty] * Sales[price])'},
        {'name':'Big lines','expression':'CALCULATE([Rows], FILTER(Sales, Sales[amount] > 40))'},
        {'name':'Last year','expression':"CALCULATE([Total], SAMEPERIODLASTYEAR('Date'[Date]))"},
        {'name':'YTD','expression':"TOTALYTD([Total], 'Date'[Date])"},
        {'name':'Prev month','expression':"CALCULATE([Total], PREVIOUSMONTH('Date'[Date]))"},
        {'name':'Rank','expression':'RANKX(ALL(Sales[region]), [Total])'},
        {'name':'Avg order','expression':'DIVIDE([Total], [Orders])'},
    ],calculated=[{'name':'Size','expression':'IF(Sales[amount] > 40, "Large", "Small")'}])
    return engine,model,frame


def table(result):
    return pd.DataFrame(result['rows'],columns=result['columns'])


def test_basic_aggregations_grand_total_and_blank_category(setup):
    engine,model,frame=setup
    result=evaluate(engine,model,['Total','Rows','Orders'],[Field('Sales','region')])
    data=table(result);data['key']=data['Sales[region]'].fillna('__blank__');data=data.set_index('key')
    expected=frame.groupby(frame.region.fillna('__blank__')).agg(total=('amount','sum'),rows=('amount','size'),orders=('order_id','nunique'))
    assert set(data.index)==set(expected.index)
    for region,row in expected.iterrows():
        got=data.loc[region]
        assert got['Total']==pytest.approx(row.total) and got['Rows']==row.rows and got['Orders']==row.orders
    assert result['total'][0]==pytest.approx(frame.amount.sum()) and result['total'][1]==len(frame)


def test_all_removes_the_row_filter_for_percent_of_total(setup):
    engine,model,frame=setup
    data=table(evaluate(engine,model,['Share'],[Field('Sales','region')]))
    assert data['Share'].sum()==pytest.approx(1.0)
    north=data[data['Sales[region]']=='North']['Share'].iloc[0]
    assert north==pytest.approx(frame[frame.region=='North'].amount.sum()/frame.amount.sum())


def test_calculate_boolean_filter_overrides_the_row_context(setup):
    engine,model,frame=setup
    data=table(evaluate(engine,model,['North'],[Field('Sales','region')]))
    assert set(data['North'].round(6))=={round(frame[frame.region=='North'].amount.sum(),6)}


def test_iterator_filter_and_ratio_measures(setup):
    engine,model,frame=setup
    result=evaluate(engine,model,['Revenue X','Big lines','Avg order'])
    revenue,big,average=result['rows'][0]
    assert revenue==pytest.approx((frame.qty*frame.price).sum()) and big==(frame.amount>40).sum()
    assert average==pytest.approx(frame.amount.sum()/frame.order_id.nunique())


def test_date_table_and_time_intelligence(setup):
    engine,model,frame=setup
    data=table(evaluate(engine,model,['Total','Last year','YTD','Prev month'],[Field('Date','Month')]))
    monthly=frame.groupby(frame.day.dt.strftime('%Y-%m')).amount.sum()
    row=data.set_index('Date[Month]')
    assert row.loc['2025-03','Total']==pytest.approx(monthly['2025-03'])
    assert row.loc['2025-03','Last year']==pytest.approx(monthly['2024-03'])
    assert row.loc['2025-03','YTD']==pytest.approx(monthly[['2025-01','2025-02','2025-03']].sum())
    assert row.loc['2025-03','Prev month']==pytest.approx(monthly['2025-02'])
    assert pd.isna(row.loc['2024-03','Last year'])


def test_last_year_without_a_date_row_shifts_the_visible_period(setup):
    engine,model,frame=setup
    # Card/region context: visible dates are the whole span; last year is that span moved back one year.
    first,last=frame.day.min(),frame.day.max()
    shifted=frame[(frame.day+pd.DateOffset(years=1)>=first)&(frame.day+pd.DateOffset(years=1)<=last)]
    total,last_year=evaluate(engine,model,['Total','Last year'])['rows'][0]
    assert last_year==pytest.approx(shifted.amount.sum()) and last_year!=pytest.approx(total)
    north=frame[frame.region=='North'];north_first,north_last=north.day.min(),north.day.max()
    data=table(evaluate(engine,model,['Last year'],[Field('Sales','region')])).set_index('Sales[region]')
    moved=north[(north.day+pd.DateOffset(years=1)>=north_first)&(north.day+pd.DateOffset(years=1)<=north_last)]
    assert data.loc['North','Last year']==pytest.approx(moved.amount.sum())


def test_rankx_ranks_regions_by_measure(setup):
    engine,model,frame=setup
    data=table(evaluate(engine,model,['Total','Rank'],[Field('Sales','region')]))
    ranked=data.sort_values('Total',ascending=False)['Rank'].tolist()
    assert ranked==list(range(1,len(ranked)+1))


def test_calculated_column_as_row_field_and_slicers(setup):
    engine,model,frame=setup
    data=table(evaluate(engine,model,['Rows'],[Field('Sales','Size')])).set_index('Sales[Size]')
    assert data.loc['Large','Rows']==(frame.amount>40).sum()
    north=slicer(model,'Sales','region',['North'])
    sliced=evaluate(engine,model,['Total','Share'],[Field('Sales','Size')],[north])
    assert sliced['total'][0]==pytest.approx(frame[frame.region=='North'].amount.sum())
    # ALL(region) removes the region slicer, exactly like Power BI.
    size=np.where(frame.amount>40,'Large','Small')
    shares=table(sliced).set_index('Sales[Size]')['Share']
    for label in ('Large','Small'):
        assert shares[label]==pytest.approx(frame[(frame.region=='North')&(size==label)].amount.sum()/frame[size==label].amount.sum())
    dated=slicer(model,'Date','Date',start='2025-01-01',end='2025-06-30')
    assert evaluate(engine,model,['Total'],[],[dated])['rows'][0][0]==pytest.approx(frame[frame.day>='2025-01-01'].amount.sum())


def test_shared_sidebar_filters_still_apply(setup):
    engine,model,frame=setup
    scoped=Engine(engine.workspace,'sales',[Filter('region','eq','South')])
    assert evaluate(scoped,model,['Total'])['rows'][0][0]==pytest.approx(frame[frame.region=='South'].amount.sum())


@pytest.mark.parametrize('expression,message',[
    ('SUM(Sales[nope])','does not exist'),('SUMX(Sales, [Total])','is a measure'),
    ('EVALUATE Sales','Unexpected'),('PYTHON("x")','not supported'),
    ("CALCULATE([Total], Sales[region] = \"N\" || Sales[qty] = 2)",'exactly one column'),
    ('[Missing]','does not exist'),('Sales[amount]','Aggregate it'),
    ("SUM(Other[amount])",'Unknown table'),('SAMEPERIODLASTYEAR(\'Date\'[Date])','Use it inside CALCULATE')])
def test_invalid_dax_is_rejected_with_guidance(setup,expression,message):
    engine,model,_=setup
    with pytest.raises(DAXError,match=message):check(engine,model,expression)


def test_self_reference_and_injection_attempts_are_rejected(setup):
    engine,model,frame=setup
    model.measures['Loop']='[Loop] + 1'
    with pytest.raises(DAXError,match='refers to itself'):evaluate(engine,model,['Loop'])
    # String literals are escaped, never interpreted as SQL.
    attack=check(engine,model,"CALCULATE([Rows], Sales[region] = \"x'); DROP TABLE sales; --\")")
    assert attack['rows'][0][0] is None and engine.workspace.info('sales')['rows']==len(frame)


def test_slicer_value_listing(setup):
    engine,model,_=setup
    values,truncated=distinct_values(engine,model,'Sales','region')
    assert set(values)>={'North','South','East','West'} and not truncated


def test_parser_handles_comments_and_quoted_tables():
    tree=parse("// revenue\nCALCULATE( SUM('Sales Data'[amount]) , 'Sales Data'[region] IN {\"A\",\"B\"} ) /* note */")
    assert tree[0]=='call' and tree[1]=='CALCULATE' and tree[2][1][0]=='in'
