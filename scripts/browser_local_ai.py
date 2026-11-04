"""Real Chromium workflows with an explicitly labeled Ollama HTTP test double.

This executes the app, calculator and network adapter. It does NOT execute Qwen.
All fixtures are synthetic or documented public measurements; no user workspace
is selected, moved, merged or deleted.
"""
from contextlib import contextmanager
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import urlsplit

import httpx
import pandas as pd
from playwright.sync_api import sync_playwright,expect

from local_ai_benchmark_cases import ROOT,make_datasets,catalog
from insightpilot.universal import Workspace
from insightpilot.universal.workspace_labels import create_named,save_name,metadata,table_labels
from insightpilot.universal.engine import Engine

MODEL='test-double:latest'


def ready(page):
    page.wait_for_timeout(350)
    expect(page.get_by_test_id('stStatusWidget')).not_to_be_visible(timeout=60000)
    expect(page.get_by_test_id('stException')).to_have_count(0)


def select(page,label,value):
    ready(page)
    box=page.get_by_role('combobox',name=label,exact=True)
    if box.input_value()==value:ready(page);return
    target=page.get_by_role('option').filter(has_text=__import__('re').compile('^'+__import__('re').escape(value)+'$'))
    for attempt in range(3):
        box.press('Escape');box.click();box.fill(value);page.wait_for_timeout(400)
        if box.get_attribute('aria-expanded')!='true':box.press('ArrowDown')
        try:expect(target.first).to_be_visible(timeout=3000)
        except AssertionError:
            if attempt==2:raise
            ready(page);continue
        # Keyboard selection survives a rerender detaching the floating option.
        box.press('Enter');ready(page);break
    expect(page.get_by_role('combobox',name=label,exact=True)).to_have_value(value,timeout=10000)


@contextmanager
def mock_ollama(overrides=None):
    plans={c['question']:c['oracle_plan'] for c in catalog()};plans.update(overrides or {})
    state={'requests':[],'bad_next':False,'missing_operation_next':False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,body,code=200):
            raw=json.dumps(body).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            if self.path=='/api/tags':self.send({'models':[{'name':MODEL,'size':0,'digest':'MOCK-NO-WEIGHTS'}]})
            else:self.send({'error':'not supported by test double'},404)
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
            if self.path=='/api/show':self.send({'capabilities':['completion'],'details':{'family':'TEST DOUBLE — NO QWEN EXECUTION'}});return
            assert self.path=='/api/chat'
            request=json.loads(body['messages'][1]['content']);state['requests'].append(request)
            if state['bad_next']:content='not JSON';state['bad_next']=False
            else:
                plan=dict(plans.get(request['question'],{'version':1,'action':'clarify','table':'facts','message':'This explicit test double has no fixture for that question.'}))
                if state['missing_operation_next']:plan.pop('operation',None);state['missing_operation_next']=False
                content=json.dumps(plan)
            self.send({'model':MODEL,'done':True,'message':{'role':'assistant','content':content}})
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    stopped=False
    def stop():
        nonlocal stopped
        if not stopped:server.shutdown();server.server_close();thread.join(timeout=5);stopped=True
    try:yield f'http://127.0.0.1:{server.server_port}',state,stop
    finally:stop()


@contextmanager
def app_server(root,endpoint,log):
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env=os.environ.copy();env.pop('INSIGHTPILOT_UNIVERSAL_WORKSPACE',None)
    env.update(INSIGHTPILOT_WORKSPACE_ROOT=str(root),INSIGHTPILOT_OLLAMA_URL=endpoint,INSIGHTPILOT_OLLAMA_MODEL=MODEL)
    with log.open('w') as stream:
        process=subprocess.Popen([sys.executable,'-m','streamlit','run','app.py','--server.headless=true','--server.address=127.0.0.1','--server.port='+str(port),'--browser.gatherUsageStats=false'],cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT)
        try:
            url='http://127.0.0.1:'+str(port)
            for _ in range(120):
                try:
                    if httpx.get(url+'/_stcore/health',timeout=1,trust_env=False).status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(.2)
            else:raise RuntimeError('App did not become healthy')
            yield url
        finally:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()


def ask(page,question):
    box=page.get_by_role('textbox',name='Your analytical question',exact=True);box.fill(question);box.press('Enter');ready(page)
    page.get_by_role('button',name='Calculate evidence-backed answer',exact=True).click();ready(page)


