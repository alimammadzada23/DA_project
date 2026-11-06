"""Actual explicit-save project lifecycle, isolated from all user workspaces."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs,urlparse
import pandas as pd
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from insightpilot.universal.projects import ProjectSession,digest
from insightpilot.universal.workspace_labels import catalog,metadata,create_named
from insightpilot.universal.store import Workspace
from browser_bi_workflow import server,ready,screenshot
from browser_redesign import navigate,hub,multi,unobscured
from browser_unified import select


def fill(page,label,value):
    item=page.get_by_role('textbox',name=label,exact=True);item.fill(value);item.press('Tab');ready(page)


def token(page):return parse_qs(urlparse(page.url).query)['project_session'][0]


def run(output):
    output.mkdir(parents=True,exist_ok=False);root=output/'projects'
    source=output/'SYNTHETIC-project.csv'
    pd.DataFrame({'region':['North']*8+['South']*4,'amount':list(range(1,13))}).to_csv(source,index=False)
    source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    legacy=create_named(root,'Unrelated legacy project');legacy.add_file(source,'unrelated')
    original=digest(legacy.directory);errors=[];dialogs=[];events=[]
    with server(root,output) as url,sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':900});page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('dialog',lambda d:(dialogs.append(d.type),d.accept()))
        page.goto(url);ready(page)
        expect(page.get_by_role('button',name='Save Project',exact=True)).to_be_visible()
        expect(page.get_by_role('combobox',name='Analysis table',exact=True)).to_have_count(0)
        first=token(page)
        screenshot(page,output,'01-empty-unsaved-project')
        def upload():
            navigate(page,'Data Hub')
            page.locator('[data-testid=stFileUploader] input[type=file]').first.set_input_files(str(source));ready(page)
            page.get_by_role('button',name='Import tables',exact=True).click();ready(page)
            navigate(page,'Explore');select(page,'Comparison field','region');select(page,'Group by','region')
        upload()
        assert len(catalog(root))==1
        screenshot(page,output,'02-analysis-without-saving',target=page.get_by_test_id('stPlotlyChart').first)
        # Refresh must warn and recover temporary data, without publishing it.
        page.reload();ready(page)
        assert token(page)==first and len(catalog(root))==1
        expect(page.locator('.st-key-active-scope')).to_contain_text('12 selected / 12')
        events.append('Unsaved upload and analysis survive refresh in a temporary session, outside Saved Projects')
        page.get_by_role('button',name='Discard Project',exact=True).click();ready(page)
        dialog=page.get_by_role('dialog')
        expect(dialog.get_by_role('button',name='Save Project',exact=True)).to_be_visible()
        expect(dialog.get_by_role('button',name='Discard Project',exact=True)).to_be_visible()
        dialog.get_by_role('button',name='Cancel',exact=True).click();ready(page)
        assert token(page)==first
        page.get_by_role('button',name='Discard Project',exact=True).click();ready(page)
        page.get_by_role('dialog').get_by_role('button',name='Discard Project',exact=True).click();ready(page)
        assert token(page)!=first and len(catalog(root))==1
        expect(page.get_by_role('combobox',name='Analysis table',exact=True)).to_have_count(0)
        screenshot(page,output,'03-discarded-not-saved')
        events.append('Save / Discard / Cancel guard works; discard leaves only preserved legacy project, no inherited dataset')
        upload()
        page.get_by_role('button',name='Save chart to report',exact=True).click();ready(page)
        page.get_by_role('button',name='Save table to report',exact=True).click();ready(page)
        current=ProjectSession(root,token(page));blocks=list(current.workspace.saved_objects('block'))
        navigate(page,'Investigate')
        fill(page,'Investigation name','Synthetic project investigation')
        fill(page,'Business question','How are the generated region counts distributed?')
        fill(page,'Observed facts','Eight North records and four South records in a generated fixture.')
        page.get_by_role('button',name='Save investigation',exact=True).click();ready(page)
        navigate(page,'Reports')
        fill(page,'Business report title','Synthetic lifecycle report')
        multi(page,'Choose saved findings',blocks)
        fill(page,'Executive summary','Synthetic software validation only. No business claims.')
        page.get_by_role('button',name='Save report draft',exact=True).click();ready(page)
        assert len(catalog(root))==1
        page.get_by_role('button',name='Save Project',exact=True).click();ready(page)
        screenshot(page,output,'04-explicit-save-dialog')
        dialog=page.get_by_role('dialog')
        dialog.get_by_role('textbox',name='Project name',exact=True).fill('Explicitly saved analysis')
        dialog.get_by_role('button',name='Save Project',exact=True).click();ready(page)
        saved=next(w for w in catalog(root) if metadata(w)['name']=='Explicitly saved analysis')
        assert len(saved.saved_objects('block'))==2
        assert 'ux.investigation:Synthetic project investigation' in saved.saved_objects('layout')
        assert 'ux.report:Synthetic lifecycle report' in saved.saved_objects('layout')
        saved_digest=digest(saved.directory)
        navigate(page,'Workspace Home')
        card=page.locator('.st-key-ux-project-'+metadata(saved)['id'])
        screenshot(page,output,'05-saved-project-card',target=card)
        expect(card.get_by_role('button',name='Delete',exact=True)).to_be_visible()
        navigate(page,'Reports')
        fill(page,'Executive summary','An edit that has not been committed with Save Changes.')
        expect(page.get_by_text('UNSAVED CHANGES',exact=True)).to_be_visible()
        assert digest(saved.directory)==saved_digest
        page.get_by_role('button',name='＋ New project',exact=True).click();ready(page)
        screenshot(page,output,'06-unsaved-change-warning')
        page.get_by_role('dialog').get_by_role('button',name='Cancel',exact=True).click();ready(page)
        expect(page.get_by_role('textbox',name='Executive summary',exact=True)).to_have_value('An edit that has not been committed with Save Changes.')
        page.get_by_role('button',name='＋ New project',exact=True).click();ready(page)
        page.get_by_role('dialog').get_by_role('button',name='Discard Project',exact=True).click();ready(page)
        assert digest(saved.directory)==saved_digest
        expect(page.get_by_role('combobox',name='Analysis table',exact=True)).to_have_count(0)
        navigate(page,'Workspace Home')
        card=page.locator('.st-key-ux-project-'+metadata(saved)['id'])
        card.get_by_role('button',name='Open',exact=True).click();ready(page)
        expect(page.get_by_role('textbox',name='Executive summary',exact=True)).to_have_value('Synthetic software validation only. No business claims.')
        expect(page.get_by_role('combobox',name='Analysis table',exact=True)).to_have_value(source.name+' · 12 rows')
        events.append('Explicit Save publishes data, evidence, investigation, report draft and editable fields; reopening restores them; discarded edits never change the saved snapshot')
        fill(page,'Executive summary','An explicit Save Changes edit.')
        page.get_by_role('button',name='Save Changes',exact=True).click();ready(page)
        assert digest(saved.directory)!=saved_digest
        assert saved.saved_objects('layout')['ux.report:Synthetic lifecycle report']['sections']['executive_summary']=='An explicit Save Changes edit.'
        expect(page.get_by_text('ALL CHANGES SAVED',exact=True)).to_be_visible()
        page.get_by_role('button',name='Delete',exact=True).click();ready(page)
        screenshot(page,output,'07-delete-confirmation')
        page.get_by_role('dialog').get_by_role('button',name='Cancel',exact=True).click();ready(page)
        assert saved.database.exists()
        page.get_by_role('button',name='Delete',exact=True).click();ready(page)
        page.get_by_role('dialog').get_by_role('button',name='Confirm Delete',exact=True).click();ready(page)
        assert not saved.database.exists() and len(catalog(root))==1
        assert digest(legacy.directory)==original
        assert hashlib.sha256(source.read_bytes()).hexdigest()==source_hash
        screenshot(page,output,'08-deleted-unrelated-project-preserved')
        for name in ['Workspace Home','Data Hub','Quality & Preparation','Explore','Investigate','Reports','Overview','AI Analyst','SQL Lab','Advanced']:
            navigate(page,name);unobscured(page)
        page.set_viewport_size({'width':390,'height':844});ready(page)
        screenshot(page,output,'09-mobile-save-actions')
        events.append('Save Changes is explicit; delete confirmation/cancel works; unrelated legacy project and original upload remain byte-for-byte intact; all wheel destinations still navigate')
        assert not errors,errors
        browser.close()
    result={'passed':True,'events':events,'browser_errors':errors,'native_dialogs':dialogs,'synthetic_fixture':True,'no_user_projects_modified':True}
    (output/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output.resolve())
