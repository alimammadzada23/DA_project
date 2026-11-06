"""Held-out ordinary-user workflow. No app/runtime presets or mappings are installed.

The references below are evaluation assertions, not product configuration.
Semantic review and metric creation use visible UI controls only.
"""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import uuid

import pandas as pd
from playwright.sync_api import sync_playwright,expect

from browser_unified import app_server,select,ready,ROOT,fill
from insightpilot.universal import Workspace
from insightpilot.universal.engine import Engine,frame_of
from insightpilot.universal.report import replay


def review_field(page,field,role,unit,grain):
    select(page,'Inspect field meaning and statistics',field)
    page.wait_for_timeout(600)
    # Expanders retain open state across reruns. Inspect the actual <details>.
    detail=page.get_by_test_id('stExpander').filter(has=page.get_by_text('Review this field and row grain',exact=True))
    if not detail.locator('details').evaluate('(e)=>e.open'):detail.locator('summary').click()
    select(page,'Field role',role)
    page.get_by_role('textbox',name='Field units / currency',exact=True).fill(unit)
    page.get_by_role('textbox',name='Reviewed row grain',exact=True).fill(grain)
    checkbox=page.get_by_role('checkbox',name='I confirm these field definitions and row grain',exact=True)
    if not checkbox.is_checked(): checkbox.focus();checkbox.press('Space')
    page.get_by_role('button',name='Approve field definition',exact=True).click();ready(page)


