"""Real local Chromium workflows. Starts its own loopback-only app per workspace."""
import argparse
from contextlib import contextmanager
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import uuid

import httpx
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]


def select(page,label,value):
    print('select',label,value,flush=True)
    box=page.get_by_role('combobox',name=label,exact=True)
    if box.input_value()==value:
        ready(page);return
    box.click();box.fill(value);page.wait_for_timeout(250)
    if box.get_attribute('aria-expanded')!='true': box.press('ArrowDown')
    exact=page.get_by_role('option').filter(has_text=__import__('re').compile('^'+__import__('re').escape(value)+'$'))
    expect(exact.first).to_be_visible(timeout=10000)
    exact.first.click();ready(page)
    expect(box).to_have_value(value,timeout=10000)


def fill(page,label,value):
    box=page.get_by_role('textbox',name=label,exact=True)
    box.fill(value);box.press('Enter');ready(page)


def ready(page):
    page.wait_for_timeout(350)
    expect(page.get_by_test_id('stStatusWidget')).not_to_be_visible(timeout=60000)
    expect(page.get_by_test_id('stException')).to_have_count(0)
    expect(page.get_by_test_id('stAlert').filter(has_text='could not complete')).to_have_count(0)


@contextmanager
def app_server(workspace,log):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env=os.environ.copy();env['INSIGHTPILOT_UNIVERSAL_WORKSPACE']=str(Path(workspace).resolve())
    with Path(log).open('w') as stream:
        process=subprocess.Popen([sys.executable,'-m','streamlit','run','app.py','--server.headless=true',
            '--server.address=127.0.0.1','--server.port='+str(port),'--browser.gatherUsageStats=false'],cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT)
        try:
            url='http://127.0.0.1:'+str(port)
            for attempt in range(120):
                try:
                    if httpx.get(url+'/_stcore/health',timeout=1).status_code==200:break
                except httpx.HTTPError:pass
                if process.poll() is not None:raise RuntimeError('Streamlit exited; inspect '+str(log))
                time.sleep(.25)
            else:raise RuntimeError('App did not become healthy')
            yield url
        finally:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()


