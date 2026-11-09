"""BI Studio through the actual Streamlit app: model, DAX measures, report page, slicers."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from insightpilot.universal import Workspace
from insightpilot.universal.projects import ProjectSession
from insightpilot.universal.ui_workflow import PAGES

APP=str(Path(__file__).parents[1]/'app.py')


@pytest.fixture
def studio(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    source=Workspace.create(tmp_path/'data/universal/source');p=tmp_path/'SYNTHETIC-sales.csv'
    p.write_text('day,region,amount\n2025-01-03,North,10\n2025-01-09,South,30\n2025-02-02,North,20\n2025-02-11,,5\n2025-03-05,South,40\n')
    source.add_file(p,'sales')
    source.approve_semantics('sales',{'day':'date','region':'geography','amount':'revenue'},'One sale',units={'amount':'EUR'},
                             date_purposes={'day':'Sale date'},date_basis='day')
    root=tmp_path/'catalog';session=ProjectSession.start(root,source.directory)
    monkeypatch.setenv('INSIGHTPILOT_WORKSPACE_ROOT',str(root));monkeypatch.setenv('INSIGHTPILOT_UNIVERSAL_WORKSPACE',str(session.directory))
    app=AppTest.from_file(APP).run(timeout=30)
    app.radio(key='universal-page').set_value('BI Studio').run(timeout=30)
    return app,session


def by_label(items,label):return next(w for w in items if w.label==label)


def ok(app):
    assert not app.exception,[e.value for e in app.exception]
    return app


def test_bi_studio_is_an_eleventh_destination_with_the_others_intact():
    assert PAGES[-1]=='BI Studio' and len(PAGES)==11 and len(set(PAGES))==11
    wheel=(Path(__file__).parents[1]/'src/insightpilot/universal/command_hub/index.html').read_text()
    assert all(f"'{page}'" in wheel for page in PAGES) and 'STEP=360/COUNT' in wheel


def test_measure_report_page_visuals_and_slicer(studio):
    app,session=studio;ok(app)
    assert any('Model it. Measure it. Report it.' in m.value for m in app.markdown)
    by_label(app.text_input,'Measure name').set_value('Total Sales')
    by_label(app.text_area,'DAX expression').set_value('SUM(sales[amount])')
    by_label(app.button,'Check measure').click().run(timeout=30);ok(app)
    assert any(m.value=='105' for m in app.metric)
    by_label(app.button,'Save measure').click().run(timeout=30);ok(app)
    by_label(app.text_input,'Measure name').set_value('North Share')
    by_label(app.text_area,'DAX expression').set_value('DIVIDE(CALCULATE([Total Sales], sales[region] = "North"), [Total Sales])')
    by_label(app.button,'Save measure').click().run(timeout=30);ok(app)
    state=session.workspace.saved_objects('layout')['bi.model:sales']
    assert [m['name'] for m in state['measures']]==['Total Sales','North Share']
    # Report page with a card and a bar chart.
    by_label(app.button,'Create page').click().run(timeout=30);ok(app)
    by_label(app.selectbox,'Visual type').set_value('Card').run(timeout=30)
    by_label(app.button,'Add to page').click().run(timeout=30);ok(app)
    by_label(app.selectbox,'Visual type').set_value('Bar').run(timeout=30)
    by_label(app.button,'Add to page').click().run(timeout=30);ok(app)
    assert any(m.value=='105' for m in app.metric) and len(app.get('plotly_chart'))==1
    # Slicer on region filters the card.
    by_label(app.multiselect,'Slicer fields').set_value(['sales\x1fregion']).run(timeout=30);ok(app)
    by_label(app.multiselect,'sales[region]').set_value(['North']).run(timeout=30);ok(app)
    assert any(m.value=='30' for m in app.metric)
    pages=session.workspace.saved_objects('layout')['bi.model:sales']['pages']
    assert [v['type'] for v in pages['Page 1']['visuals']]==['Card','Bar'] and pages['Page 1']['slicers']==['sales\x1fregion']


def test_invalid_dax_shows_guidance_without_saving(studio):
    app,session=studio
    by_label(app.text_input,'Measure name').set_value('Broken')
    by_label(app.text_area,'DAX expression').set_value('SUM(sales[missing])')
    by_label(app.button,'Save measure').click().run(timeout=30);ok(app)
    assert any('does not exist' in e.value for e in app.error)
    assert 'bi.model:sales' not in session.workspace.saved_objects('layout')


def test_calculated_column_and_model_tab(studio):
    app,session=studio
    by_label(app.text_input,'New column name').set_value('Size')
    by_label(app.text_area,'Row expression').set_value('IF(sales[amount] > 15, "Large", "Small")')
    by_label(app.button,'Add column').click().run(timeout=30);ok(app)
    assert session.workspace.saved_objects('layout')['bi.model:sales']['columns']==[{'name':'Size','expression':'IF(sales[amount] > 15, "Large", "Small")'}]
    assert len(app.get('graphviz_chart'))==1


def test_two_visuals_with_the_same_data_on_one_page(studio):
    app,session=studio
    by_label(app.text_input,'Measure name').set_value('Total Sales')
    by_label(app.text_area,'DAX expression').set_value('SUM(sales[amount])')
    by_label(app.button,'Save measure').click().run(timeout=30);ok(app)
    by_label(app.button,'Create page').click().run(timeout=30);ok(app)
    for kind in ('Line','Donut'):
        by_label(app.selectbox,'Visual type').set_value(kind).run(timeout=30)
        by_label(app.button,'Add to page').click().run(timeout=30);ok(app)
    visuals=session.workspace.saved_objects('layout')['bi.model:sales']['pages']['Page 1']['visuals']
    assert [v['type'] for v in visuals]==['Line','Donut'] and visuals[0]['rows']==visuals[1]['rows']
    assert len(app.get('plotly_chart'))==2
    assert sum(b.label=='Download evidence JSON' for b in app.get('download_button'))==2
