"""Rendered redesign acceptance. Isolated, explicitly synthetic data; no external AI."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
import pandas as pd
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from insightpilot.universal.workspace_labels import catalog,metadata
from browser_bi_workflow import server,ready,screenshot,multi
from browser_unified import select

PAGES=['Workspace Home','Data Hub','Quality & Preparation','Explore','Investigate','Reports','Overview','AI Analyst','SQL Lab','Advanced']


def hub(page):
    return page.frame_locator('iframe[title="insightpilot.universal.command_navigation.insightpilot_command_hub"]')


def open_hub(page):
    expander=page.get_by_text('Command hub · all destinations',exact=True)
    if expander.count() and not expander.evaluate("e=>e.closest('details').open"):
        expander.click();ready(page)
    expect(page.locator('iframe[title="insightpilot.universal.command_navigation.insightpilot_command_hub"]')).to_be_visible()
    expect(hub(page).get_by_role('button',name='Explore',exact=True)).to_be_visible()


def navigate(page,destination):
    print('Navigate',destination,flush=True)
    open_hub(page)
    hub(page).get_by_role('button',name=destination,exact=True).click()
    ready(page)
    expect(page.get_by_text('INSIGHTPILOT / '+destination.upper(),exact=False)).to_be_visible()


def run(output):
    output.mkdir(parents=True,exist_ok=False)
    source=output/'SYNTHETIC-review.csv'
    data=pd.DataFrame({'record_id':[f'R{i:03d}' for i in range(160)],
        'region':['North']*80+['South']*40+['East']*24+['West']*16,
        'amount':[float(i%30+10) for i in range(160)],
        'quantity':[(i%5)+1 for i in range(160)],
        'date':['2026-01-15']*80+['2026-02-15']*80})
    data.to_csv(source,index=False)
    errors=[];events=[]
    with server(output/'projects',output) as url,sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':900})
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(url);ready(page)
        expect(hub(page).get_by_role('button')).to_have_count(10)
        screenshot(page,output,'01-home-empty-1440')
        hub(page).get_by_role('button',name='Explore',exact=True).hover()
        expect(hub(page).locator('#hint')).to_contain_text('Explore')
        screenshot(page,output,'02-wheel-hover')
        # Actual keyboard activation, not JS-dispatched clicks.
        hub(page).get_by_role('button',name='Workspace Home',exact=True).focus()
        page.keyboard.press('ArrowRight');page.keyboard.press('Enter');ready(page)
        expect(page.get_by_role('heading',name='Your data, in one place.',exact=True)).to_be_visible()
        events.append('Keyboard arrow movement and Enter changed destination')
        navigate(page,'Workspace Home')
        page.get_by_role('textbox',name='Project name',exact=True).fill('Synthetic operations review')
        page.get_by_role('textbox',name='Business purpose (optional)',exact=True).fill('Interface verification with synthetic data; no real business conclusions.')
        page.get_by_role('textbox',name='Project description (optional)',exact=True).fill('Isolated browser acceptance project.')
        page.get_by_role('button',name='Create analysis project',exact=True).click();ready(page)
        page.locator('[data-testid=stFileUploader] input[type=file]').first.set_input_files(str(source));ready(page)
        page.get_by_role('button',name='Import tables',exact=True).click();ready(page)
        expect(page.get_by_role('heading',name='Just imported · '+source.name,exact=True)).to_be_visible(timeout=30000)
        workspace=next(w for w in catalog(output/'projects') if metadata(w)['name']=='Synthetic operations review')
        table=next(iter(workspace.tables()))
        original=workspace.info(table)
        assert original['rows']==160
        assert metadata(workspace)['purpose'].startswith('Interface verification')
        screenshot(page,output,'03-data-hub',target=page.get_by_role('heading',name='Just imported · '+source.name,exact=True))
        # Every actual SVG destination must successfully change the Python page.
        for destination in PAGES:
            navigate(page,destination)
            open_hub(page)
            expect(hub(page).get_by_role('button',name=destination,exact=True)).to_have_attribute('aria-pressed','true')
            if destination=='Explore':screenshot(page,output,'02b-wheel-active')
        events.append('All ten actual wheel sectors route successfully and show active state')
        navigate(page,'Workspace Home');screenshot(page,output,'04-project-home')
        navigate(page,'Quality & Preparation');screenshot(page,output,'05-quality')
        expect(page.get_by_test_id('stPlotlyChart').first).to_be_visible()
        navigate(page,'Explore');select(page,'Comparison field','region')
        select(page,'Group by','region')
        plot=page.locator('[data-testid=stPlotlyChart] .js-plotly-plot').first
        expect(plot).to_be_visible()
        actual=plot.evaluate('(e)=>({x:Array.from(e._fullData[0].x),y:Array.from(e._fullData[0].y)})')
        assert dict(zip(actual['y'],actual['x']))==data.groupby('region').size().to_dict()
        page.get_by_role('button',name='Save chart to report',exact=True).click();ready(page)
        page.get_by_role('button',name='Save table to report',exact=True).click();ready(page)
        screenshot(page,output,'06-explore',target=page.get_by_test_id('stPlotlyChart').first)
        select(page,'What would you like to understand?','Composition & Pareto')
        select(page,'Concentration grouping field','region')
        expect(page.get_by_text('Groups to reach 80%',exact=True)).to_be_visible()
        screenshot(page,output,'07-pareto',target=page.get_by_test_id('stPlotlyChart').first)
        plot=page.locator('[data-testid=stPlotlyChart] .js-plotly-plot').first
        shares=plot.evaluate('(e)=>Array.from(e._fullData[1].y)')
        assert shares==[.5,.75,.9,1]
        page.get_by_text('Donut',exact=True).click();ready(page)
        screenshot(page,output,'08-composition',target=page.get_by_test_id('stPlotlyChart').first)
        page.get_by_role('button',name='Save chart to report',exact=True).click();ready(page)
        events.append('Rendered grouped values and Pareto cumulative shares match independent pandas results; donut rendered and saved')
        # A shared filter survives wheel navigation.
        page.get_by_role('button',name='Adjust filters',exact=True).click();ready(page)
        select(page,'Category filter 1','region');select(page,'Value 1','North')
        navigate(page,'Investigate')
        expect(page.locator('.st-key-active-scope')).to_contain_text('80 selected / 160')
        page.get_by_role('textbox',name='Investigation name',exact=True).fill('Synthetic segment review')
        page.get_by_role('textbox',name='Business question',exact=True).fill('What is the recorded region mix in this synthetic fixture?')
        page.get_by_role('textbox',name='Observed facts',exact=True).fill('The generated fixture has 80 North records out of 160 total records. This is not a business finding.')
        page.get_by_role('textbox',name='Uncertainties and limitations',exact=True).fill('Synthetic software validation only; no causal or population inference.')
        blocks=list(workspace.saved_objects('block'))
        multi(page,'Supporting saved findings',blocks)
        page.get_by_role('button',name='Save investigation',exact=True).click();ready(page)
        screenshot(page,output,'09-investigation',target=page.get_by_role('heading',name='Define the problem. Build the evidence.',exact=True))
        navigate(page,'Reports')
        page.get_by_role('textbox',name='Business report title',exact=True).fill('Synthetic acceptance report')
        multi(page,'Choose saved findings',blocks)
        page.get_by_role('textbox',name='Executive summary',exact=True).fill('Generated data for software verification. No real-world findings.')
        page.get_by_role('button',name='Save report draft',exact=True).click();ready(page)
        page.get_by_role('button',name='Preview & build report',exact=True).click();ready(page)
        expect(page.get_by_role('button',name='Download business report · HTML',exact=True)).to_be_visible()
        with page.expect_download() as download:
            page.get_by_role('button',name='Download business report · HTML',exact=True).click()
        download.value.save_as(str(output/'synthetic-report.html'))
        assert original['source']['sha256'] in (output/'synthetic-report.html').read_text()
        screenshot(page,output,'10-report-builder',target=page.get_by_role('heading',name='Build a defensible business report',exact=True))
        screenshot(page,output,'10b-report-narrative',target=page.get_by_role('textbox',name='Executive summary',exact=True))
        events.append('Saved investigation, ordered report draft, self-contained report download with original source hash')
        navigate(page,'Workspace Home')
        for width,height in [(1920,1080),(1280,832),(768,1024),(390,844)]:
            page.set_viewport_size({'width':width,'height':height});ready(page)
            screenshot(page,output,f'11-home-{width}')
            if width>=1000:
                expect(hub(page).get_by_role('button')).to_have_count(10)
            else:
                expect(page.get_by_role('combobox',name='Navigate to',exact=True)).to_be_visible()
                select(page,'Navigate to','Explore')
                expect(page.get_by_role('heading',name='Find the signal.',exact=True)).to_be_visible()
                expect(page.locator('.st-key-active-scope')).to_contain_text('80 selected / 160')
                screenshot(page,output,f'12-responsive-explore-{width}')
                select(page,'Navigate to','Workspace Home')
        events.append('1440, 1920, 1280, 768, and 390px layouts checked; native responsive routes preserve filters')
        assert workspace.info(table)['current']==original['current']
        assert workspace.info(table)['source']==original['source']
        assert not errors,errors
        browser.close()
    report={'passed':True,'at':datetime.now(timezone.utc).isoformat(),'events':events,'browser_errors':errors,'data':'Explicitly synthetic isolated fixture','local_ai_model_tested':False}
    (output/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output.resolve())
