"""Workflow/retained-evidence regressions. No actual Qwen or native-Mac claim."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from insightpilot.universal import Workspace
from insightpilot.universal.engine import Engine,Filter
from insightpilot.universal.metrics import Metric
from insightpilot.universal.visuals import ranked
from insightpilot.universal.report import configuration
from insightpilot.universal.ui_deliverables import business_report,report_definition,sections_from_investigation
from insightpilot.universal.ui_workflow import PRIMARY,quality_findings,project_summary
from insightpilot.universal.workspace_labels import catalog,metadata
from test_universal_ui import app_workspace,open_page,APP
from test_descriptive_analysis import unreviewed


def widget(app,kind,label):return next(w for w in getattr(app,kind) if w.label==label)


def saved_blocks(workspace):
    e=Engine(workspace,'orders');m=Metric('Records')
    first={'analysis':configuration(e,m),'evidence':e.groups('region',m)}
    first['figure']=ranked(first['evidence'],'Recorded rows by region','records').to_json()
    filtered=Engine(workspace,'orders',[Filter('region','eq','East')])
    second={'analysis':configuration(filtered,m),'evidence':filtered.records()}
    workspace.save_object('block','Full comparison',first)
    workspace.save_object('block','East source rows',second)
    return {'Full comparison':first,'East source rows':second}


def test_home_workflow_is_central_and_empty_state_leads_to_upload(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path);monkeypatch.delenv('INSIGHTPILOT_UNIVERSAL_WORKSPACE',raising=False)
    monkeypatch.setenv('INSIGHTPILOT_WORKSPACE_ROOT',str(tmp_path/'projects'))
    app=AppTest.from_file(APP).run(timeout=30)
    assert app.radio(key='universal-page').value=='Workspace Home'
    assert not any(r.key=='universal-page' for r in app.sidebar.radio)
    assert all(page in app.radio(key='universal-page').options for page in PRIMARY)
    widget(app,'button','Upload dataset').click().run(timeout=30)
    assert app.radio(key='universal-page').value=='Data Hub'
    assert any(w.label=='Dataset files' for w in app.get('file_uploader'))
    assert not app.exception and not app.error


def test_central_project_creation_preserves_the_original_workspace(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path);monkeypatch.delenv('INSIGHTPILOT_UNIVERSAL_WORKSPACE',raising=False)
    root=tmp_path/'projects';monkeypatch.setenv('INSIGHTPILOT_WORKSPACE_ROOT',str(root))
    from insightpilot.universal.projects import ProjectSession
    app=AppTest.from_file(APP).run(timeout=30)
    original=Path(app.session_state['universal-directory'])
    assert catalog(root)==[]
    widget(app,'button','New Project').click().run(timeout=30)
    assert original.parent==Path(app.session_state['universal-directory']).parent
    assert not catalog(root)
    widget(app,'button','Save Project').click().run(timeout=30)
    widget(app,'text_input','Project name').set_value('Supplier review')
    widget(app,'text_input','Business purpose (optional)').set_value('Investigate observed delivery differences.')
    widget(app,'button','Save Project').click().run(timeout=30)
    assert not app.exception and len(catalog(root))==1
    project=catalog(root)[0]
    assert not project.tables() and metadata(project)['purpose']=='Investigate observed delivery differences.'
    assert metadata(project)['description']==''


def test_home_and_investigation_round_trip_preserve_shared_filters(app_workspace):
    app=open_page('Explore')
    widget(app,'selectbox','Category filter 1').select('region').run(timeout=30)
    widget(app,'selectbox','Value 1').select('East').run(timeout=30)
    before=deepcopy(app_workspace.info('orders'))
    for page in ['Workspace Home','Investigate','Reports','Explore']:
        app.radio(key='universal-page').set_value(page).run(timeout=30)
        assert not app.exception and widget(app,'selectbox','Value 1').value=='East'
    assert app_workspace.info('orders')==before
    assert any('6 selected / 12' in t.value for t in app.text)


def test_data_hub_exposes_actual_preview_and_explicit_handoffs(app_workspace):
    app=open_page('Data Hub')
    widget(app,'radio','Data Hub view').set_value('Dataset library').run(timeout=30)
    assert not app.exception
    preview=next(frame.value for frame in app.dataframe if 'order_id' in frame.value.columns)
    assert len(preview)==10 and list(preview.amount.astype(float))==list(range(1,11))
    widget(app,'button','Explore data').click().run(timeout=30)
    assert app.radio(key='universal-page').value=='Explore'
    assert widget(app,'selectbox','Analysis table').value=='orders'


def test_quality_priorities_are_signals_and_never_approve_semantics(unreviewed,monkeypatch,tmp_path):
    workspace=unreviewed[0].workspace;before=deepcopy(workspace.info('facts'))
    review,optional,usable=quality_findings(unreviewed[1],before)
    assert any(x['Field']=='age' and x['Review']=='Missing values' for x in review)
    assert any(x['Field']=='marker' and x['Review']=='Special markers' for x in review)
    assert any(x['Field']=='entity_id' for x in optional)
    assert any(x['Field']=='campaign' for x in usable)
    monkeypatch.chdir(tmp_path);monkeypatch.setenv('INSIGHTPILOT_UNIVERSAL_WORKSPACE',str(workspace.directory))
    app=open_page('Quality & Preparation')
    after=workspace.info('facts')
    assert not app.exception and after['roles']==before['roles'] and after.get('semantics')==before.get('semantics')
    assert after['current']==before['current'] and after['source']==before['source']


def test_project_stage_is_based_on_saved_objects_not_fake_completion(app_workspace):
    before=project_summary(app_workspace)
    assert before['stage']=='Data imported' and before['reports']==0
    saved_blocks(app_workspace)
    assert project_summary(app_workspace)['stage']=='Evidence saved'
    assert 'percent' not in project_summary(app_workspace)


def test_report_definition_freezes_original_blocks_and_order(app_workspace):
    blocks=saved_blocks(app_workspace)
    draft=report_definition('Review',{'executive_summary':'Observed records only.'},['East source rows','Full comparison'],blocks)
    original=deepcopy(draft)
    blocks['East source rows']['evidence']['rows']=[]
    assert draft==original
    html=business_report(app_workspace,Engine(app_workspace,'orders'),Metric('Records'),draft)
    assert html.index('<h3>1. East source rows')<html.index('<h3>2. Full comparison')
    assert 'Observed records only.' in html and 'Each finding has its own dataset' in html
    assert app_workspace.info('orders')['source']['sha256'] in html
    assert 'Plotly.newPlot' in html and '<script src="http' not in html


def test_retained_report_reproduces_after_preparation_and_live_block_replacement(app_workspace):
    blocks=saved_blocks(app_workspace)
    draft=report_definition('Retained evidence',{},['Full comparison'],blocks)
    app_workspace.save_object('layout','ux.report:Retained evidence',draft)
    old_version=app_workspace.info('orders')['current']
    app_workspace.transform('orders',{'kind':'rename','column':'region','new_name':'market'})
    app_workspace.save_object('block','Full comparison',{'unrelated':'replacement must not affect draft'})
    fresh=Workspace(app_workspace.directory);stored=fresh.saved_objects('layout')['ux.report:Retained evidence']
    html=business_report(fresh,Engine(fresh,'orders'),Metric('Records'),stored)
    assert old_version in html and '<td>East</td>' in html and '<td>West</td>' in html
    assert fresh.info('orders')['current']!=old_version
    assert stored==json.loads(json.dumps(draft,default=str))


def test_report_appendix_uses_saved_scope_instead_of_unrelated_live_filters(app_workspace):
    draft=report_definition('Saved scope',{},['Full comparison'],saved_blocks(app_workspace))
    live=Engine(app_workspace,'orders',[Filter('region','eq','not-present')])
    assert live.count()==0
    html=business_report(app_workspace,live,Metric('Records'),draft)
    assert 'Records: <strong>12</strong>' in html
    assert 'Primary saved selection · calculation appendix' in html


@pytest.mark.parametrize('field,bad',[('version','unknown-version'),('source_sha256','not-the-source'),('table','other')])
def test_report_rejects_evidence_manifest_mismatch(app_workspace,field,bad):
    blocks=saved_blocks(app_workspace);blocks['Full comparison']['evidence'][field]=bad
    draft=report_definition('Invalid evidence',{},['Full comparison'],blocks)
    with pytest.raises(ValueError,match='retained source/version'):business_report(app_workspace,Engine(app_workspace,'orders'),Metric('Records'),draft)


def test_analyst_commentary_is_escaped_in_html_and_embedded_definition(app_workspace):
    blocks=saved_blocks(app_workspace);payload='</script><img src=x onerror=alert(1)>'
    draft=report_definition('Safe report',{'executive_summary':payload},['Full comparison'],blocks)
    html=business_report(app_workspace,Engine(app_workspace,'orders'),Metric('Records'),draft)
    assert '&lt;/script&gt;&lt;img' in html and '<img src=x' not in html
    embedded=html.split('id="insightpilot-report-definition">')[1].split('</script>')[0]
    assert '\\u003c' in embedded and json.loads(embedded)['sections']['executive_summary']==payload


def test_investigation_transfer_preserves_classification_without_generating_conclusions():
    brief={'question':'What changed?','observations':'Recorded source totals differ.','associations':'Two variables move together.',
           'hypotheses':'Check collection coverage.','actions':'Validate before deciding.','limitations':'Uncertain completeness.'}
    sections=sections_from_investigation(brief)
    assert 'executive_summary' not in sections
    assert sections['business_context']=='What changed?'
    assert 'Observed facts:\nRecorded source totals differ.' in sections['interpretation']
    assert 'Observed associations:\nTwo variables move together.' in sections['interpretation']
    assert 'Hypotheses to investigate:\nCheck collection coverage.' in sections['interpretation']


def test_invalid_evidence_rejects_before_any_new_summary_calculation(app_workspace,monkeypatch):
    blocks=saved_blocks(app_workspace);blocks['Full comparison']['evidence']['version']='other-version'
    draft=report_definition('Invalid evidence',{},['Full comparison'],blocks)
    monkeypatch.setattr(Engine,'metric',lambda *a,**k:pytest.fail('Invalid report calculated a summary'))
    with pytest.raises(ValueError,match='retained source/version'):business_report(app_workspace,Engine(app_workspace,'orders'),Metric('Records'),draft)


def test_report_preview_retains_the_existing_saved_object_size_bound(app_workspace):
    blocks=saved_blocks(app_workspace);blocks['Full comparison']['oversized']='x'*5_000_000
    with pytest.raises(ValueError,match='size limit'):report_definition('Too large',{},['Full comparison'],blocks)


@pytest.mark.parametrize('selection',[[],['missing'],['Full comparison']*2])
def test_report_selection_validation_does_not_substitute_evidence(app_workspace,selection):
    with pytest.raises(ValueError):report_definition('Review',{},selection,saved_blocks(app_workspace))


def test_investigation_notes_save_with_scope_without_inventing_findings(app_workspace):
    saved_blocks(app_workspace);app=open_page('Investigate')
    before=deepcopy(app_workspace.info('orders'))
    widget(app,'text_input','Investigation name').set_value('Observed selection')
    widget(app,'text_area','Business question').set_value('What differs in these recorded groups?')
    widget(app,'text_area','Observed facts').set_value('The source contains twelve synthetic rows.')
    widget(app,'text_area','Hypotheses to investigate').set_value('Check whether data collection differs between groups.')
    widget(app,'multiselect','Supporting saved findings').set_value(['Full comparison'])
    widget(app,'button','Save investigation').click().run(timeout=30)
    assert not app.exception
    brief=app_workspace.saved_objects('layout')['ux.investigation:Observed selection']
    assert brief['observations']=='The source contains twelve synthetic rows.'
    assert brief['associations']=='' and brief['actions']=='' and brief['evidence']==['Full comparison']
    assert brief['analysis']['version']==before['current'] and app_workspace.info('orders')==before


def test_business_report_ui_persists_draft_and_invalidates_changed_scope(app_workspace):
    saved_blocks(app_workspace);app=open_page('Reports')
    widget(app,'text_input','Business report title').set_value('Recorded evidence review')
    widget(app,'multiselect','Choose saved findings').set_value(['Full comparison','East source rows']).run(timeout=30)
    widget(app,'text_area','Executive summary').set_value('Two retained descriptions of synthetic rows.')
    widget(app,'number_input','Position · Full comparison').set_value(2)
    widget(app,'number_input','Position · East source rows').set_value(1)
    widget(app,'button','Save report draft').click().run(timeout=30)
    assert not app.exception
    stored=app_workspace.saved_objects('layout')['ux.report:Recorded evidence review']
    assert stored['evidence_order']==['East source rows','Full comparison']
    widget(app,'button','Preview & build report').click().run(timeout=30)
    assert not app.exception and any(w.label=='Download business report · HTML' for w in app.get('download_button'))
    widget(app,'selectbox','Category filter 1').select('region').run(timeout=30)
    assert not any(w.label=='Download business report · HTML' for w in app.get('download_button'))
    fresh=open_page('Reports')
    widget(fresh,'selectbox','Saved report draft').select('ux.report:Recorded evidence review').run(timeout=30)
    assert widget(fresh,'text_input','Business report title').value=='Recorded evidence review'
    assert widget(fresh,'text_area','Executive summary').value=='Two retained descriptions of synthetic rows.'
    assert not fresh.exception


def test_explicit_date_basis_review_is_applied_once_without_repeated_configuration(unreviewed,monkeypatch,tmp_path):
    workspace=unreviewed[0].workspace
    monkeypatch.chdir(tmp_path);monkeypatch.setenv('INSIGHTPILOT_UNIVERSAL_WORKSPACE',str(workspace.directory))
    app=open_page('Quality & Preparation')
    widget(app,'selectbox','Inspect field meaning and statistics').select('observed').run(timeout=30)
    widget(app,'selectbox','Field role').select('date')
    widget(app,'text_input','Timestamp purpose').set_value('Synthetic observation date, explicitly reviewed.')
    widget(app,'text_input','Reviewed row grain').set_value('One synthetic source observation.')
    widget(app,'checkbox','Use this field as the shared date basis').check()
    widget(app,'checkbox','I confirm these field definitions and row grain').check()
    widget(app,'button','Approve field definition').click().run(timeout=30)
    assert not app.exception and widget(app,'selectbox','Date basis').value=='observed'
    working=Workspace(app.session_state['universal-directory'])
    assert working.info('facts')['semantics']['date_basis']=='observed'
    assert working.info('facts')['roles']=={'observed':'date'}
    assert workspace.info('facts')['roles']!={'observed':'date'}


def test_quality_preparation_and_undo_round_trip_in_the_actual_widgets(app_workspace):
    app=open_page('Quality & Preparation');before=deepcopy(app_workspace.info('orders'))
    widget(app,'selectbox','Preparation operation').select('trim').run(timeout=30)
    widget(app,'selectbox','Preparation column').select('region').run(timeout=30)
    widget(app,'button','Apply preparation to a new version').click().run(timeout=30)
    assert not app.exception and len(app_workspace.info('orders')['versions'])==2
    widget(app,'selectbox','Version history / undo').set_value(0).run(timeout=30)
    assert widget(app,'selectbox','Version history / undo').value==0
    widget(app,'button','Activate selected version').click().run(timeout=30)
    assert not app.exception and app_workspace.info('orders')['current']==before['current']
    assert app_workspace.info('orders')['source']==before['source']


def test_one_observed_period_keeps_the_real_values_without_suggesting_a_trend(app_workspace):
    app=open_page('Explore')
    widget(app,'selectbox','Category filter 1').select('region').run(timeout=30)
    widget(app,'selectbox','Value 1').select('East').run(timeout=30)
    widget(app,'selectbox','What would you like to understand?').select('Calendar trend').run(timeout=30)
    assert not app.exception and not app.get('plotly_chart')
    assert any('Fewer than two observed periods' in item.value for item in app.info)
    result=next(frame.value for frame in app.dataframe if 'period' in frame.value.columns)
    assert len(result)==1 and result.value.iloc[0]==21 and result.records.iloc[0]==6


def test_home_recent_work_accepts_investigation_evidence_references(app_workspace):
    saved_blocks(app_workspace)
    app_workspace.save_object('layout','ux.investigation:Observed change',{
        'kind':'ux.investigation','title':'Observed change','question':'What changed?',
        'evidence':['Full comparison'],'updated_at':'2026-11-05T12:00:00+00:00'})
    app=open_page('Workspace Home')
    assert not app.exception and not app.error
    assert any('Recent work' in h.value for h in app.subheader)
