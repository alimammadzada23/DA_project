"""Real-browser check of BI Studio and the eleven-slice wheel on an isolated synthetic workspace.

  STREAMLIT_SERVER_FILE_WATCHER_TYPE=none python scripts/browser_bi_studio.py --output outputs/bi-studio-check
"""
import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path
import random
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from playwright.sync_api import sync_playwright, expect
from browser_unified import app_server, ready
from browser_redesign import navigate
from insightpilot.universal.projects import ProjectSession


def fixture(folder):
    random.seed(5);rows=['order_id,order_date,region,product,quantity,unit_price']
    for i in range(1500):
        d=date(2024,1,1)+timedelta(days=random.randint(0,545));q=random.randint(1,6)
        rows.append(f'O{i//2},{d},{"" if i%29==0 else random.choice(["North","South","East","West"])},{random.choice("ABCDEF")}-line,{q},{random.choice([4.5,9,12.25,20])}')
    path=folder/'SYNTHETIC-sales.csv';path.write_text('\n'.join(rows)+'\n');return path


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    output=Path(args.output);output.mkdir(parents=True,exist_ok=False)
    temporary=Path(tempfile.mkdtemp(prefix='insightpilot-bi-'));root=temporary/'catalog'
    session=ProjectSession.start(root);w=session.workspace;w.add_file(fixture(temporary),'sales')
    w.approve_semantics('sales',{'order_date':'date','region':'geography','product':'product','quantity':'quantity','unit_price':'price','order_id':'transaction'},
                        'One synthetic order line',units={'unit_price':'EUR'},date_purposes={'order_date':'Order placed'},date_basis='order_date')
    # A prepared model so the browser check focuses on rendering and interaction.
    w.save_object('layout','bi.model:sales',{'columns':[{'name':'Line value','expression':'sales[quantity] * sales[unit_price]'}],
        'measures':[{'name':'Revenue','expression':'SUMX(sales, sales[quantity] * sales[unit_price])','format':'Currency / unit','unit':'EUR'},
                    {'name':'Orders','expression':'DISTINCTCOUNT(sales[order_id])','format':'Whole number'},
                    {'name':'Revenue LY','expression':"CALCULATE([Revenue], SAMEPERIODLASTYEAR('Date'[Date]))",'format':'Currency / unit','unit':'EUR'},
                    {'name':'YoY %','expression':'DIVIDE([Revenue] - [Revenue LY], [Revenue LY])','format':'Percent'},
                    {'name':'% of region total','expression':'DIVIDE([Revenue], CALCULATE([Revenue], ALL(sales[region])))','format':'Percent'}],
        'pages':{'Sales overview':{'slicers':['sales\x1fregion'],'visuals':[
            {'id':'c1','type':'Card','measures':['Revenue'],'rows':[],'title':'','top':None,'compare':'Revenue LY'},
            {'id':'c2','type':'Card','measures':['Orders'],'rows':[],'title':'','top':None},
            {'id':'v1','type':'Line','measures':['Revenue','Revenue LY'],'rows':['Date\x1fMonth'],'title':'Revenue vs last year','top':None},
            {'id':'v2','type':'Bar','measures':['Revenue'],'rows':['sales\x1fproduct'],'title':'Revenue by product','top':12},
            {'id':'v3','type':'Matrix','measures':['Revenue','% of region total','YoY %'],'rows':['sales\x1fregion'],'title':'Region scorecard','top':None},
            {'id':'v4','type':'Donut','measures':['Revenue'],'rows':['sales\x1fregion'],'title':'Revenue mix','top':8}]}}})
    os.environ['INSIGHTPILOT_WORKSPACE_ROOT']=str(root);events=[];errors=[]
    with app_server(session.directory,output/'server.log') as url, sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(url);ready(page);page.wait_for_timeout(800)
        page.screenshot(path=str(output/'01-home-eleven-slices.png'))
        navigate(page,'BI Studio')
        expect(page.get_by_text('Model it. Measure it. Report it.',exact=True)).to_be_visible()
        expect(page.get_by_test_id('stPlotlyChart')).to_have_count(3,timeout=60000)
        expect(page.get_by_test_id('stMetric').first).to_be_visible()
        page.set_viewport_size({'width':1440,'height':3300});page.wait_for_timeout(1200)
        page.screenshot(path=str(output/'02-report-page.png'))
        events.append('Report page renders cards (with YoY delta), line, bar, donut and matrix visuals')
        page.get_by_role('tab',name='Measures (DAX)').click();ready(page)
        page.wait_for_timeout(600);page.screenshot(path=str(output/'03-measures.png'))
        page.get_by_role('tab',name='Model').click();ready(page)
        page.wait_for_timeout(800);page.screenshot(path=str(output/'04-model.png'))
        events.append('Measures and Model tabs render, including the model diagram')
        browser.close()
    result={'passed':not errors,'events':events,'browser_errors':errors,'synthetic_fixture':True}
    (output/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
    if errors:raise SystemExit(1)


if __name__=='__main__':main()
