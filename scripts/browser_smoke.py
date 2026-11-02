"""Optional real-browser regression for the synthetic portfolio UI.

Requires Playwright and Chromium. Start the app with the portfolio-v2 database,
then run: python scripts/browser_smoke.py --url <local-server-url> --chromium <browser-path>
Application operation does not require Playwright.
"""
import argparse
import json
from pathlib import Path
from xml.etree import ElementTree as ET

from playwright.sync_api import sync_playwright, expect

parser=argparse.ArgumentParser()
parser.add_argument('--url',required=True)
parser.add_argument('--chromium')
parser.add_argument('--source',type=Path,default=Path('data/portfolio-demo-v2/SYNTHETIC-portfolio-v2.xes'))
parser.add_argument('--output',type=Path,default=Path('outputs/browser-check'))
args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
root=ET.parse(args.source).getroot()
traces=[{child.attrib['key']:child.attrib.get('value') for child in trace if 'key' in child.attrib} for trace in root]
if not traces or any(trace.get('fixture:status')!='SYNTHETIC' for trace in traces):
    parser.error('This regression requires the explicitly synthetic portfolio source')
total=len(traces);category='SYNTHETIC · 2-way matching'
filtered=sum(trace.get('Item Category')==category for trace in traces)
checks=[]

with sync_playwright() as playwright:
    browser=playwright.chromium.launch(executable_path=args.chromium,headless=True,args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1512,'height':1050})
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    response=page.goto(args.url,wait_until='domcontentloaded',timeout=60000)
    expect(page.get_by_test_id('stMetricValue')).to_have_count(4,timeout=60000)
    expect(page.get_by_test_id('stMetricValue').first).to_have_text(f'{total:,}',timeout=60000)
    page.locator('[data-testid="stPlotlyChart"] .js-plotly-plot').first.wait_for(timeout=60000)
    assert page.locator('[data-testid="stJson"]:visible').count()==0
    assert not page.get_by_role('button',name='Execute SQL',exact=True).is_visible()
    page.screenshot(path=str(args.output/'executive.png'))
    checks.append({'page':'Executive Overview','http_status':response.status,'items':total,'visible_json':0})
    page.get_by_role('combobox',name='Procurement process type',exact=True).click()
    page.get_by_role('option',name='2-way matching',exact=True).click();page.keyboard.press('Escape')
    expect(page.get_by_test_id('stMetricValue').first).to_have_text(f'{filtered:,}',timeout=60000)
    for name,filename in [('Process Explorer','process'),('Root-Cause Investigation','investigation')]:
        page.get_by_text(name,exact=True).click()
        page.get_by_role('heading',name=name,exact=True).wait_for(timeout=60000)
        page.get_by_text(f'Selected cohort: {filtered:,} of {total:,} recorded items',exact=False).wait_for(timeout=60000)
        page.locator('[data-testid="stPlotlyChart"] .js-plotly-plot').first.wait_for(timeout=60000)
        assert page.locator('[data-testid="stJson"]:visible').count()==0
        page.screenshot(path=str(args.output/(filename+'.png')))
        checks.append({'page':name,'filtered_items':filtered,'charts_rendered':True})
    page.get_by_role('button',name='Reset filters',exact=True).click()
    page.get_by_text(f'Selected cohort: {total:,} of {total:,} recorded items',exact=False).wait_for(timeout=60000)
    expect(page.get_by_test_id('stStatusWidget')).not_to_be_visible(timeout=60000)
    graph=page.locator('[data-testid="stPlotlyChart"] .js-plotly-plot').first
    expect(graph.locator('.barlayer .point path')).to_have_count(5,timeout=60000)
    group=graph.evaluate('(element)=>element.data[0].customdata[1][0]')
    page.get_by_role('heading',name='Root-Cause Investigation',exact=True).scroll_into_view_if_needed()
    page.screenshot(path=str(args.output/'investigation.png'))
    bar=graph.locator('.barlayer .point path').nth(1);bar.scroll_into_view_if_needed();box=bar.bounding_box()
    page.mouse.move(box['x']+2,box['y']-2);page.mouse.down()
    page.mouse.move(box['x']+box['width']+2,box['y']+box['height']+2,steps=15);page.mouse.up()
    display='Not recorded' if group is None else group.removeprefix('SYNTHETIC · ')
    page.get_by_role('heading',name='Underlying items · '+display,exact=True).wait_for(timeout=60000)
    case_id=next(trace['concept:name'] for trace in traces if trace.get('Item Category')==group)
    lookup=page.get_by_role('textbox',name='Look up an item ID',exact=True)
    lookup.fill(case_id);lookup.press('Enter')
    page.get_by_text('Item '+case_id,exact=False).wait_for(timeout=60000)
    checks.append({'reset_then_box_selection':True,'selected_group':group,'item_lookup':case_id})
    page.get_by_text('AI Business Analyst',exact=True).click()
    page.get_by_role('heading',name='AI Business Analyst',exact=True).wait_for(timeout=60000)
    page.get_by_role('button',name='Run suggested investigation',exact=True).click()
    page.get_by_test_id('stChatMessage').last.get_by_text('Guided Analyst · SQL, not an LLM',exact=True).first.wait_for(timeout=60000)
    assert page.locator('[data-testid="stJson"]:visible').count()==0
    page.get_by_role('heading',name='AI Business Analyst',exact=True).scroll_into_view_if_needed()
    page.screenshot(path=str(args.output/'analyst.png'))
    checks.append({'page':'AI Business Analyst','guided_answer_rendered':True,'visible_json':0})
    page.get_by_role('combobox',name='Procurement process type',exact=True).click()
    page.get_by_role('option',name='2-way matching',exact=True).click();page.keyboard.press('Escape')
    page.get_by_text(f'Selected cohort: {filtered:,} of {total:,} recorded items',exact=False).wait_for(timeout=60000)
    expect(page.get_by_test_id('stChatMessage')).to_have_count(0,timeout=60000)
    assert not errors,errors
    report={'scope':'SYNTHETIC PORTFOLIO UI ONLY; not original BPI findings','checks':checks,'uncaught_browser_errors':errors}
    (args.output/'browser-verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2));browser.close()