def analyst_mode(page,name):
    control=page.get_by_role('radio',name=name,exact=True)
    control.focus();control.press('Space');ready(page)


def download_evidence(page,path):
    ready(page)
    box=page.get_by_test_id('stExpander').filter(has_text=__import__('re').compile('Analysis evidence ·')).last
    if not box.locator('details').evaluate('(e)=>e.open'):box.locator('summary').click()
    ready(page)
    print('Downloading',path.name,flush=True)
    try:
        with page.expect_download(timeout=15000) as dl:box.get_by_role('button',name='Download analyst evidence',exact=True).click()
    except Exception:
        page.screenshot(path=str(path.with_suffix('.failure.png')))
        path.with_suffix('.failure.html').write_text(page.content())
        raise
    dl.value.save_as(str(path));return json.loads(path.read_text())


def result_screenshot(page,path):
    card=page.get_by_test_id('stChatMessage').last
    for details in card.locator('details[open]').all():details.locator('summary').click()
    card.evaluate("e=>e.scrollIntoView({block:'start'})")
    page.wait_for_timeout(200);page.screenshot(path=str(path))


def prepare_visible_workspace(root,name,dataset):
    w=create_named(root,name,'Explicit browser acceptance fixture')
    source=w.directory.parent/(w.directory.name+'-input.csv');dataset['raw'].to_csv(source,index=False)
    info=dataset['engine'].info
    w.add_file(source,'facts',provenance=dataset['provenance']|{'filename':name+'.csv'})
    w.approve_semantics('facts',info['roles'],info['grain'],units=info['semantics'].get('units'),date_purposes=info['semantics'].get('date_purposes'),date_basis=info['semantics'].get('date_basis'))
    w.save_analysis_defaults('facts',dataset['engine'].context.dictionary(),'Records')
    return w


