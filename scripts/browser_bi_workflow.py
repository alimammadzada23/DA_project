"""Actual Chromium/Streamlit acceptance on two already-verified public datasets.

Only isolated workspaces under outputs are created. No Qwen/native-Mac claim,
downloads, private data, synthetic business evidence or external AI requests.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

import httpx
import pandas as pd
from playwright.sync_api import expect,sync_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from insightpilot.universal import Workspace
from insightpilot.universal.workspace_labels import catalog,metadata
from insightpilot.universal.relationships import load_relationships
from browser_unified import ready as existing_ready,select,fill


def ready(page):
    existing_ready(page)
    expect(page.get_by_test_id('stAlert').filter(has_text=re.compile(r'Traceback:|StreamlitAPIException'))).to_have_count(0)


@contextmanager
def server(root,output):
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env=os.environ.copy();env.pop('INSIGHTPILOT_UNIVERSAL_WORKSPACE',None)
    env['INSIGHTPILOT_WORKSPACE_ROOT']=str(root)
    with (output/'server.log').open('w') as log:
        process=subprocess.Popen([sys.executable,'-m','streamlit','run','app.py','--server.address=127.0.0.1',f'--server.port={port}','--server.headless=true','--browser.gatherUsageStats=false'],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        try:
            url=f'http://127.0.0.1:{port}'
            for _ in range(120):
                try:
                    if httpx.get(url+'/_stcore/health',timeout=1).status_code==200:break
                except httpx.HTTPError:pass
                if process.poll() is not None:raise RuntimeError('Local Streamlit failed; inspect server.log')
                time.sleep(.25)
            else:raise RuntimeError('Local Streamlit did not become ready')
            yield url
        finally:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()


def navigate(page,name):
    page.locator('.st-key-workflow-nav').get_by_text(name,exact=True).click();ready(page)


def open_expander(page,label):
    heading=page.get_by_text(label,exact=True)
    if not heading.evaluate("e=>e.closest('details').open"):
        heading.click();ready(page)


def multi(page,label,names):
    box=page.get_by_role('combobox',name=label,exact=True)
    for name in names:
        box.click();box.fill(name)
        page.get_by_role('option').filter(has_text=re.compile('^'+re.escape(name)+'$')).first.click()
    box.press('Escape');ready(page)


def screenshot(page,output,name,*,target=None):
    if target:target.scroll_into_view_if_needed()
    else:
        page.evaluate("document.querySelector('[data-testid=stMain]')?.scrollTo(0,0)")
        page.wait_for_timeout(100)
    page.screenshot(path=str(output/(name+'.png')))
    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+2'),'Horizontal page overflow'


def run(output):
    output.mkdir(parents=True,exist_ok=False);root=output/'projects'
    sources=[('taxis',ROOT/'data/unified-evaluation/sources/taxis.csv','08d6d71784dbaa2651fee37fc03389754194c05d72d2d19cbc2c799dea6ac09d'),('penguins',ROOT/'data/real-cases/penguins.csv','f204db2c753b0937caac3cb35258562c14f073e4bbc76be24b4c51ce22767a93')]
    for _,path,digest in sources:assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
    trips=pd.read_csv(sources[0][1]);penguins=pd.read_csv(sources[1][1]);events=[];errors=[]
    with server(root,output) as url,sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':900});page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(url,wait_until='domcontentloaded');ready(page)
        expect(page.get_by_role('button',name='Upload dataset',exact=True)).to_be_visible(timeout=30000)
        screenshot(page,output,'01-home-empty')
        page.get_by_role('button',name='Create analysis project',exact=True).click();ready(page)
        expect(page.get_by_text('Choose a workspace name of 1–60 visible characters.',exact=True)).to_be_visible()
        screenshot(page,output,'02-actionable-empty-name')
        page.get_by_role('textbox',name='Project name',exact=True).fill('Public transport review')
        page.get_by_role('textbox',name='Business purpose (optional)',exact=True).fill('Describe a public trip sample; assess coverage and alternatives.')
        page.get_by_role('button',name='Create analysis project',exact=True).click();ready(page)
        page.locator('[data-testid=stFileUploader] input[type=file]').first.set_input_files(str(sources[0][1]));ready(page)
        page.get_by_role('button',name='Import tables',exact=True).click();ready(page)
        expect(page.get_by_role('heading',name='Just imported · taxis.csv',exact=True)).to_be_visible(timeout=60000)
        screenshot(page,output,'03-imported-public-data')
        workspace=next(w for w in catalog(root) if metadata(w)['name']=='Public transport review')
        original=workspace.info('taxis');assert original['rows']==len(trips) and not original['roles']
        assert original['source']['sha256']==sources[0][2]
        events.append({'create_project':True,'real_browser_upload':'taxis.csv','rows':len(trips),'source_sha256':original['source']['sha256']})
        page.get_by_role('button',name='Review data quality',exact=True).click();ready(page)
        expect(page.get_by_text('Review potential issues',exact=False).first).to_be_visible()
        screenshot(page,output,'04-quality-priorities')
        assert not workspace.info('taxis')['roles']
        navigate(page,'Explore');select(page,'Comparison field','color')
        page.get_by_role('button',name='Open Ranked groups',exact=True).click();ready(page)
        page.get_by_role('button',name='Adjust filters',exact=True).click();ready(page)
        select(page,'Category filter 1','payment')
        payment=trips.payment.value_counts().index[0];select(page,'Value 1',payment)
        filtered=trips[trips.payment.eq(payment)]
        plot=page.locator('[data-testid=stPlotlyChart] .js-plotly-plot').first
        expect(plot).to_be_visible(timeout=30000)
        actual=plot.evaluate('(e)=>({categories:Array.from(e._fullData[0].y),values:Array.from(e._fullData[0].x)})')
        expected=filtered.groupby('color',dropna=False).size().to_dict()
        print({'rendered_chart':actual,'independent_expected':expected},flush=True)
        assert dict(zip(actual['categories'],actual['values']))==expected
        expect(page.get_by_text(f'{len(filtered):,} selected / {len(trips):,}',exact=False).first).to_be_visible()
        screenshot(page,output,'05-category-comparison-filtered',target=plot)
        page.get_by_role('button',name='Save chart to report',exact=True).click();ready(page)
        page.get_by_role('button',name='Save table to report',exact=True).click();ready(page)
        events.append({'filtered_public_trip_rows':len(filtered),'payment_filter':str(payment),'independent_category_counts':expected,'chart_values_reconciled':True,'saved_chart_and_table':True})
        container=page.get_by_test_id('stPlotlyChart').first
        container.evaluate("e=>e.scrollIntoView({block:'center'})");container.hover()
        container.get_by_role('button',name='Box Select',exact=True).click()
        page.wait_for_function("document.querySelector('[data-testid=stPlotlyChart] .js-plotly-plot')?.layout?.dragmode==='select'")
        bar=plot.locator('.barlayer .point path').first.bounding_box();center=bar['y']+bar['height']/2;endpoint=bar['x']+bar['width']
        page.mouse.move(endpoint-5,center-4);page.mouse.down();page.mouse.move(endpoint+5,center+4,steps=15);page.mouse.up()
        expect(page.get_by_text('Chart selection:',exact=False)).to_be_visible(timeout=30000);ready(page)
        expect(page.get_by_text(f'{actual["values"][0]:,} matching rows',exact=False)).to_be_visible()
        screenshot(page,output,'05b-pointer-drilldown')
        events.append({'actual_pointer_chart_selection':True,'drilled_rows':actual['values'][0],'category':actual['categories'][0]})
        page.get_by_role('button',name='Reset chart selection',exact=True).click();ready(page)
        navigate(page,'Workspace Home');screenshot(page,output,'06-home-project-cards')
        navigate(page,'Explore');assert page.get_by_role('combobox',name='Value 1',exact=True).input_value()==payment
        page.get_by_role('button',name='Open Numeric distribution',exact=True).click();ready(page)
        select(page,'Numeric field','distance')
        histogram=page.locator('[data-testid=stPlotlyChart] .js-plotly-plot').first
        frequencies=histogram.evaluate('(e)=>Array.from(e._fullData[0].y)')
        assert sum(frequencies)==int(pd.to_numeric(filtered.distance,errors='coerce').notna().sum())
        screenshot(page,output,'07-numeric-distribution',target=histogram)
        events.append({'histogram_rows_reconciled':sum(frequencies),'no_semantic_approval_required':not bool(workspace.info('taxis').get('semantics',{}).get('reviewed'))})
        page.get_by_role('button',name='Open Numeric relationship',exact=True).click();ready(page)
        select(page,'Horizontal field','distance');select(page,'Vertical field','fare')
        screenshot(page,output,'08-numeric-relationship',target=page.get_by_test_id('stPlotlyChart').first)
        navigate(page,'Quality & Preparation')
        select(page,'Inspect field meaning and statistics','pickup')
        page.get_by_text('Review this field and row grain',exact=True).click()
        select(page,'Field role','date')
        page.get_by_role('textbox',name='Timestamp purpose',exact=True).fill('Recorded trip pickup timestamp in this historical source sample.')
        page.get_by_role('textbox',name='Reviewed row grain',exact=True).fill('One recorded trip in the public mirror sample; coverage is not established.')
        page.get_by_text('Use this field as the shared date basis',exact=True).click()
        page.get_by_text('I confirm these field definitions and row grain',exact=True).click()
        expect(page.get_by_role('checkbox',name='Use this field as the shared date basis',exact=True)).to_be_checked()
        page.get_by_role('button',name='Approve field definition',exact=True).click();ready(page)
        expect(page.get_by_role('combobox',name='Date basis',exact=True)).to_have_value(re.compile('^pickup'),timeout=30000)
        navigate(page,'Explore');page.get_by_role('button',name='Open Calendar trend',exact=True).click();ready(page)
        select(page,'Calendar grain','day')
        trend=page.locator('[data-testid=stPlotlyChart] .js-plotly-plot').first
        daily=trend.evaluate('(e)=>({dates:Array.from(e._fullData[0].x),counts:Array.from(e._fullData[0].y)})')
        reference=filtered.groupby(pd.to_datetime(filtered.pickup).dt.date).size().to_dict()
        rendered={pd.Timestamp(day).date():value for day,value in zip(daily['dates'],daily['counts']) if value is not None}
        assert rendered==reference
        screenshot(page,output,'08b-reviewed-calendar-trend',target=trend)
        page.get_by_role('button',name='Save chart to report',exact=True).click();ready(page)
        page.get_by_text('Save this analysis',exact=True).click()
        page.get_by_role('textbox',name='View name',exact=True).fill('Public payment selection')
        page.get_by_role('button',name='Save view and layout',exact=True).click();ready(page)
        assert 'Public payment selection' in workspace.saved_objects('view')
        events.append({'reviewed_timestamp_explicitly':True,'daily_trend_matches_independent_reference':True,'named_analysis_saved':True})
        navigate(page,'Investigate')
        page.get_by_role('textbox',name='Investigation name',exact=True).fill('Recorded trip selection')
        page.get_by_role('textbox',name='Business question',exact=True).fill('How do recorded category counts differ in this selected public sample?')
        page.get_by_role('textbox',name='Observed facts',exact=True).fill(f'The selected source sample contains {len(filtered)} recorded rows. This describes the uploaded extract, not all trips.')
        page.get_by_role('textbox',name='Hypotheses to investigate',exact=True).fill('Check whether sampling and payment coverage explain the observed category mix; no causal effect is estimated.')
        page.get_by_role('textbox',name='Uncertainties and limitations',exact=True).fill('Historical mirror sample. Coverage, independence and business suitability require review.')
        block_names=list(workspace.saved_objects('block'))
        multi(page,'Supporting saved findings',block_names)
        page.get_by_role('button',name='Save investigation',exact=True).click();ready(page)
        assert 'ux.investigation:Recorded trip selection' in workspace.saved_objects('layout')
        screenshot(page,output,'09-investigation-brief',target=page.get_by_role('heading',name='Define the problem. Build the evidence.',exact=True))
        navigate(page,'SQL Lab');page.get_by_role('button',name='Run query',exact=True).click();ready(page)
        expect(page.get_by_role('button',name='Save query result to Reports',exact=True)).to_be_visible()
        screenshot(page,output,'10-sql-still-available',target=page.get_by_role('heading',name='SQL Lab',exact=True))
        navigate(page,'Reports')
        select(page,'Use a saved investigation','Recorded trip selection')
        page.get_by_role('button',name='Use investigation notes',exact=True).click();ready(page)
        assert 'recorded category counts' in page.get_by_role('textbox',name='Business context',exact=True).input_value()
        assert page.get_by_role('textbox',name='Executive summary',exact=True).input_value()==''
        fill(page,'Business report title','Public trip sample — descriptive review')
        saved=workspace.saved_objects('block')
        report_order=sorted(block_names,key=lambda name:('figure' not in saved[name],saved[name]['evidence']['columns'][0]!='period'))
        for name in block_names:page.get_by_role('spinbutton',name='Position · '+name,exact=True).fill(str(report_order.index(name)+1))
        page.get_by_role('textbox',name='Executive summary',exact=True).fill('Observed sample counts are retained with their executed evidence. This is a descriptive review, not a causal business diagnosis.')
        page.get_by_role('textbox',name='Business context',exact=True).fill('Public NYC TLC mirror sample; no claim of population coverage.')
        page.get_by_role('tab',name='Interpretation & actions',exact=True).click()
        page.get_by_role('textbox',name='Investigation and interpretation',exact=True).fill('Group differences are associations in the selected extract. Alternative explanations require further evidence.')
        page.get_by_role('textbox',name='Recommendations and validation steps',exact=True).fill('Validate source coverage and business definitions before deciding on an action or controlled experiment.')
        page.get_by_role('tab',name='Methods & limitations',exact=True).click()
        page.get_by_role('textbox',name='Data and methodology',exact=True).fill('Local DuckDB counts with explicit payment selection. No source data was altered.')
        page.get_by_role('textbox',name='Limitations',exact=True).fill('Descriptive sample only. Missingness, units and collection bias require review.')
        page.get_by_role('button',name='Save report draft',exact=True).click();ready(page)
        page.get_by_role('button',name='Preview & build report',exact=True).click();ready(page)
        page.screenshot(path=str(output/'report-before-download.png'))
        print({'report_alerts':page.get_by_test_id('stAlert').all_inner_texts(),'positions':[box.input_value() for box in page.get_by_role('spinbutton').all()]},flush=True)
        expect(page.get_by_role('button',name='Download business report · HTML',exact=True)).to_be_visible(timeout=10000)
        with page.expect_download() as download:page.get_by_role('button',name='Download business report · HTML',exact=True).click()
        download.value.save_as(str(output/'business-report.html'))
        html=(output/'business-report.html').read_text()
        assert 'Executive summary' in html and 'Recommendations and validation steps' in html and original['source']['sha256'] in html
        assert 'insightpilot-report-definition' in html and '<script src="http' not in html
        screenshot(page,output,'11-report-builder',target=page.get_by_role('heading',name='Build a defensible business report',exact=True))
        screenshot(page,output,'12-report-preview',target=page.get_by_text('Preview the finished report',exact=True))
        export_context=browser.new_context(viewport={'width':1280,'height':832},offline=True)
        exported=export_context.new_page();outbound=[]
        exported.on('request',lambda request:outbound.append(request.url) if request.url.startswith(('http://','https://')) else None)
        # Managed Chromium blocks file: navigation. Render the authorized export
        # bytes directly in an offline blank document; no file/network grants or
        # browser policies are changed.
        exported.set_content(html,wait_until='load')
        expect(exported.locator('.js-plotly-plot')).to_have_count(2)
        expect(exported.get_by_role('heading',name='Executive summary',exact=True)).to_be_visible()
        screenshot(exported,output,'12b-standalone-report')
        assert not outbound,outbound
        export_context.close()
        events.append({'business_report_downloaded':True,'report_draft_saved':True,'ordered_findings':report_order,'self_contained_HTML':True})
        navigate(page,'Data Hub')
        page.get_by_text('Upload datasets',exact=True).click();ready(page)
        page.locator('[data-testid=stFileUploader] input[type=file]').first.set_input_files(str(sources[1][1]));ready(page)
        page.get_by_role('button',name='Import tables',exact=True).click();ready(page)
        expect(page.get_by_role('heading',name='Just imported · penguins.csv',exact=True)).to_be_visible(timeout=60000)
        page.get_by_role('button',name='Review data quality',exact=True).click();ready(page)
        select(page,'Preparation operation','trim');select(page,'Preparation column','sex')
        before=workspace.info('penguins');page.get_by_role('button',name='Apply preparation to a new version',exact=True).click();ready(page)
        expect(page.get_by_role('combobox',name='Version history / undo',exact=True)).to_have_value(re.compile('^2 · trim'),timeout=15000)
        prepared=workspace.info('penguins');assert len(prepared['versions'])==2 and prepared['source']==before['source']
        select(page,'Version history / undo',f'1 · import · {len(penguins):,} rows · 0 conversion errors')
        page.get_by_role('button',name='Activate selected version',exact=True).click();ready(page)
        expect(page.locator('.st-key-active-scope')).to_contain_text(before['current'][-10:],timeout=15000)
        assert workspace.info('penguins')['current']==before['current']
        screenshot(page,output,'13-second-dataset-preparation-undo')
        navigate(page,'Explore');select(page,'Comparison field','species');page.get_by_role('button',name='Open Ranked groups',exact=True).click();ready(page)
        graph=page.locator('[data-testid=stPlotlyChart] .js-plotly-plot').first
        actual=graph.evaluate('(e)=>({categories:Array.from(e._fullData[0].y),values:Array.from(e._fullData[0].x)})')
        assert dict(zip(actual['categories'],actual['values']))==penguins.groupby('species').size().to_dict()
        screenshot(page,output,'14-different-public-schema',target=graph)
        assert not load_relationships(workspace)
        events.append({'second_public_dataset':'Palmer penguins','rows':len(penguins),'category_counts_reconciled':True,'reversible_preparation_and_undo':True,'no_auto_join':True})
        assert workspace.info('taxis')['current']==original['current'] and workspace.info('taxis')['source']==original['source']
        for _,source,digest in sources:assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
        page.close();page=browser.new_page(viewport={'width':1280,'height':832});page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(url,wait_until='domcontentloaded');ready(page)
        screenshot(page,output,'15a-fresh-session-projects')
        card=page.locator('.st-key-ux-project-'+metadata(workspace)['id'])
        card.get_by_role('button',name='Saved work',exact=True).click();ready(page)
        select(page,'Saved report draft','Public trip sample — descriptive review')
        assert page.get_by_role('textbox',name='Business report title',exact=True).input_value()=='Public trip sample — descriptive review'
        screenshot(page,output,'15-macbook-size-saved-report')
        select(page,'Analysis table',f'taxis.csv · {len(trips):,} rows')
        open_expander(page,'Saved analyses')
        saved_view=page.get_by_role('combobox',name='Saved view',exact=True)
        if saved_view.get_attribute('aria-expanded')!='true':saved_view.click()
        expect(page.get_by_role('option').first).to_be_visible()
        page.wait_for_timeout(500);saved_view.fill('Public payment selection');page.wait_for_timeout(500)
        page.get_by_role('option',name='Public payment selection',exact=True).click();ready(page)
        expect(saved_view).to_have_value('Public payment selection')
        expect(page.get_by_role('button',name='Restore saved view',exact=True)).to_be_visible()
        page.get_by_role('button',name='Restore saved view',exact=True).click();ready(page)
        expect(page.get_by_text(f'{len(filtered):,} selected / {len(trips):,}',exact=False).first).to_be_visible()
        screenshot(page,output,'15b-restored-analysis-and-filters')
        navigate(page,'Workspace Home');screenshot(page,output,'16-macbook-size-project-home')
        events.append({'fresh_browser_reopened_report':True,'restored_saved_analysis_and_filters':True,'viewport_checked':{'width':1280,'height':832},'source_hashes_unchanged':True})
        browser.close()
    assert not errors,errors
    report={'at':datetime.now(timezone.utc).isoformat(),'actual_Chromium_UI':True,'native_Mac':False,'Qwen_tested':False,'uci_verification':'Pending on user Mac; official archive blocked by cloud network policy. No UCI source fabricated.','sources':[{'dataset':name,'path':str(path.relative_to(ROOT)),'sha256':digest} for name,path,digest in sources],'events':events,'browser_errors':errors,'passed':True}
    (output/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    run(args.output.resolve())
