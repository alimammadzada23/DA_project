"""Real launcher/Chromium/restart acceptance using only an HTTP model test double."""
from contextlib import contextmanager
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid

import pandas as pd
from playwright.sync_api import sync_playwright,expect
from accept_local_ai import ROOT,fixture,ORIGINAL
from browser_local_ai import mock_ollama,ready,ask,download_evidence,analyst_mode,result_screenshot
from insightpilot.universal.workspace_labels import save_name,metadata
import launch_insightpilot as launch


@contextmanager
def unrelated_listener():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):self.send_response(200);self.end_headers();self.wfile.write(b'unrelated application')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield server.server_port
    finally:server.shutdown();server.server_close();thread.join(timeout=3)


@contextmanager
def server(env,port,log):
    with log.open('w') as stream:
        command=[sys.executable,str(ROOT/'scripts/launch_insightpilot.py'),'--no-browser','--no-ollama-start','--port',str(port)]
        process=subprocess.Popen(command,env=env,cwd='/tmp',stdout=stream,stderr=subprocess.STDOUT);state={}
        try:
            for _ in range(200):
                if process.poll() is not None:raise RuntimeError('Launcher exited before this test owned a server: '+log.read_text()[-2000:])
                try:state=json.loads((ROOT/'outputs/launcher/server.lock').read_text())
                except (ValueError,OSError):state={}
                if launch.healthy(state):break
                time.sleep(.2)
            else:raise RuntimeError('Launcher readiness timed out; see '+str(log))
            assert state['port']!=port
            yield process,state,command
        finally:
            process.send_signal(signal.SIGTERM)
            try:process.wait(timeout=12)
            except subprocess.TimeoutExpired:process.kill();process.wait();raise RuntimeError('Launcher did not stop its owned server promptly.')
            assert not launch.healthy(state),'Owned server survived launcher shutdown'