def main():
    plan=json.loads((ROOT/'outputs/heldout-evaluation-plan.json').read_text());w=Workspace(plan['workspace'])
    if w.tables():
        w=Workspace.create(w.directory.parent/('workspace-'+uuid.uuid4().hex[:6]))
        plan['workspace']=str(w.directory)
    folder=w.directory.parent/('browser-'+w.directory.name);folder.mkdir(exist_ok=True)
    reference=pd.read_csv(ROOT/plan['original_csv'])
    grain='One joint age, alcohol and tobacco stratum, with aggregated sampled cases and controls; not one person.'
    errors=[];steps=[]
    with app_server(w.directory,folder/'server.log') as url,sync_playwright() as p:
        browser=p.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
        page=browser.new_page(viewport={'width':1512,'height':1050});page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(url);expect(page.get_by_role('button',name='Import tables',exact=True)).to_be_visible(timeout=60000)
        assert not w.tables(),'Use a fresh held-out workspace; never overwrite an earlier evaluation.'
        page.get_by_test_id('stFileUploader').first.locator('input[type=file]').set_input_files(plan['upload'])
        page.get_by_role('button',name='Import tables',exact=True).click()
        expect(page.get_by_text('unseen_rows: 88 rows imported.',exact=False)).to_be_visible(timeout=60000);ready(page)
        steps.append({'ordinary_browser_upload':True,'format':'JSONL','rows':88,'origin_label':'user_upload; authenticity not automatically asserted'})
        page.get_by_text('Quality & Preparation',exact=True).click();ready(page)
        for field,role,unit in [('rownames','identifier',''),('ncases','quantity','sampled people'),('ncontrols','quantity','sampled people'),('agegp','category','age band'),('alcgp','category','g/day band'),('tobgp','category','g/day band')]:
            print('review',field,flush=True);review_field(page,field,role,unit,grain)
        info=w.info('unseen_rows');assert info['semantics']['reviewed'] and info['roles']['ncases']=='quantity'
        assert info['semantics']['date_basis'] is None
        assert info['roles']=={'rownames':'identifier','ncases':'quantity','ncontrols':'quantity','agegp':'category','alcgp':'category','tobgp':'category'},info['roles']
        steps.append({'field_approvals_through_UI':True,'grain':grain,'date_basis':None,'no_date_guessed':True})
        page.get_by_text('Define a reusable metric',exact=True).click()
        definition='Sampled cases divided by sampled cases plus sampled controls. This is sample composition, not population incidence or causal risk.'
        metric_name='Sample case share'
        for label,value in [('Metric name',metric_name),('Aggregate formula','sum("ncases") / (sum("ncases") + sum("ncontrols"))'),
            ('Metric definition / interpretation',definition),('Units or currency','proportion (0–1)'),
            ('Known metric limitations','Case-control sampling fixes composition; no population incidence, individual risk, or causal alcohol effect is established.'),
            ('Eligible denominator / missing-value policy','Sum of observed sampled cases and controls; missing inputs excluded, zero denominator unavailable.')]:
            page.get_by_role('textbox',name=label,exact=True).fill(value)
        page.get_by_role('button',name='Validate and save metric',exact=True).click();ready(page)
        page.get_by_text('Overview',exact=True).click();ready(page)
        select(page,'Analysis metric',metric_name);select(page,'Comparison field','agegp')
        expect(page.get_by_test_id('stMetricValue').nth(1)).to_have_text('0.2051',timeout=60000)
        expect(page.locator('.js-plotly-plot')).to_have_count(2,timeout=60000)
        page.screenshot(path=str(folder/'overview.png'),full_page=True)
        steps.append({'overview_value':200/975,'numerator':200,'eligible_denominator':975,'charts':['ranked rate by age group','distribution of sampled stratum counts']})
        # Check each executed group against a separate Pandas calculation.
        from insightpilot.universal.metrics import load_metrics
        from insightpilot.universal.context import AnalysisContext
        metric=next(m for m in load_metrics(w,'unseen_rows') if m.name==metric_name)
        e=Engine(w,'unseen_rows',context=AnalysisContext(comparison='agegp'))
        actual=e.groups('agegp',metric)
        grouped=reference.groupby('agegp')[['ncases','ncontrols']].sum()
        for category,value,n in actual['rows']:
            r=grouped.loc[category];expected=r.ncases/(r.ncases+r.ncontrols)
            assert abs(value-expected)<1e-12
        steps.append({'independent_group_rates':len(actual['rows']),'reference':'Pandas sums over original CSV; no production parser or formula evaluator reused.'})
        page.get_by_text('Explore',exact=True).click();ready(page)
        graph=page.locator('[data-testid="stPlotlyChart"] .js-plotly-plot').first;plot=page.get_by_test_id('stPlotlyChart').first
        plot.evaluate("e=>e.scrollIntoView({block:'center'})");plot.hover()
        plot.get_by_role('button',name='Box Select',exact=True).click()
        page.wait_for_function("document.querySelector('[data-testid=stPlotlyChart] .js-plotly-plot')?.layout?.dragmode==='select'",timeout=10000)
        bar=graph.locator('.barlayer .point path').first;box=bar.bounding_box();endpoint=box['x']+box['width'];center=box['y']+box['height']/2
        page.mouse.move(endpoint-5,center-4);page.mouse.down();page.mouse.move(endpoint+5,center+4,steps=15);page.mouse.up()
        expect(page.get_by_text('Chart selection:',exact=False)).to_be_visible(timeout=60000);ready(page)
        page.screenshot(path=str(folder/'drill-through.png'),full_page=True);steps.append({'pointer_drill_through':True})
        page.get_by_text('SQL Lab',exact=True).click();ready(page)
        page.get_by_role('button',name='Run query',exact=True).click();ready(page)
        fill(page,'Report table block name','Held-out grouped rates')
        page.get_by_role('button',name='Save query result to Reports',exact=True).click();ready(page)
        expect(page.get_by_text('Saved query result and execution evidence.',exact=True)).to_be_visible(timeout=60000)
        block=w.saved_objects('block')['Held-out grouped rates']
        assert len(block['evidence']['rows'])==6
        page.get_by_role('button',name='Edit generated SQL',exact=True).click();ready(page)
        editor=page.frame_locator('iframe[title="streamlit_ace.streamlit_ace"]').locator('.ace_text-input')
        query='SELECT sum(try_cast(ncases AS DOUBLE)) / NULLIF(sum(try_cast(ncases AS DOUBLE))+sum(try_cast(ncontrols AS DOUBLE)),0) AS case_share FROM unseen_rows'
        editor.focus();editor.press('ControlOrMeta+a');page.keyboard.insert_text(query);page.wait_for_timeout(2000)
        page.get_by_role('button',name='Run query',exact=True).click();ready(page)
        fill(page,'Report table block name','Held-out aggregate rate')
        page.get_by_role('button',name='Save query result to Reports',exact=True).click();ready(page)
        expect(page.get_by_text('Saved query result and execution evidence.',exact=True)).to_be_visible(timeout=60000)
        aggregate=w.saved_objects('block')['Held-out aggregate rate']['evidence']
        assert abs(aggregate['rows'][0][0]-plan['expected'])<1e-12
        steps.append({'advanced_ace_sql_value':aggregate['rows'][0][0],'matches_overview_and_reference':True})
        page.screenshot(path=str(folder/'sql-lab.png'),full_page=True)
        page.get_by_text('Advanced',exact=True).click();ready(page)
        select(page,'Analytical method','Time-series changes')
        expect(page.get_by_text(re.compile('Time analysis needs a reviewed date basis|No recognized date field is available'))).to_be_visible(timeout=60000)
        steps.append({'inapplicable_time_analysis_gated':True})
        page.get_by_text('AI Analyst',exact=True).click()
        page.get_by_role('button',name='Calculate evidence-backed answer',exact=True).click();ready(page)
        expect(page.get_by_text('Deterministic guided analyst; no model or tokens used',exact=True)).to_be_visible(timeout=60000)
        steps.append({'zero_token_answer':True})
        page.get_by_text('Reports',exact=True).click()
        page.get_by_role('button',name='Build report from this selection',exact=True).click();ready(page)
        with page.expect_download(timeout=60000) as dl:page.get_by_role('button',name='Download standalone HTML report',exact=True).click()
        report=folder/'heldout-report.html';dl.value.save_as(str(report));html=report.read_text()
        assert 'population incidence' in html and '975.0' in html and '200.0' in html and w.info('unseen_rows')['source']['sha256'] in html
        manifest_box=page.get_by_role('button',name='Download reusable analysis manifest',exact=True)
        with page.expect_download(timeout=60000) as dl:manifest_box.click()
        saved=folder/'heldout-manifest.json';dl.value.save_as(str(saved))
        restored,m=replay(w,json.loads(saved.read_text()));assert abs(restored.metric(m)['rows'][0][0]-plan['expected'])<1e-12
        steps.append({'browser_report_download':True,'denominator_and_sampling_caveat_visible':True,'manifest_replay':True})
        page.set_viewport_size({'width':768,'height':1024});ready(page);page.screenshot(path=str(folder/'tablet.png'),full_page=True)
        widths=page.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth})');assert widths['scroll']<=widths['width']+2
        steps.append({'tablet_width':768,'horizontal_overflow':False})
        assert not errors,errors
        browser.close()
    receipt={'completed_at':datetime.now(timezone.utc).isoformat(),'source':plan['source_receipt'],'workspace':str(w.directory.relative_to(ROOT)),
        'upload_sha256':hashlib.sha256(Path(plan['upload']).read_bytes()).hexdigest(),'rows':88,'independent_counts':plan['independent_counts'],'value':plan['expected'],
        'generic_runtime_mapping_added':False,'ordinary_UI_semantic_review':True,'steps':steps,'javascript_errors':errors,'screenshots':str(folder.relative_to(ROOT)),'status':'passed'}
    (ROOT/'docs/unified-heldout-evaluation.json').write_text(json.dumps(receipt,indent=2)+'\n');print('held-out browser passed',flush=True)

if __name__=='__main__':main()
