"""Chart correctness and insight layers: values stay exact, decorations never alter plotted data."""
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from insightpilot.universal import Workspace, visuals
from insightpilot.universal.engine import Engine
from insightpilot.universal.metrics import Metric
from insightpilot.universal.ui import missing_date_basis
from insightpilot.universal.ui_workflow import card_description, saved_when


@pytest.fixture
def engine(tmp_path):
    w=Workspace.create(tmp_path/'ws');p=tmp_path/'orders.csv'
    rows=['day,region,amount']
    for i in range(60):
        day=f'2025-{1+i//20:02d}-{1+i%20:02d}'
        rows.append(f'{day},{"" if i%7==0 else ["North","South","East"][i%3]},{(i%13)*3.5+1}')
    p.write_text('\n'.join(rows)+'\n');w.add_file(p,'orders')
    w.approve_semantics('orders',{'day':'date','region':'geography','amount':'revenue'},'One order',
                        units={'amount':'EUR'},date_purposes={'day':'Order placed'},date_basis='day')
    return Engine(w,'orders')


def test_histogram_bars_span_their_bins_instead_of_centering_on_the_lower_edge(engine):
    result=engine.distribution('amount',10);figure=visuals.histogram(result,'Amount')
    bar=figure.data[0];starts=[r[0] for r in result['rows']];width=(starts[-1]-starts[0])/10
    assert list(bar.x)==starts and list(bar.y)==[r[1] for r in result['rows']]
    # Every ordinary bin is drawn from its lower boundary to lower+width.
    assert all(o==0 for o in bar.offset[:-1]) and all(w==pytest.approx(width) for w in bar.width[:-1])
    # The engine's separate exact-maximum boundary bin is a narrow marker centred on the maximum.
    assert bar.offset[-1]==pytest.approx(-bar.width[-1]/2) and starts[-1]==max(float(v) for v in pd.read_csv(engine.workspace.directory.parent/'orders.csv').amount)
    reference,_=np.histogram(pd.read_csv(engine.workspace.directory.parent/'orders.csv').amount,bins=10)
    assert sum(bar.y)==reference.sum()


def test_histogram_reference_lines_use_exact_full_data_statistics(engine):
    summary=engine.numeric_summary('amount');stats=dict(zip(summary['columns'],summary['rows'][0]))
    figure=visuals.histogram(engine.distribution('amount'),'Amount',summary=summary)
    lines=[s.x0 for s in figure.layout.shapes if s.type=='line']
    assert stats['median'] in lines and stats['mean'] in lines
    band=[s for s in figure.layout.shapes if s.type=='rect'][0]
    assert (band.x0,band.x1)==(stats['q1'],stats['q3'])
    assert len(figure.data)==1, 'Reference lines are layout shapes; exported plotted values are unchanged.'


def test_contribution_bridge_reconciles_totals_and_labels_missing_segments(engine):
    metric=Metric('Total','sum("amount")',units='EUR')
    result=engine.compare_periods('region',metric,'day',['2025-01-01','2025-01-20'],['2025-02-01','2025-02-20'])
    figure=visuals.contribution(result,'Change',metric.units);trace=figure.data[0]
    assert list(trace.x)[0]=='Earlier period' and list(trace.x)[-1]=='Later period'
    assert 'Missing' in trace.x and 'nan' not in [str(x).casefold() for x in trace.x]
    earlier,later=sum(r[1] for r in result['rows']),sum(r[2] for r in result['rows'])
    assert trace.y[0]==pytest.approx(earlier) and trace.y[-1]==pytest.approx(later)
    assert trace.y[0]+sum(trace.y[1:-1])==pytest.approx(later)


def test_contribution_bridge_includes_segments_beyond_the_display_limit():
    rows=[(f'S{i}',10.0,10.0+i,float(i),None) for i in range(25)]
    result={'columns':['category','earlier','later','change','relative_change'],'rows':rows}
    trace=visuals.contribution(result,'Change','units').data[0]
    assert 'Other 5 segments' in trace.x and trace.y[0]+sum(trace.y[1:-1])==pytest.approx(trace.y[-1])


