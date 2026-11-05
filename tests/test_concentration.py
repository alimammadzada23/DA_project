"""Independent numerical and safety checks for full-denominator concentration."""
import pytest
from insightpilot.universal.store import Workspace
from insightpilot.universal.engine import Engine, Filter
from insightpilot.universal.metrics import Metric
from insightpilot.universal.concentration import pareto
from insightpilot.universal.visuals import plotted_data
import plotly.graph_objects as go


def setup(tmp_path, amounts):
    w=Workspace.create(tmp_path/'project')
    p=tmp_path/'facts.csv'
    p.write_text('group,amount\n'+'\n'.join(f'{g},{v}' for g,v in amounts))
    w.add_file(p,'facts',provenance={'origin':'synthetic'})
    w.approve_semantics('facts',{'group':'category','amount':'measure'},'One test observation')
    return Engine(w,'facts')


def test_concentration_uses_full_denominator_and_keeps_evidence(tmp_path):
    e=setup(tmp_path,[('A',60),('B',20),('C',10),('D',10)])
    out=pareto(e,'group',Metric('Value','sum("amount")'))
    assert out['total']==100 and out['groups_to_threshold']==2
    assert out['data'].share.tolist()==[.6,.2,.1,.1]
    assert out['data'].cumulative_share.tolist()==[.6,.8,.9,1]
    assert out['evidence']['source_sha256']==e.info['source']['sha256']
    assert out['evidence']['columns'][-2:]==['share','cumulative_share']
    filtered=Engine(e.workspace,'facts',[Filter('group','ne','A')])
    assert pareto(filtered,'group',Metric('Records'))['total']==3


@pytest.mark.parametrize('amounts',[[('A',-1),('B',5)],[('A',0),('B',0)],[('A',''),('B',2)]])
def test_undefined_composition_is_rejected(tmp_path,amounts):
    e=setup(tmp_path,amounts)
    with pytest.raises(ValueError):pareto(e,'group',Metric('Value','sum("amount")'))


def test_rates_and_invalid_thresholds_are_rejected(tmp_path):
    e=setup(tmp_path,[('A',10),('B',20)])
    with pytest.raises(ValueError,match='additive'):pareto(e,'group',Metric('Mean','avg("amount")'))
    for threshold in [0,1.1,float('nan'),True]:
        with pytest.raises(ValueError):pareto(e,'group',Metric('Records'),threshold=threshold)


def test_many_groups_are_not_silently_top_n_and_nulls_are_retained(tmp_path):
    e=setup(tmp_path,[(f'G{i}',1) for i in range(120)]+[('',1)])
    out=pareto(e,'group',Metric('Records'))
    assert out['total']==121 and len(out['data'])==121
    assert sum(out['data'].share)==pytest.approx(1)
    assert out['groups_to_threshold']==97


def test_donut_export_contains_actual_plotted_values():
    table=plotted_data(go.Figure(go.Pie(labels=['A','B'],values=[60,40])))
    assert table[['category','value']].to_dict('records')==[{'category':'A','value':60},{'category':'B','value':40}]
