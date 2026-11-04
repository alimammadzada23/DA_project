from pathlib import Path
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from insightpilot.universal import Workspace
from insightpilot.universal.metrics import Metric, save_metric
from insightpilot.universal.ui import PAGES
from insightpilot.workspace_ui import WORKSPACES

APP=str(Path(__file__).parents[1]/'app.py')


@pytest.fixture
def app_workspace(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('INSIGHTPILOT_DATABASE',raising=False)
    w=Workspace.create(tmp_path/'universal')
    p=tmp_path/'SYNTHETIC-orders.csv'
    pd.DataFrame({'order_id':[f'{i:04}' for i in range(12)],
        'date':['2025-01-01']*6+['2025-02-01']*6,'amount':list(range(1,13)),
        'quantity':[1]*12,'region':['East']*6+['West']*6,
        'customer':['A','B']*6,'description':['delayed package']*12}).to_csv(p,index=False)
    w.add_file(p,'orders',provenance={'origin':'synthetic'})
    save_metric(w,'orders',Metric('Total amount','sum("amount")','Sum of uploaded amount','user units'))
    monkeypatch.setenv('INSIGHTPILOT_UNIVERSAL_WORKSPACE',str(w.directory))
    return w


def open_page(page):
    app=AppTest.from_file(APP).run(timeout=30)
    app.selectbox(key='dataset_workspace').select(WORKSPACES[4]).run(timeout=30)
    app.radio(key='universal-page').set_value(page).run(timeout=30)
    return app


@pytest.mark.parametrize('page',PAGES)
def test_all_eight_universal_workspaces_render(app_workspace,page):
    app=open_page(page)
    assert not app.exception and not app.error
    if page!='Data Hub': assert any('SYNTHETIC DATA' in w.value for w in app.warning)
    if page=='Executive Intelligence': assert any(m.label=='Selected rows' and m.value=='12' for m in app.metric)


def test_shared_filters_reset_and_metric_definition(app_workspace):
    app=open_page('Executive Intelligence')
    metric=next(s for s in app.selectbox if s.label=='Analysis metric')
    metric.set_value(1).run(timeout=30)
    assert any(m.label=='Total amount' and m.value=='78' for m in app.metric)
    next(s for s in app.selectbox if s.label=='Category filter 1').select('region').run(timeout=30)
    next(s for s in app.selectbox if s.label=='Value 1').select('East').run(timeout=30)
    assert any(m.label=='Selected rows' and m.value=='6' for m in app.metric)
    assert any(m.label=='Total amount' and m.value=='21' for m in app.metric)
    next(b for b in app.button if b.label=='Reset analysis filters').click().run(timeout=30)
    assert any(m.label=='Selected rows' and m.value=='12' for m in app.metric)


@pytest.mark.parametrize('view',['Ranked groups','Numeric distribution','Numeric relationship','Calendar trend',
    'Distribution by group','Two-dimensional counts','Records'])
def test_all_explorer_modes_render(app_workspace,view):
    app=open_page('Visual Explorer')
    next(s for s in app.selectbox if s.label=='What would you like to understand?').select(view).run(timeout=30)
    assert not app.exception and not app.error


def test_analyst_computes_evidence_and_reports_downloads(app_workspace):
    app=open_page('AI Business Analyst')
    next(b for b in app.button if b.label=='Calculate evidence-backed answer').click().run(timeout=30)
    assert not app.exception and not app.error
    assert any('12 selected rows' in m.value for m in app.markdown)
    app.radio(key='universal-page').set_value('Reports and Exports').run(timeout=30)
    next(b for b in app.button if b.label=='Build report from this selection').click().run(timeout=30)
    assert not app.exception and not app.error
    assert len(app.get('download_button'))>=4


def test_advanced_statistics_execute_in_ui(app_workspace):
    app=open_page('Advanced Analytics')
    next(b for b in app.button if b.label=='Run selected analysis').click().run(timeout=30)
    assert not app.exception and not app.error
    assert any('Independent observations' in i.value for i in app.info)


def test_multiple_tables_have_unique_join_preview_column_names(app_workspace,tmp_path):
    p=tmp_path/'customers.csv'; p.write_text('customer,segment\nA,One\nB,Two\n')
    app_workspace.add_file(p,'customers')
    app=open_page('Data Hub')
    assert not app.exception and not app.error


@pytest.mark.parametrize('surface',['api','provider'])
def test_unavailable_external_services_show_safe_messages(app_workspace,monkeypatch,surface):
    import httpx
    def unavailable(*args,**kwargs):
        request=httpx.Request('GET','https://example.test/source')
        raise httpx.HTTPStatusError('Synthetic service unavailable',request=request,response=httpx.Response(503,request=request))
    if surface=='api':
        monkeypatch.setattr('insightpilot.universal.ui_data.import_api',unavailable)
        app=open_page('Data Hub')
        next(w for w in app.text_input if w.label=='HTTPS API base URL').set_value('https://example.test/source')
        next(w for w in app.checkbox if 'verified this API' in w.label).check()
        next(b for b in app.button if b.label=='Fetch configured API').click().run(timeout=30)
    else:
        monkeypatch.setattr('insightpilot.universal.assistant.provider_plan',unavailable)
        app=open_page('AI Business Analyst')
        next(w for w in app.checkbox if w.label=='Use optional external model for planning').check().run(timeout=30)
        next(w for w in app.checkbox if w.label.startswith('I authorize sending')).check().run(timeout=30)
        next(b for b in app.button if b.label=='Calculate evidence-backed answer').click().run(timeout=30)
    assert not app.exception and not app.error
    assert any('HTTP 503' in w.value for w in app.warning)
    assert app_workspace.info('orders')['rows']==12