def test_contribution_without_complete_totals_falls_back_to_changes_only():
    result={'columns':['category','earlier','later','change','relative_change'],'rows':[(None,None,5.0,None,None),('A',1.0,3.0,2.0,2.0)]}
    trace=visuals.contribution(result,'Change','units').data[0]
    assert list(trace.x)==['Missing','A'] and set(trace.measure)=={'relative'}


def test_ranked_bars_keep_values_selection_data_and_label_missing(engine):
    result=engine.groups('region',Metric('Records'));figure=visuals.ranked(result,'Records by region','records','scope')
    trace=figure.data[0]
    assert list(trace.x)==[r[1] for r in result['rows']] and 'Missing' in trace.y
    assert all(len(c)==2 and c[1]=='scope' for c in trace.customdata) and any(c[0] is None for c in trace.customdata)
    assert all(t for t in trace.text)


def test_trend_marks_partial_edge_periods_without_altering_observed_values():
    result={'columns':['period','value','records','first_observed','last_observed'],'method':{'time_grain':'month'},
            'rows':[('2025-01-01',5,2,'2025-01-15','2025-01-31'),('2025-02-01',7,3,'2025-02-01','2025-02-28'),
                    ('2025-04-01',6,3,'2025-04-01','2025-04-30'),('2025-05-01',9,1,'2025-05-01','2025-05-10')]}
    figure=visuals.trend(result,'Trend','units');trace=figure.data[0]
    assert len(trace.x)==5 and pd.isna(trace.y[2]) and trace.connectgaps is False
    assert list(trace.y[:2])==[5,7] and trace.y[-1]==9
    assert list(trace.marker.symbol)==['circle-open','circle','circle','circle','circle-open']


def test_trend_moving_average_never_bridges_a_missing_period():
    periods=pd.date_range('2025-01-01',periods=9,freq='MS')
    rows=[(p.date().isoformat(),float(i),1) for i,p in enumerate(periods) if i!=4]
    figure=visuals.trend({'columns':['period','value','records'],'rows':rows,'method':{'time_grain':'month'}},'Trend','units')
    average=figure.data[1]
    assert average.name=='3-period moving average'
    # Windows touching the missing fifth month (positions 4-6) are undefined.
    assert all(pd.isna(average.y[i]) for i in (4,5,6)) and average.y[3]==pytest.approx(2.0)


@pytest.mark.parametrize('value,expected',[(10211.4,'10.2k'),(9999,'9,999'),(1234567,'1.23M'),(-1945.2,'-1,945'),(.5,'0.5'),(None,'n/a'),(float('nan'),'n/a')])
def test_compact_annotation_numbers(value,expected):
    assert visuals.compact(value)==expected


@pytest.mark.parametrize('value',[None,float('nan'),np.nan])
def test_missing_category_label(value):
    assert visuals.category_label(value)=='Missing'


def test_time_guidance_distinguishes_unreviewed_dates_from_absent_dates():
    unreviewed={'columns':[{'column':'order_date','recognized_dates':40},{'column':'amount','recognized_dates':0}]}
    assert 'order_date' in missing_date_basis(unreviewed) and 'date basis' in missing_date_basis(unreviewed)
    assert missing_date_basis({'columns':[{'column':'amount','recognized_dates':0}]}).startswith('No recognized date field')


def test_saved_project_cards_use_readable_time_and_do_not_mislabel_saved_projects():
    stamp=datetime(2026,11,6,19,39,0,688507,tzinfo=timezone.utc).isoformat()
    assert '2026' in saved_when(stamp) and 'T19' not in saved_when(stamp) and '.688507' not in saved_when(stamp)
    assert saved_when(None)=='unknown · preserved legacy project'
    assert card_description({'purpose':'','description':''},{'legacy':False})!='Preserved existing project'
    assert card_description({'purpose':'','description':''},{'legacy':True})=='Preserved existing project'
    assert card_description({'purpose':'Quarterly review','description':''},{'legacy':False})=='Quarterly review'