def main():
    run=ROOT/'outputs/reliability-browser'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:6]);run.mkdir(parents=True)
    e,profile,metric,frame=fixture(run/'fixture');save_name(e.workspace,'Persistent repair review')
    original_info=e.workspace.info('facts');events=[];errors=[]
    p={'version':1,'action':'analyze','table':'facts','operation':'aggregate','group_by':['job'],'measures':[{'aggregation':'records'}],'sort_by':'records','descending':True,'limit':5,'chart':'bar'}
    follow='Now show only the top three.'
    plans={ORIGINAL:p,follow:{'version':1,'action':'analyze','table':'facts','operation':'aggregate','inherit':True,'limit':3}}
    with mock_ollama(plans) as (endpoint,state,stop_model),unrelated_listener() as occupied,sync_playwright() as pw:
        env=os.environ.copy();env.update(INSIGHTPILOT_UNIVERSAL_WORKSPACE=str(e.workspace.directory),INSIGHTPILOT_OLLAMA_URL=endpoint,INSIGHTPILOT_OLLAMA_MODEL='test-double:latest')
        browser=pw.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
        page=browser.new_page(viewport={'width':1512,'height':1100});page.on('pageerror',lambda err:errors.append(str(err)))
        with server(env,occupied,run/'first-start.log') as (_,owned,command):
            url=f"http://127.0.0.1:{owned['port']}/{owned['prefix']}/"
            page.goto(url);expect(page.get_by_role('combobox',name='Analytics workspace',exact=True)).to_have_value('Persistent repair review',timeout=60000)
            page.get_by_text('AI Analyst',exact=True).click();ready(page);analyst_mode(page,'Local AI Analyst')
            state['missing_operation_next']=True;ask(page,ORIGINAL)
            result=download_evidence(page,run/'repaired-result.json')
            assert result['status']=='ok' and result['backend']['repair_attempted'] and len(state['requests'])==2
            table=pd.DataFrame(result['primary']['rows'],columns=result['primary']['columns'])
            assert list(table.m0)==[13,11,9,8,7] and len(table)==5
            expect(page.locator('.js-plotly-plot')).to_have_count(1)
            page.evaluate("document.querySelector('[data-testid=stMain]').scrollTop=0");page.screenshot(path=str(run/'question-and-repair-status.png'))
            result_screenshot(page,run/'verified-top-five.png')
            card=page.get_by_test_id('stChatMessage').last
            export=card.get_by_test_id('stExpander').filter(has_text='Chart data and export · 1');export.locator('summary').click()
            with page.expect_download() as dl:export.get_by_role('button',name='Plotted values CSV',exact=True).click()
            dl.value.save_as(str(run/'plotted-counts.csv'));plotted=pd.read_csv(run/'plotted-counts.csv')
            assert list(plotted.x)==[13,11,9,8,7] and not plotted.isin(['dtype','bdata']).any().any()
            page.get_by_role('button',name='Calculate evidence-backed answer',exact=True).click();ready(page);assert len(state['requests'])==2
            ask(page,follow);top=download_evidence(page,run/'top-three.json');assert len(top['primary']['rows'])==3
            save=page.get_by_test_id('stExpander').filter(has_text='Save this analysis to Reports').last;save.locator('summary').click()
            name=save.get_by_role('textbox',name='Saved analysis name',exact=True);name.fill('Tomorrow verified count');name.press('Enter');ready(page)
            save.get_by_role('button',name='Save verified analysis',exact=True).click();ready(page)
            expect(page.get_by_text('Saved verified results, definitions and evidence in Reports.',exact=True)).to_be_visible(timeout=30000)
            stored=e.workspace.saved_objects('block')['Tomorrow verified count']
            before_restart=e.workspace.info('facts')
            # The ordinary UI stores shared analysis defaults on first use.
            # Everything about the imported data and reviewed semantics must
            # stay unchanged, and the full post-use catalog must survive restart.
            assert {k:v for k,v in before_restart.items() if k!='analysis_defaults'}==original_info
            events.append({'repaired_original_top_five':True,'exact_counts':[13,11,9,8,7],'chart_download_numeric_values_verified':True,'duplicate_inference_prevented':True,'followup_top_three':True})
            reuse=subprocess.run(command,env=env,cwd='/tmp',capture_output=True,text=True,timeout=20)
            assert reuse.returncode==0 and 'Reusing this project' in reuse.stdout
            current=json.loads((ROOT/'outputs/launcher/server.lock').read_text());assert current['pid']==owned['pid'] and current['port']==owned['port']
            events.append({'occupied_port_preserved':True,'second_launch_reuses_same_server':True,'bound_address':'127.0.0.1'})
        assert launch.local_get(occupied,'/')[1]==b'unrelated application'
        stop_model()
        with server(env,occupied,run/'second-start.log') as (_,owned,_):
            page.goto(f"http://127.0.0.1:{owned['port']}/{owned['prefix']}/")
            expect(page.get_by_role('combobox',name='Analytics workspace',exact=True)).to_have_value('Persistent repair review',timeout=60000);ready(page)
            page.get_by_text('AI Analyst',exact=True).click();ready(page)
            assert e.workspace.info('facts')==before_restart and metadata(e.workspace)['name']=='Persistent repair review'
            assert e.workspace.saved_objects('block')['Tomorrow verified count']==stored
            replay=page.get_by_test_id('stExpander').filter(has_text='Replay a saved analyst calculation');replay.locator('summary').click()
            before=len(state['requests']);replay.get_by_role('button',name='Replay saved calculation without a model',exact=True).click();ready(page)
            expect(page.locator('.js-plotly-plot')).to_have_count(1)
            assert len(state['requests'])==before
            page.screenshot(path=str(run/'replayed-after-restart.png'))
            analyst_mode(page,'Local AI Analyst');expect(page.get_by_text('Ollama is not reachable.',exact=False)).to_be_visible(timeout=30000)
            assert page.get_by_role('button',name='Calculate evidence-backed answer',exact=True).is_disabled()
            analyst_mode(page,'Standard analysis');ask(page,'How many records are there?')
            fallback=download_evidence(page,run/'offline-standard.json');assert fallback['primary']['rows'][0][0]==len(frame)
            events.append({'clean_stop_and_restart':True,'datasets_roles_name_and_saved_analysis_preserved':True,'saved_chart_replay_without_model':True,'offline_standard_correct':len(frame)})
        browser.close()
    logs='\n'.join(p.read_text() for p in run.glob('*start.log'))
    assert 'was created with a default value' not in logs and not errors
    report={'status':'passed','platform':'Real Linux Streamlit processes and Chromium; native Finder not executed',
        'model':'Explicit HTTP test double; no actual Qwen/model weights','run_directory':str(run.relative_to(ROOT)),
        'events':events,'javascript_errors':errors,'question_widget_duplication_warning':False}
    (ROOT/'docs/local-ai-reliability-browser.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
