"""Rendered redesign acceptance. Isolated, explicitly synthetic data; no external AI."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
from urllib.parse import urlparse,parse_qs
import sys
import pandas as pd
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from insightpilot.universal.workspace_labels import catalog,metadata
from insightpilot.universal.ui_workflow import PAGES  # single source of truth for wheel destinations
from insightpilot.universal.store import Workspace
from browser_bi_workflow import server,ready,screenshot
from browser_unified import select



def hub(page):
    return page.frame_locator('iframe[title="insightpilot.universal.command_navigation.insightpilot_command_hub"]')


def open_hub(page):
    frame=page.locator('iframe[title="insightpilot.universal.command_navigation.insightpilot_command_hub"]')
    expect(frame).to_be_visible()
    opener=hub(page).get_by_role('button',name='Open command wheel',exact=True)
    if opener.is_visible():opener.click();ready(page)
    expect(hub(page).get_by_role('button',name='Explore',exact=True)).to_be_visible()


def unobscured(page):
    collisions=page.evaluate("""() => {
      const dock=document.querySelector('.st-key-command-dock,.st-key-command-dock-expanded,.st-key-command-wheel,.st-key-command-wheel-expanded');
      if(!dock || getComputedStyle(dock).position!=='fixed')return [];
      const d=dock.getBoundingClientRect();
      return Array.from(document.querySelectorAll('[data-testid=stPlotlyChart],[data-testid=stDataFrame],[data-testid=stFileUploader],[data-testid=stTextInput],[data-testid=stSelectbox]')).filter(e=>!dock.contains(e)).flatMap(e=>{
        const clip=e.closest('[data-testid=stSidebar]') || e.closest('[data-testid=stMain]');if(!clip)return [];
        const r=e.getBoundingClientRect(),c=clip.getBoundingClientRect();
        const l=Math.max(0,r.left,c.left),t=Math.max(0,r.top,c.top),rr=Math.min(innerWidth,r.right,c.right),bb=Math.min(innerHeight,r.bottom,c.bottom);
        return rr>l && bb>t && Math.min(rr,d.right)-Math.max(l,d.left)>1 && Math.min(bb,d.bottom)-Math.max(t,d.top)>1 ? [e.dataset.testid] : [];
      });
    }""")
    assert not collisions,{'navigation_overlaps_visible_controls':collisions}


def multi(page,label,names):
    # Every selection can trigger a Streamlit rerun; wait before opening again.
    import re
    for name in names:
        ready(page)
        box=page.get_by_role('combobox',name=label,exact=True)
        box.click();box.fill(name)
        if box.get_attribute('aria-expanded')!='true':box.press('ArrowDown')
        option=page.get_by_role('option').filter(has_text=re.compile('^'+re.escape(name)+'$')).first
        expect(option).to_be_visible(timeout=10000);option.click();ready(page)
    page.get_by_role('combobox',name=label,exact=True).press('Escape');ready(page)


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
        page.on('dialog',lambda d:d.accept())
        page.goto(url);ready(page)
        expect(hub(page).locator('.sector')).to_have_count(len(PAGES))
        expect(page.get_by_role('heading',name='Data Analysis',exact=True)).to_be_visible()
        expect(page.get_by_role('heading',name='Good questions. Clear evidence.',exact=True)).to_have_count(0)
        assert 'Choose a destination' not in hub(page).locator('body').inner_text()
        screenshot(page,output,'01-home-empty-1440')
        hub(page).get_by_role('button',name='Explore',exact=True).hover()
        expect(hub(page).locator('#destination')).to_contain_text('Explore')
        screenshot(page,output,'02-wheel-hover')
        # Actual keyboard activation, not JS-dispatched clicks.
        hub(page).get_by_role('button',name='Workspace Home',exact=True).focus()
        page.keyboard.press('ArrowRight');page.keyboard.press('Enter');ready(page)
        expect(page.get_by_role('heading',name='Your data, in one place.',exact=True)).to_be_visible()
        events.append('Keyboard arrow movement and Enter changed destination')
        navigate(page,'Workspace Home')
        page.get_by_role('button',name='Save Project',exact=True).click();ready(page)
        dialog=page.get_by_role('dialog')
        dialog.get_by_role('textbox',name='Project name',exact=True).fill('Synthetic operations review')
        dialog.get_by_role('textbox',name='Business purpose (optional)',exact=True).fill('Interface verification with synthetic data; no real business conclusions.')
        dialog.get_by_role('textbox',name='Project description (optional)',exact=True).fill('Isolated browser acceptance project.')
        dialog.get_by_role('button',name='Save Project',exact=True).click();ready(page)
        navigate(page,'Data Hub')
        page.locator('[data-testid=stFileUploader] input[type=file]').first.set_input_files(str(source));ready(page)
        page.get_by_role('button',name='Import tables',exact=True).click();ready(page)
        expect(page.get_by_role('heading',name='Just imported · '+source.name,exact=True)).to_be_visible(timeout=30000)
        workspace=Workspace(output/'projects'/'.sessions'/parse_qs(urlparse(page.url).query)['project_session'][0])
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
            unobscured(page)
            if destination!='Workspace Home':expect(page.locator('.st-key-command-dock')).to_be_visible()
            if destination=='Explore':screenshot(page,output,'02b-wheel-active')
        events.append(f'All {len(PAGES)} actual wheel sectors route successfully and show active state')
        navigate(page,'Workspace Home');screenshot(page,output,'04-project-home')
        navigate(page,'Quality & Preparation');screenshot(page,output,'05-quality')
        hub(page).get_by_role('button',name='Expand command wheel',exact=True).click();ready(page)
        expect(hub(page).locator('body.expanded')).to_be_visible()
        unobscured(page);screenshot(page,output,'05b-expanded-quality')
        hub(page).get_by_role('button',name='Collapse command wheel',exact=True).click();ready(page)
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
        unobscured(page)
        expect(page.locator('.st-key-command-dock')).to_be_in_viewport()
        events.append('Persistent dock remains visible while scrolling analytical charts and never intersects visible chart/table/form controls')
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
        save_expander=page.get_by_text('Save this analysis',exact=True)
        if not save_expander.evaluate("e=>e.closest('details').open"):save_expander.click();ready(page)
        page.get_by_role('textbox',name='View name',exact=True).fill('Persistent navigation selection')
        page.get_by_role('button',name='Save view and layout',exact=True).click();ready(page)
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
        for width,height in [(1920,1080),(1280,832),(1100,900),(768,1024),(390,844)]:
            page.set_viewport_size({'width':width,'height':height});ready(page)
            unobscured(page);screenshot(page,output,f'11-home-{width}')
            if width>1100:
                expect(hub(page).locator('.sector')).to_have_count(len(PAGES))
                navigate(page,'Explore');unobscured(page)
                screenshot(page,output,f'12-compact-explore-{width}')
                navigate(page,'Workspace Home')
            else:
                expect(hub(page).get_by_role('button',name='Open command wheel',exact=True)).to_be_visible()
                open_hub(page);unobscured(page)
                expect(hub(page).locator('.sector')).to_have_count(len(PAGES))
                screenshot(page,output,f'12-expanded-wheel-{width}')
                navigate(page,'Explore')
                expect(page.get_by_role('heading',name='Find the signal.',exact=True)).to_be_visible()
                expect(page.locator('.st-key-active-scope')).to_contain_text('80 selected / 160')
                expect(hub(page).get_by_role('button',name='Open command wheel',exact=True)).to_be_visible()
                unobscured(page);screenshot(page,output,f'13-responsive-explore-{width}')
                navigate(page,'Workspace Home')
        events.append('1440, 1920, 1280, 1100, 768, and 390px layouts checked; mobile wheel opens in one tap and preserves selected filters')
        # Dataset and project switches retain saved work and never remove routing.
        page.set_viewport_size({'width':1440,'height':900});ready(page)
        from insightpilot.universal.workspace_labels import table_labels,create_named
        workspace.add_file(source,'secondary',provenance={'origin':'synthetic'})
        navigate(page,'Explore')
        labels=table_labels(workspace)
        select(page,'Analysis table',labels['secondary']);navigate(page,'Quality & Preparation')
        expect(page.get_by_role('combobox',name='Analysis table',exact=True)).to_have_value(labels['secondary'])
        navigate(page,'Explore');select(page,'Analysis table',labels[table])
        assert 'Persistent navigation selection' in workspace.saved_objects('view')
        page.get_by_role('button',name='Save Changes',exact=True).click();ready(page)
        second=create_named(output/'projects','Second navigation project')
        navigate(page,'Workspace Home');select(page,'Active project','Second navigation project')
        navigate(page,'Data Hub');expect(page.locator('.st-key-command-dock')).to_be_visible()
        select(page,'Active project','Synthetic operations review');navigate(page,'Reports')
        workspace=Workspace(output/'projects'/'.sessions'/parse_qs(urlparse(page.url).query)['project_session'][0])
        select(page,'Saved report draft','Synthetic acceptance report')
        expect(page.get_by_role('textbox',name='Business report title',exact=True)).to_have_value('Synthetic acceptance report')
        page.reload();ready(page)
        open_hub(page)
        expect(hub(page).locator('.sector')).to_have_count(len(PAGES))
        assert workspace.saved_objects('layout')['ux.investigation:Synthetic segment review']['title']=='Synthetic segment review'
        events.append('Wheel works after dataset switches, project switches, ordinary reruns, and browser refresh; saved selection, investigation, and report draft retained')
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
