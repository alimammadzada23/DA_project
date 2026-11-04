"""Capture real public-evaluation app views, positioned at the top of the page."""
import json
from pathlib import Path
import shutil
from playwright.sync_api import sync_playwright,expect
from browser_unified import ROOT,app_server,ready,select


def main():
    initial=json.loads((ROOT/'docs/unified-initial-evaluation.json').read_text())['initial_cases']
    heldout=json.loads((ROOT/'docs/unified-heldout-evaluation.json').read_text())
    cases=[{'workspace':c['workspace'],'table':c['table'],'metric':c['metric']['name'],'comparison':c['context']['comparison'],'name':Path(c['workspace']).name,'rows':c['rows']} for c in initial]
    cases.append({'workspace':heldout['workspace'],'table':'unseen_rows','metric':'Sample case share','comparison':'agegp','name':'heldout','rows':88})
    out=ROOT/'docs/screenshots/unified';out.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
        for case in cases:
            with app_server(ROOT/case['workspace'],ROOT/'outputs'/('capture-'+case['name']+'.log')) as url:
                page=browser.new_page(viewport={'width':1512,'height':1100});page.goto(url)
                expect(page.get_by_role('combobox',name='Analysis table',exact=True)).to_be_visible(timeout=60000)
                select(page,'Analysis table',case['table'])
                expect(page.get_by_test_id('stMetricValue').first).to_have_text(f'{case["rows"]:,}',timeout=60000)
                select(page,'Analysis metric',case['metric']);select(page,'Comparison field',case['comparison'])
                if case['name']=='transactions':
                    page.get_by_text('Shared analysis filters',exact=True).click();select(page,'Category filter 1','color');select(page,'Value 1','yellow')
                page.evaluate("document.querySelectorAll('[data-testid=stMain],[data-testid=stSidebarContent]').forEach(e=>e.scrollTop=0)")
                page.wait_for_timeout(500);page.screenshot(path=str(out/(case['name']+'-overview.png')))
                if case['name']=='heldout':
                    page.get_by_text('SQL Lab',exact=True).click();ready(page)
                    page.get_by_role('button',name='Edit generated SQL',exact=True).click();ready(page)
                    page.get_by_text('Available sources and columns',exact=True).click()
                    page.evaluate("document.querySelectorAll('[data-testid=stMain],[data-testid=stSidebarContent]').forEach(e=>e.scrollTop=0)")
                    page.wait_for_timeout(500);page.screenshot(path=str(out/'heldout-sql-editor.png'))
                page.close()
            print(case['name'],'captured',flush=True)
        browser.close()

if __name__=='__main__':main()
