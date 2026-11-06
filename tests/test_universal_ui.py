from pathlib import Path
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from insightpilot.universal import Workspace
from insightpilot.universal.metrics import Metric, save_metric
from insightpilot.universal.ui import PAGES

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
    w.approve_semantics('orders',{'order_id':'identifier','date':'date','amount':'measure','quantity':'quantity','region':'category','customer':'customer','description':'text'},'One synthetic order',date_purposes={'date':'order observation'},date_basis='date')
    save_metric(w,'orders',Metric('Total amount','sum("amount")','Sum of uploaded amount','user units'))
    from insightpilot.universal.projects import ProjectSession
    root=tmp_path/'project-catalog'
    session=ProjectSession.start(root,w.directory)
    monkeypatch.setenv('INSIGHTPILOT_WORKSPACE_ROOT',str(root))
    monkeypatch.setenv('INSIGHTPILOT_UNIVERSAL_WORKSPACE',str(session.directory))
    return session.workspace


def open_page(page):
    app=AppTest.from_file(APP).run(timeout=30)
    app.radio(key='universal-page').set_value(page).run(timeout=30)
    return app


@pytest.mark.parametrize('page',PAGES)
def test_all_universal_workspaces_render(app_workspace,page):
    app=open_page(page)
    assert not app.exception and not app.error
    if page!='Data Hub': assert any('SYNTHETIC DATA' in w.value for w in app.warning)
    if page=='Overview': assert any(m.label=='Selected rows' and m.value=='12' for m in app.metric)


def test_shared_filters_reset_and_metric_definition(app_workspace):
    app=open_page('Overview')
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
    app=open_page('Explore')
    next(s for s in app.selectbox if s.label=='What would you like to understand?').select(view).run(timeout=30)
    assert not app.exception and not app.error


def test_analyst_computes_evidence_and_reports_downloads(app_workspace):
    app=open_page('AI Analyst')
    next(b for b in app.button if b.label=='Calculate evidence-backed answer').click().run(timeout=30)
    assert not app.exception and not app.error
    assert any('12 selected rows' in m.value for m in app.markdown)
    app.radio(key='universal-page').set_value('Reports').run(timeout=30)
    next(b for b in app.button if b.label=='Build report from this selection').click().run(timeout=30)
    assert not app.exception and not app.error
    assert len(app.get('download_button'))>=4


def test_advanced_statistics_execute_in_ui(app_workspace):
    app=open_page('Advanced')
    next(b for b in app.button if b.label=='Run selected analysis').click().run(timeout=30)
    assert not app.exception and not app.error
    assert any('Independent observations' in i.value for i in app.info)


def test_multiple_tables_have_unique_join_preview_column_names(app_workspace,tmp_path):
    p=tmp_path/'customers.csv'; p.write_text('customer,segment\nA,One\nB,Two\n')
    app_workspace.add_file(p,'customers')
    app=open_page('Data Hub')
    next(r for r in app.radio if r.label=='Data Hub view').set_value('Dataset library').run(timeout=30)
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
        app=open_page('AI Analyst')
        next(w for w in app.checkbox if w.label=='Use optional external model for planning').check().run(timeout=30)
        next(w for w in app.checkbox if w.label.startswith('I authorize sending')).check().run(timeout=30)
        next(b for b in app.button if b.label=='Calculate evidence-backed answer').click().run(timeout=30)
    assert not app.exception and not app.error
    assert any('HTTP 503' in w.value for w in app.warning)
    assert app_workspace.info('orders')['rows']==12


def test_saved_view_restores_metric_and_filters_and_reset_clears_them(app_workspace):
    app=open_page('Overview')
    next(s for s in app.selectbox if s.label=='Analysis metric').set_value(1).run(timeout=30)
    next(s for s in app.selectbox if s.label=='Category filter 1').select('region').run(timeout=30)
    next(s for s in app.selectbox if s.label=='Value 1').select('East').run(timeout=30)
    next(t for t in app.text_input if t.label=='View name').set_value('Reviewed East')
    next(b for b in app.button if b.label=='Save view and layout').click().run(timeout=30)
    assert app_workspace.saved_objects('view')['Reviewed East']['analysis']['filters'][0]['value']=='East'
    next(s for s in app.selectbox if s.label=='Value 1').select('West').run(timeout=30)
    next(s for s in app.selectbox if s.label=='Saved view').select('Reviewed East').run(timeout=30)
    next(b for b in app.button if b.label=='Restore saved view').click().run(timeout=30)
    assert not app.exception and not app.error
    assert any(m.label=='Total amount' and m.value=='21' for m in app.metric)
    next(b for b in app.button if b.label=='Reset analysis filters').click().run(timeout=30)
    assert not app.exception and not app.error
    assert any(m.label=='Total amount' and m.value=='78' for m in app.metric)


def test_report_artifacts_survive_reruns_and_old_scope_is_withheld(app_workspace):
    app=open_page('Reports')
    next(b for b in app.button if b.label=='Build report from this selection').click().run(timeout=30)
    assert len(app.get('download_button'))>=4
    app.run(timeout=30)
    assert len(app.get('download_button'))>=4
    next(s for s in app.selectbox if s.label=='Category filter 1').select('region').run(timeout=30)
    assert not any(d.label=='Download standalone HTML report' for d in app.get('download_button'))
    assert not app.exception and not app.error


def test_reviewed_metric_and_explicit_date_intent_survive_new_browser_session(app_workspace):
    app=open_page('Overview')
    assert any(m.label=='Total amount' and m.value=='78' for m in app.metric)
    next(s for s in app.selectbox if s.label=='Comparison field').select('region').run(timeout=30)
    next(s for s in app.selectbox if s.label=='Calendar grain').select('day').run(timeout=30)
    fresh=AppTest.from_file(APP).run(timeout=30)
    assert fresh.radio(key='universal-page').value=='Overview'
    fresh.radio(key='universal-page').set_value('Overview').run(timeout=30)
    assert not fresh.exception and not fresh.error
    assert next(s for s in fresh.selectbox if s.label=='Comparison field').value=='region'
    assert next(s for s in fresh.selectbox if s.label=='Calendar grain').value=='day'
    assert any(m.label=='Total amount' and m.value=='78' for m in fresh.metric)


def test_obsolete_metric_is_disclosed_without_blocking_quality_editor(app_workspace):
    app_workspace.transform('orders',{'kind':'rename','column':'amount','new_name':'other'})
    app=open_page('Quality & Preparation')
    assert not app.exception and not app.error
    assert any('Saved definitions need review' in w.value for w in app.warning)
    assert any(t.label=='Search the column catalog' for t in app.text_input)
