"""Expansion and routing must never mutate analytical selections or saved work."""
from streamlit.testing.v1 import AppTest
from test_universal_ui import app_workspace, open_page, APP


def widget(app,kind,label):
    return next(w for w in getattr(app,kind) if w.label==label)


def test_home_uses_the_requested_heading(app_workspace):
    app=open_page('Workspace Home')
    assert not app.exception
    html=' '.join(m.value for m in app.markdown)
    assert '<h1>Data Analysis</h1>' in html
    assert 'Good questions. Clear evidence.' not in html


def test_expansion_is_consumed_once_without_touching_filters(app_workspace,monkeypatch):
    app=open_page('Explore')
    widget(app,'selectbox','Category filter 1').select('region').run(timeout=30)
    widget(app,'selectbox','Value 1').select('East').run(timeout=30)
    before=app_workspace.info('orders')
    monkeypatch.setattr('insightpilot.universal.command_navigation.command_hub',lambda **kwargs:{'action':'toggle','sequence':1234})
    app.run(timeout=30)
    assert app.session_state['_hub-expanded'] is True
    assert app.radio(key='universal-page').value=='Explore'
    assert widget(app,'selectbox','Value 1').value=='East'
    app.run(timeout=30)
    assert app.session_state['_hub-expanded'] is True
    assert app_workspace.info('orders')['current']==before['current']
    assert app_workspace.info('orders')['source']==before['source']


def test_wheel_route_collapses_presentation_and_preserves_selection(app_workspace,monkeypatch):
    app=open_page('Quality & Preparation')
    widget(app,'selectbox','Category filter 1').select('region').run(timeout=30)
    widget(app,'selectbox','Value 1').select('East').run(timeout=30)
    monkeypatch.setattr('insightpilot.universal.command_navigation.command_hub',lambda **kwargs:{'page':'Explore','sequence':4567})
    app.run(timeout=30)
    assert app.radio(key='universal-page').value=='Explore'
    assert app.session_state['_hub-expanded'] is False
    assert widget(app,'selectbox','Value 1').value=='East'
    assert not app.exception and not app.error
