"""Interaction regressions for the new workbench and routing event lifecycle."""
from streamlit.testing.v1 import AppTest
from test_universal_ui import app_workspace, open_page, APP


def field(app,kind,label):
    return next(w for w in getattr(app,kind) if w.label==label)


def test_composition_defaults_to_the_shared_category_and_saves_actual_evidence(app_workspace):
    app=open_page('Explore')
    field(app,'selectbox','Comparison field').select('region').run(timeout=30)
    field(app,'selectbox','What would you like to understand?').select('Composition & Pareto').run(timeout=30)
    assert not app.exception and not app.error
    assert field(app,'selectbox','Concentration grouping field').value=='region'
    assert field(app,'metric','Groups to reach 80%').value=='2'
    field(app,'button','Save chart to report').click().run(timeout=30)
    block=next(iter(app_workspace.saved_objects('block').values()))
    assert block['evidence']['columns'][-2:]==['share','cumulative_share']
    assert block['evidence']['source_sha256']==app_workspace.info('orders')['source']['sha256']


def test_period_comparison_survives_saving_its_chart(app_workspace):
    app=open_page('Investigate')
    field(app,'button','Calculate segment contributions').click().run(timeout=30)
    assert not app.exception and not app.error
    assert field(app,'metric','Earlier baseline').value=='21'
    assert field(app,'metric','Later observed value').value=='57'
    field(app,'button','Save chart to report').click().run(timeout=30)
    assert field(app,'metric','Absolute change').value=='36'
    assert app_workspace.saved_objects('block')


def test_consumed_component_event_never_overrides_native_navigation(app_workspace,monkeypatch):
    monkeypatch.setattr('insightpilot.universal.command_navigation.command_hub',lambda **kwargs:{'page':'Explore','sequence':42})
    app=AppTest.from_file(APP).run(timeout=30)
    assert app.radio(key='universal-page').value=='Explore'
    app.radio(key='universal-page').set_value('SQL Lab').run(timeout=30)
    assert app.radio(key='universal-page').value=='SQL Lab'
    assert not app.exception and not app.error
    assert app_workspace.info('orders')['rows']==12


def test_project_search_includes_independently_saved_business_purpose(app_workspace):
    from insightpilot.universal.workspace_labels import save_name
    from insightpilot.universal.projects import ProjectSession
    session=ProjectSession(app_workspace.directory.parent.parent,app_workspace.directory.name)
    session.save('Operations review','Dataset context',purpose='Understand delivery variation')
    app=open_page('Workspace Home')
    field(app,'text_input','Find a project').set_value('delivery variation').run(timeout=30)
    assert not app.exception and not app.error
    assert any(t.value=='Operations review' for t in app.text)
    assert not any('No project matches' in w.value for w in app.info)


def test_metric_template_executes_through_the_existing_safe_compiler(app_workspace):
    from insightpilot.universal.metrics import load_metrics
    from insightpilot.universal.engine import Engine
    app=open_page('Quality & Preparation')
    field(app,'selectbox','Metric starting point').select('Total value').run(timeout=30)
    field(app,'selectbox','Metric input field').select('amount').run(timeout=30)
    field(app,'text_input','Metric name').set_value('Reviewed total')
    field(app,'text_input','Metric definition / interpretation').set_value('Sum of the uploaded amount field in this synthetic fixture.')
    field(app,'text_input','Units or currency').set_value('user units')
    field(app,'button','Validate and save metric').click().run(timeout=30)
    assert not app.exception and not app.error
    definition=next(m for m in load_metrics(app_workspace,'orders') if m.name=='Reviewed total')
    assert definition.formula=='sum("amount")'
    result=Engine(app_workspace,'orders').metric(definition)
    assert result['rows'][0][0]==78