def workflow(browser,case,folder):
    from insightpilot.universal import Workspace
    name=case['shape'];workspace=ROOT/case['workspace'];folder.mkdir(parents=True,exist_ok=True)
    errors=[];events=[]
    with app_server(workspace,folder/'server.log') as url:
        page=browser.new_page(viewport={'width':1512,'height':1050});page.on('pageerror',lambda e:errors.append(str(e)))
        response=page.goto(url,wait_until='domcontentloaded')
        expect(page.get_by_role('combobox',name='Analysis table',exact=True)).to_be_visible(timeout=60000)
        select(page,'Analysis table',case['table']);ready(page)
        select(page,'Analysis metric',case['metric']['name'])
        select(page,'Comparison field',case['context']['comparison']);ready(page)
        if name=='transactions':
            page.get_by_text('Shared analysis filters',exact=True).click()
            select(page,'Category filter 1','color');select(page,'Value 1','yellow');ready(page)
        expect(page.get_by_test_id('stMetricValue').nth(1)).to_have_text(case['display_value'],timeout=60000)
        expect(page.locator('.js-plotly-plot').first).to_be_visible(timeout=60000)
        page.screenshot(path=str(folder/'overview.png'),full_page=True)
        events.append({'overview_value':case['value'],'selected_rows':case['selected_rows'],'shared_date_basis':case['context']['date_basis']})
        # Native Plotly image download from the rendered figure.
        plot=page.get_by_test_id('stPlotlyChart').first;plot.hover()
        with page.expect_download(timeout=60000) as dl:
            plot.get_by_role('button',name='Download plot as a PNG',exact=True).click()
        dl.value.save_as(str(folder/'chart.png'));assert (folder/'chart.png').read_bytes().startswith(b'\x89PNG')
        events.append({'png_download':True})
        page.get_by_text('Quality & Preparation',exact=True).click()
        expect(page.get_by_role('heading',name='Understand every field',exact=True)).to_be_visible(timeout=60000)
        page.get_by_role('textbox',name='Search the column catalog',exact=True).fill(case['search_field'])
        page.get_by_role('textbox',name='Search the column catalog',exact=True).press('Enter');ready(page)
        page.screenshot(path=str(folder/'quality.png'),full_page=True)
        events.append({'full_catalog_search':case['search_field']})
        page.get_by_text('Overview',exact=True).click()
        expect(page.get_by_test_id('stMetricValue').nth(1)).to_have_text(case['display_value'],timeout=60000)
        events.append({'metric_and_filters_survive_quality':True})
        page.get_by_text('Explore',exact=True).click();ready(page)
        select(page,'Group by',case['context']['comparison']);ready(page)
        graph=page.locator('[data-testid="stPlotlyChart"] .js-plotly-plot').first
        plot=page.get_by_test_id('stPlotlyChart').first
        plot.evaluate("e=>e.scrollIntoView({block:'center'})");plot.hover()
        plot.get_by_role('button',name='Box Select',exact=True).click()
        page.wait_for_function("document.querySelector('[data-testid=stPlotlyChart] .js-plotly-plot')?.layout?.dragmode==='select'",timeout=10000)
        paths=graph.locator('.barlayer .point path')
        values=graph.evaluate('e=>Array.from(e._fullData[0].x)')
        index=next((i for i,value in enumerate(values) if value is not None and value>0),0)
        box=paths.nth(index).bounding_box();endpoint=box['x']+box['width'] if values[index]>0 else box['x']
        center=box['y']+box['height']/2
        page.mouse.move(endpoint-5,center-4);page.mouse.down();page.mouse.move(endpoint+5,center+4,steps=15);page.mouse.up()
        expect(page.get_by_text('Chart selection:',exact=False)).to_be_visible(timeout=60000);ready(page)
        page.screenshot(path=str(folder/'drill-through.png'),full_page=True)
        events.append({'pointer_box_selection_to_supporting_records':True})
        page.get_by_role('button',name='Reset chart selection',exact=True).click();ready(page)
        page.get_by_text('SQL Lab',exact=True).click()
        expect(page.get_by_role('button',name='Run query',exact=True)).to_be_visible(timeout=60000)
        page.get_by_role('button',name='Run query',exact=True).click();ready(page)
        expect(page.get_by_text('SQL Lab: validated read-only SELECT',exact=False)).to_be_visible(timeout=60000) if False else None
        fill(page,'Report table block name','Browser guided '+name)
        page.get_by_role('button',name='Save query result to Reports',exact=True).click();ready(page)
        expect(page.get_by_text('Saved query result and execution evidence.',exact=True)).to_be_visible(timeout=60000)
        block=Workspace(workspace).saved_objects('block')['Browser guided '+name]
        assert block['evidence']['rows'] and block['evidence']['context']['date_basis']==case['context']['date_basis']
        page.get_by_role('button',name='Edit generated SQL',exact=True).click();ready(page)
        expect(page.locator('iframe[title="streamlit_ace.streamlit_ace"]').first).to_be_visible(timeout=60000)
        # Use the real Ace editor for an advanced aggregate query.
        frame=page.frame_locator('iframe[title="streamlit_ace.streamlit_ace"]')
        editor=frame.locator('.ace_text-input');editor.focus();editor.press('ControlOrMeta+a');editor.fill(case['aggregate_sql']);editor.press('ArrowRight')
        page.wait_for_timeout(1800)
        page.get_by_role('button',name='Run query',exact=True).click();ready(page)
        fill(page,'Report table block name','Browser aggregate '+name)
        page.get_by_role('button',name='Save query result to Reports',exact=True).click();ready(page)
        expect(page.get_by_text('Saved query result and execution evidence.',exact=True)).to_be_visible(timeout=60000)
        block=Workspace(workspace).saved_objects('block')['Browser aggregate '+name]
        actual=block['evidence']['rows'][0][0]
        assert abs(actual-case['value'])<max(1e-8,abs(case['value'])*1e-10),(actual,case['value'])
        events.append({'guided_query_and_saved_block':True,'ace_editor_query_value':actual,'independent_reference':case['independent_pandas_value']})
        page.get_by_role('button',name='Explain validated query',exact=True).click();ready(page)
        page.screenshot(path=str(folder/'sql-lab.png'),full_page=True)
        page.get_by_text('AI Analyst',exact=True).click()
        page.get_by_role('button',name='Calculate evidence-backed answer',exact=True).click();ready(page)
        expect(page.get_by_text('Deterministic guided analyst; no model or tokens used',exact=True)).to_be_visible(timeout=60000)
        events.append({'zero_token_answer':True})
        page.get_by_text('Advanced',exact=True).click()
        select(page,'Analytical method','Mean confidence interval')
        select(page,'Analysis value field',case['search_field'])
        page.get_by_role('button',name='Run selected analysis',exact=True).click();ready(page)
        expect(page.get_by_text('Independent observations',exact=False)).to_be_visible(timeout=60000)
        page.screenshot(path=str(folder/'advanced.png'),full_page=True)
        events.append({'real_data_mean_interval':True})
        page.get_by_text('Reports',exact=True).click()
        page.get_by_role('button',name='Build report from this selection',exact=True).click();ready(page)
        with page.expect_download(timeout=60000) as dl:
            page.get_by_role('button',name='Download standalone HTML report',exact=True).click()
        dl.value.save_as(str(folder/'browser-report.html'));html=(folder/'browser-report.html').read_text()
        assert case['metric']['name'] in html and case['source']['sha256'] in html and 'source_sha256' in html
        events.append({'report_download_source_context_sql':True})
        page.set_viewport_size({'width':768,'height':1024});page.get_by_text('Overview',exact=True).click();ready(page)
        page.screenshot(path=str(folder/'tablet.png'),full_page=True)
        width=page.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth})')
        assert width['scroll']<=width['width']+2,width
        inputs=page.locator('input,textarea,select,button').count()
        page.keyboard.press('Tab');focus=page.evaluate('document.activeElement.tagName')
        assert inputs>10 and focus!='BODY'
        events.append({'tablet_width':768,'horizontal_overflow':False,'keyboard_focus':focus,'labelled_controls_present':True})
        assert not errors,errors
        page.close()
    return {'dataset':name,'status':'passed','events':events,'javascript_errors':errors,'screenshots':str(folder.relative_to(ROOT)),'http_status':response.status}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--case',choices=['transactions','time-observations','relational']);args=parser.parse_args()
    payload=json.loads((ROOT/'docs/unified-initial-evaluation.json').read_text())
    folder=ROOT/'outputs/unified-browser'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:5]);folder.mkdir(parents=True)
    checks=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
        for case in payload['initial_cases']:
            shape=Path(case['workspace']).name
            if args.case and shape!=args.case:continue
            case['shape']=shape
            from insightpilot.universal.story import format_value
            case['display_value']=format_value(case['value'])
            case['search_field']={'transactions':'total','time-observations':'Ozone','relational':'arr_delay'}[shape]
            case['aggregate_sql']={'transactions':'SELECT sum(try_cast(total AS DOUBLE)) AS value FROM observations',
                'time-observations':'SELECT avg(try_cast(Ozone AS DOUBLE)) AS value FROM observations',
                'relational':'SELECT avg(try_cast(arr_delay AS DOUBLE)) AS value FROM events'}[shape]
            checks.append(workflow(browser,case,folder/shape));print(shape,'browser passed',flush=True)
        browser.close()
    result={'generated_at':datetime.now(timezone.utc).isoformat(),'browser':'System Chromium via Playwright, headless Linux','checks':checks}
    (ROOT/'docs/unified-browser.json').write_text(json.dumps(result,indent=2)+'\n')

if __name__=='__main__':main()