def main():
    run=ROOT/'outputs/local-ai-browser'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:6]);run.mkdir(parents=True)
    reference_root=run/'references';reference_root.mkdir();data=make_datasets(reference_root)
    root=run/'workspaces';root.mkdir()
    first=prepare_visible_workspace(root,'Synthetic team review',data['synthetic'])
    second=prepare_visible_workspace(root,'Field measurements',data['heldout'])
    screenshots=run/'screenshots';screenshots.mkdir();events=[];errors=[];outbound=[];started=time.perf_counter()
    with mock_ollama() as (endpoint,state,stop_model),app_server(root,endpoint,run/'server.log') as url,sync_playwright() as p:
        browser=p.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
        page=browser.new_page(viewport={'width':1512,'height':1100});page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('requestfailed',lambda r:print('Request failed:',r.url,r.failure,flush=True))
        page.on('response',lambda r:print('HTTP failure:',r.status,r.url,flush=True) if r.status>=400 else None)
        page.on('request',lambda r:outbound.append(r.url) if urlsplit(r.url).scheme in ('http','https') and urlsplit(r.url).hostname not in ('127.0.0.1','localhost','::1') else None)
        page.goto(url);expect(page.get_by_role('combobox',name='Analytics workspace',exact=True)).to_be_visible(timeout=60000)
        select(page,'Analytics workspace','Synthetic team review')
        expect(page.get_by_test_id('stMetricValue').first).to_have_text('22',timeout=60000)
        assert 'data/universal' not in page.get_by_role('combobox',name='Analytics workspace',exact=True).input_value()
        events.append({'friendly_workspace_selected':True,'stable_id':metadata(first)['id']})
        page.get_by_text('AI Analyst',exact=True).click();ready(page)
        compound='Which job categories have the most records, and what is their average age?'
        ask(page,compound)
        expect(page.locator('.js-plotly-plot')).to_have_count(2,timeout=60000)
        actual=download_evidence(page,run/'standard-compound-evidence.json')
        table=pd.DataFrame(actual['primary']['rows'],columns=actual['primary']['columns'])
        assert table.iloc[0].m0==7 and table.iloc[0].m1==22.5
        page.evaluate("document.querySelector('[data-testid=stMain]').scrollTop=0");page.screenshot(path=str(screenshots/'standard-compound.png'))
        result_screenshot(page,screenshots/'standard-compound-result.png')
        page.locator('.js-plotly-plot').last.scroll_into_view_if_needed();page.screenshot(path=str(screenshots/'standard-compound-mean-chart.png'))
        events.append({'standard_compound':True,'top_group_records':7,'top_group_mean_age':22.5,'two_verified_charts':True})
        analyst_mode(page,'Local AI Analyst')
        expect(page.get_by_text('Local AI available · test-double:latest',exact=False)).to_be_visible(timeout=60000)
        base='Show record counts by job.';ask(page,base)
        expect(page.locator('.js-plotly-plot')).to_have_count(1,timeout=60000)
        first_result=download_evidence(page,run/'local-count-evidence.json');assert first_result['primary']['rows'][0][1]==7
        asks=len(state['requests']);page.get_by_role('button',name='Calculate evidence-backed answer',exact=True).click();ready(page);assert len(state['requests'])==asks
        ask(page,'Now show only the top five.');top=download_evidence(page,run/'top-five-evidence.json');assert len(top['primary']['rows'])==5
        ask(page,'And compare their average age.');mean=download_evidence(page,run/'followup-mean-evidence.json')
        assert {r[0] for r in mean['primary']['rows']}=={r[0] for r in top['primary']['rows']} and len(mean['primary']['rows'])==5
        expect(page.get_by_text('Follow-up restricted to 5',exact=False)).to_be_visible(timeout=60000)
        page.screenshot(path=str(screenshots/'local-followup-evidence.png'))
        result_screenshot(page,screenshots/'local-followup-result.png')
        events.append({'local_transport':'Explicit HTTP test double; no Qwen execution','model':MODEL,'followup_preserves_top_five':True,'duplicate_inference_prevented':True})
        # Save the complete current plan/chart/evidence through the ordinary UI.
        save=page.get_by_test_id('stExpander').filter(has_text='Save this analysis to Reports').last;save.locator('summary').click()
        save.get_by_role('textbox',name='Saved analysis name',exact=True).fill('Browser verified follow-up')
        save.get_by_role('button',name='Save verified analysis',exact=True).click();ready(page)
        expect(page.get_by_text('Saved verified results, definitions and evidence in Reports.',exact=True)).to_be_visible(timeout=60000)
        assert 'Browser verified follow-up' in first.saved_objects('block')
        events.append({'saved_plan_charts_evidence':True})
        # Scope changes remove all prior response cards before another model call.
        shared=page.get_by_test_id('stExpander').filter(has_text='Shared analysis filters').first
        if not shared.locator('details').evaluate('(e)=>e.open'):shared.locator('summary').click()
        select(page,'Category filter 1','region');ready(page)
        expect(page.get_by_test_id('stChatMessage')).to_have_count(0,timeout=60000)
        events.append({'shared_filter_invalidates_history':True})
        # Switching to a dissimilar authentic schema does not send previous intent.
        select(page,'Analytics workspace','Field measurements');ready(page)
        expect(page.get_by_test_id('stChatMessage')).to_have_count(0,timeout=60000)
        heldout='Which species has the highest average bill_length_mm?';ask(page,heldout)
        result=download_evidence(page,run/'heldout-evidence.json');frame=pd.DataFrame(result['primary']['rows'],columns=result['primary']['columns'])
        ref=data['heldout']['raw'].groupby('species').bill_length_mm.mean()
        for row in frame.itertuples():assert abs(row.m0-ref.loc[row.group_0])<1e-9
        last=state['requests'][-1]['context'];assert last['last_validated_plan'] is None
        assert 'job' not in {f['name'] for f in last['columns']}
        page.evaluate("document.querySelector('[data-testid=stMain]').scrollTop=0");page.screenshot(path=str(screenshots/'heldout-local-test-double.png'))
        result_screenshot(page,screenshots/'heldout-result.png')
        events.append({'heldout_reference_reconciled':True,'cross_workspace_context_isolation':True,'heldout_rows':344})
        old=second.info('facts');identifier=metadata(second)['id'];directory=second.directory
        settings=page.get_by_test_id('stExpander').filter(has_text='Workspace settings').first;settings.locator('summary').click()
        settings.get_by_role('textbox',name='Workspace display name',exact=True).fill('Field research review')
        settings.get_by_role('button',name='Save workspace name',exact=True).click();ready(page)
        assert second.directory==directory and metadata(second)['id']==identifier and second.info('facts')==old
        expect(page.get_by_role('combobox',name='Analytics workspace',exact=True)).to_have_value('Field research review',timeout=60000)
        page.screenshot(path=str(screenshots/'friendly-workspace.png'));events.append({'workspace_rename_preserves_identity_data_and_history':True})
        state['bad_next']=True;ask(page,'How many records are in this dataset?')
        expect(page.get_by_text('The model did not return a valid JSON plan.',exact=False)).to_be_visible(timeout=60000)
        events.append({'malformed_model_output_rejected':True})
        stop_model();page.get_by_role('button',name='Refresh local models',exact=True).click();ready(page)
        expect(page.get_by_text('Ollama is not reachable.',exact=False)).to_be_visible(timeout=60000)
        page.screenshot(path=str(screenshots/'local-unavailable.png'))
        analyst_mode(page,'Standard analysis');ask(page,'How many records are in this dataset?')
        result=download_evidence(page,run/'fallback-evidence.json');assert result['primary']['rows'][0][0]==344
        events.append({'stopped_test_service_detected':True,'standard_fallback_correct':344,'cloud_fallback_used':False})
        page.get_by_text('SQL Lab',exact=True).click();page.get_by_role('button',name='Run query',exact=True).click();ready(page)
        expect(page.get_by_role('textbox',name='Report table block name',exact=True)).to_be_visible(timeout=60000)
        events.append({'existing_sql_lab_works_without_ollama':True})
        # Correct delimiter before importing; repeat without deleting either table.
        page.get_by_text('Data Hub',exact=True).click();ready(page)
        source=run/'SYNTHETIC-semicolon.csv';source.write_text('group;value\nA;10\nB;20\nC;30\n')
        page.get_by_test_id('stFileUploader').first.locator('input[type=file]').set_input_files(str(source))
        expect(page.get_by_text('Another separator looks more plausible:',exact=False)).to_be_visible(timeout=60000)
        select(page,'Delimited text separator',"';'")
        expect(page.get_by_text('2 fields detected.',exact=False)).to_be_visible(timeout=60000)
        checkbox=page.get_by_role('checkbox',name='These files are synthetic test data',exact=True);checkbox.focus();checkbox.press('Space')
        page.get_by_role('button',name='Import tables',exact=True).click();ready(page)
        expect(page.get_by_text('3 rows imported. 2 fields detected.',exact=False)).to_be_visible(timeout=60000)
        page.get_by_role('button',name='Import tables',exact=True).click();ready(page)
        assert len(second.tables())==3 and len([v for v in table_labels(second).values() if 'import ' in v])==2
        page.screenshot(path=str(screenshots/'delimiter-and-import-context.png'))
        events.append({'delimiter_warning_before_import':True,'manual_separator_two_fields':True,'duplicate_imports_retained_with_context':True})
        new=page.get_by_test_id('stExpander').filter(has_text='New workspace').first;new.locator('summary').click()
        new.get_by_role('textbox',name='New workspace name',exact=True).fill('Scratch review')
        new.get_by_role('button',name='Create workspace',exact=True).click();ready(page)
        expect(page.get_by_role('combobox',name='Analytics workspace',exact=True)).to_have_value('Scratch review',timeout=60000)
        page.reload();ready(page);select(page,'Analytics workspace','Field research review')
        assert first.database.exists() and second.database.exists() and Engine(first,'facts').count()==22 and Engine(second,'facts').count()==344
        events.append({'friendly_creation_and_persistence_after_reload':True,'existing_workspaces_preserved':True})
        page.set_viewport_size({'width':768,'height':1024});ready(page);page.screenshot(path=str(screenshots/'tablet.png'))
        widths=page.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth})');assert widths['scroll']<=widths['width']+2
        page.keyboard.press('Tab');assert page.evaluate('document.activeElement.tagName')!='BODY'
        assert not errors,errors
        events.append({'tablet_width':768,'horizontal_overflow':False,'keyboard_focus':True})
        browser.close()
    report={'at':datetime.now(timezone.utc).isoformat(),'status':'passed','browser':'System Chromium via Playwright, Linux',
        'model_execution':'Explicit Ollama HTTP test double; no model weights, no actual Qwen execution. Numerical calculations are real.',
        'screenshots':str(screenshots.relative_to(ROOT)),'run_directory':str(run.relative_to(ROOT)),
        'events':events,'javascript_errors':errors,'browser_external_requests':outbound,'elapsed_seconds':round(time.perf_counter()-started,3)}
    (ROOT/'docs/local-ai-browser.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))

if __name__=='__main__':main()
