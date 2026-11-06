"""Browser check of chart insight layers and the verified AI interpretation.

Uses an isolated synthetic workspace and, for the AI Analyst, an explicitly
named loopback Ollama TEST DOUBLE (never real Qwen). The double derives its
wording from the computed table it receives, as a real model would have to.

  STREAMLIT_SERVER_FILE_WATCHER_TYPE=none python scripts/browser_insights.py --output outputs/insights-check
"""
import argparse
from contextlib import contextmanager
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import random
import sys
import tempfile
import threading

sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from playwright.sync_api import sync_playwright, expect
from browser_unified import app_server, select, ready, fill
from browser_redesign import navigate
from insightpilot.universal.projects import ProjectSession

MODEL='test-double:latest'


class FakeOllama(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,value):
        body=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_GET(self):
        self.reply({'models':[{'name':MODEL,'size':1,'digest':'test-double'}]})
    def do_POST(self):
        request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path=='/api/show':return self.reply({'capabilities':['completion'],'details':{'family':'test-double'}})
        user=json.loads(request['messages'][-1]['content'])
        if 'headline' in request['format'].get('required',[]):
            columns,rows=user['table']['columns'],user['table']['rows']
            total=sum(r[1] for r in rows);top,second,low=rows[0],rows[1],rows[-1]
            content={'headline':f'{top[0]} leads with {top[1]:,.0f} ({top[1]/total:.1%} of the total shown).',
                     'findings':[f'{top[0]} exceeds {second[0]} by {top[1]-second[1]:,.0f}.',
                                 f'{low[0]} is lowest at {low[1]:,.0f}.',
                                 f'{top[0]} grew because of seasonal demand.'],
                     'caveats':['Totals describe the current filters and retained source version only.'],
                     'follow_ups':[f'How does {columns[1].split(" (")[0].lower()} change over time?','Which product lines contribute most in North?']}
        else:
            content={'version':1,'action':'analyze','table':'orders','operation':'aggregate','group_by':['region'],
                     'measures':[{'aggregation':'sum','column':'amount'}],'sort_by':'m0'}
        self.reply({'model':MODEL,'done':True,'done_reason':'stop','message':{'role':'assistant','content':json.dumps(content)}})


@contextmanager
def fake_ollama():
    server=ThreadingHTTPServer(('127.0.0.1',0),FakeOllama)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{server.server_port}'
    finally:server.shutdown()


def fixture(folder):
    random.seed(11);rows=['order_id,order_date,region,product,amount']
    for i in range(900):
        d=date(2024,1,1)+timedelta(days=random.randint(0,540))
        if date(2024,9,1)<=d<date(2024,10,1):continue
        region='' if i%23==0 else random.choice(['North','South','East','West','Central'])
        amount=round(random.lognormvariate(3.2,.6)*(1.6 if region=='North' else 1),2)
        rows.append(f'O{i},{d},{region},{random.choice("ABCDEFGH")}-line,{amount}')
    path=folder/'SYNTHETIC-orders.csv';path.write_text('\n'.join(rows)+'\n');return path


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    output=Path(args.output);output.mkdir(parents=True,exist_ok=False)
    temporary=Path(tempfile.mkdtemp(prefix='insightpilot-insights-'))
    root=temporary/'catalog';session=ProjectSession.start(root)
    session.workspace.add_file(fixture(temporary),'orders')
    session.workspace.approve_semantics('orders',{'order_date':'date','region':'geography','product':'product','amount':'revenue'},
        'One synthetic order',units={'amount':'EUR'},date_purposes={'order_date':'Order placed'},date_basis='order_date')
    os.environ['INSIGHTPILOT_WORKSPACE_ROOT']=str(root);events=[]
    with fake_ollama() as endpoint:
        os.environ['INSIGHTPILOT_OLLAMA_URL']=endpoint;os.environ['INSIGHTPILOT_OLLAMA_MODEL']=MODEL
        with app_server(session.directory,output/'server.log') as url, sync_playwright() as p:
            browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(url);ready(page)
            def go(name):navigate(page,name)
            def shot(name):
                chart=page.get_by_test_id('stPlotlyChart').first;expect(chart).to_be_visible(timeout=60000)
                chart.scroll_into_view_if_needed();page.wait_for_timeout(600);chart.screenshot(path=str(output/name))
            go('Explore')
            select(page,'What would you like to understand?','Numeric distribution');shot('01-histogram.png')
            plot=page.locator('[data-testid=stPlotlyChart] .js-plotly-plot').first
            bars=plot.evaluate('(e)=>({x:Array.from(e._fullData[0].x),offset:Array.from(e._fullData[0].offset),width:Array.from(e._fullData[0].width)})')
            assert all(o==0 for o in bars['offset'][:-1]) and abs(bars['x'][0]+bars['width'][0]-bars['x'][1])<1e-9
            events.append('Histogram bars span their full bins in the rendered chart')
            select(page,'What would you like to understand?','Calendar trend');shot('02-trend.png')
            select(page,'What would you like to understand?','Ranked groups');select(page,'Group by','region');shot('03-ranked.png')
            go('AI Analyst')
            page.get_by_text('Local AI Analyst',exact=True).click();ready(page)
            expect(page.get_by_text('Local AI available',exact=False)).to_be_visible(timeout=60000)
            fill(page,'Your analytical question','Which region has the highest total amount?')
            page.get_by_role('button',name='Calculate evidence-backed answer',exact=True).click();ready(page)
            expect(page.get_by_text('AI interpretation',exact=False)).to_be_visible(timeout=60000)
            expect(page.get_by_text('removed: causal wording',exact=False)).to_be_visible()
            expect(page.get_by_text('grew because',exact=False)).to_have_count(0)
            box=page.locator('[class*="st-key-ai-interpretation-"]').first;box.scroll_into_view_if_needed()
            page.wait_for_timeout(400);box.screenshot(path=str(output/'04-ai-interpretation.png'))
            events.append('Verified interpretation shown; the causal sentence from the test double was removed and disclosed')
            browser.close()
    result={'passed':not errors,'events':events,'browser_errors':errors,'synthetic_fixture':True,'model':'loopback test double, not Qwen'}
    (output/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
    if errors:raise SystemExit(1)


if __name__=='__main__':main()
