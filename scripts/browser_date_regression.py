"""Browser acceptance for the reported CFPB date/donut defects.

Uses SYNTHETIC records with the actual timestamp syntax, NOT the user's 81,915
records. Start the app with prepare_browser_fixtures.py's printed settings.
"""
import argparse
import json
from pathlib import Path
import re

from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--url', required=True)
parser.add_argument('--chromium', default='/usr/bin/chromium')
parser.add_argument('--output', type=Path, default=Path('outputs/date-regression'))
args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
checks = []

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=args.chromium, args=['--no-sandbox'], headless=True)
    page = browser.new_page(viewport={'width': 1280, 'height': 800})
    errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(args.url, timeout=60000)
    page.get_by_role('combobox', name='Dataset workspace', exact=True).click()
    page.get_by_role('option', name='Customer Experience & Risk Intelligence', exact=True).click()
    expect(page.get_by_test_id('stMetricValue').first).to_have_text('8', timeout=60000)

    def idle():
        expect(page.get_by_test_id('stStatusWidget')).not_to_be_visible(timeout=60000)
        assert not page.get_by_test_id('stException').count()

    def upload(path):
        summary = page.get_by_text('Load another complaint snapshot', exact=True)
        if summary.locator('xpath=ancestor::details').get_attribute('open') is None: summary.click()
        page.get_by_test_id('stFileUploader').filter(has_text='CFPB CSV export').locator('input[type="file"]').set_input_files(str(path))
        box = page.get_by_role('checkbox', name='This complaint file is synthetic test data', exact=True)
        if not box.is_checked(): page.get_by_text('This complaint file is synthetic test data', exact=True).click()
        page.get_by_role('button', name='Import complaint CSV', exact=True).click()

    upload(Path('data/browser-fixtures/date-regression-v2/SYNTHETIC-cfpb-iso.csv'))
    expect(page.get_by_test_id('stMetricValue').first).to_have_text('1,000', timeout=60000)
    idle()
    summary = page.get_by_text('Load another complaint snapshot', exact=True)
    if summary.locator('xpath=ancestor::details').get_attribute('open') is not None: summary.click()
    page.get_by_text('0 missing · 0 invalid', exact=False).wait_for()
    graphs = page.locator('[data-testid="stPlotlyChart"] .js-plotly-plot')
    expect(graphs).to_have_count(4)
    trend = graphs.nth(0); pie = graphs.nth(1)
    assert trend.evaluate('(e)=>Array.from(e._fullData[0].y)') == [168,168,166,166,166,166]
    assert pie.evaluate('(e)=>Array.from(e._fullData[0].values)') == [600,180,90,60,50,20]
    assert pie.evaluate('(e)=>e._fullData[0].customdata.map(row=>row[1])') == [.6,.18,.09,.06,.05,.02]
    checks.append({'csv_import_exact_timestamp_format': True, 'records': 1000, 'dated_records': 1000,
                   'independent_daily_counts': [168,168,166,166,166,166], 'donut_counts': [600,180,90,60,50,20]})

    for width, height in [(1280,800),(1440,900)]:
        page.set_viewport_size({'width':width,'height':height})
        pie.scroll_into_view_if_needed(); page.wait_for_timeout(400)
        geometry = pie.evaluate('''(e)=>{
            const root=e.getBoundingClientRect();
            const items=Array.from(e.querySelectorAll('.legend .legendtext')).map(node=>{
                const b=node.getBoundingClientRect();return {x:b.x-root.x,y:b.y-root.y,width:b.width,height:b.height};
            });
            return {width:root.width,height:root.height,items,arcLabels:Array.from(e.querySelectorAll('.slicetext')).filter(n=>n.textContent.trim()).length};
        }''')
        assert len(geometry['items']) == 6 and geometry['arcLabels'] == 0
        boxes = geometry['items']
        for index, box in enumerate(boxes):
            assert 0 <= box['x'] and box['x']+box['width'] <= geometry['width']+1, geometry
            assert 0 <= box['y'] and box['y']+box['height'] <= geometry['height']+1, geometry
            for other in boxes[index+1:]:
                overlap_x = min(box['x']+box['width'],other['x']+other['width'])-max(box['x'],other['x'])
                overlap_y = min(box['y']+box['height'],other['y']+other['height'])-max(box['y'],other['y'])
                assert overlap_x <= 0 or overlap_y <= 0, geometry
        assert page.evaluate('document.scrollingElement.scrollWidth <= window.innerWidth')
        pie.screenshot(path=str(args.output/f'SYNTHETIC-donut-{width}.png'))
        checks.append({'viewport':[width,height],'legend_items':6,'overlapping_labels':0,'clipped_labels':0,'horizontal_page_overflow':False})
    pie.locator('.slice path.surface').nth(5).hover()
    page.get_by_text('20 selected complaints', exact=False).wait_for(timeout=15000)
    pie.screenshot(path=str(args.output/'SYNTHETIC-donut-hover.png'))
    expect(pie.locator('.hoverlayer')).to_contain_text('2.00% of all selected complaints',timeout=15000)
    checks.append({'actual_hover': {'complaints':20,'share':'2.00%'}})

    # Filtering must update real SQL values and the rendered trend together.
    company = page.get_by_role('combobox', name='Company', exact=True)
    company.scroll_into_view_if_needed(); company.click()
    if company.get_attribute('aria-expanded') != 'true': company.press('ArrowDown')
    page.get_by_role('option', name='SYNTHETIC Alpha', exact=True).click(); page.keyboard.press('Escape')
    expect(page.get_by_test_id('stMetricValue').first).to_have_text('500', timeout=60000); idle()
    assert graphs.nth(0).evaluate('(e)=>Array.from(e._fullData[0].y)') == [84,84,83,83,83,83]
    page.get_by_text('Filter by record date', exact=True).click()
    idle()
    field = page.get_by_test_id('stDateInput').filter(has_text='Record date · inclusive bounds')
    # React Aria exposes visible calendar segments; the native form inputs are
    # hidden and are not a user interaction surface.
    days = field.get_by_role('spinbutton', name=re.compile(r'^day', re.I))
    days.nth(0).click(); days.nth(0).press('2')
    days.nth(1).click(); days.nth(1).press('4'); days.nth(1).press('Enter')
    page.get_by_role('heading', name='Executive Overview', exact=True).click()
    expect(page.get_by_test_id('stMetricValue').first).to_have_text('250', timeout=60000); idle()
    assert graphs.nth(0).evaluate('(e)=>Array.from(e._fullData[0].y)') == [84,83,83]
    checks.append({'company_filter_records':500,'company_and_date_filter_records':250,'filtered_daily_counts':[84,83,83]})
    page.get_by_role('heading', name='Executive Overview', exact=True).scroll_into_view_if_needed()
    page.screenshot(path=str(args.output/'SYNTHETIC-date-filtered.png'))

    # The same original-CSV reimport flow creates a new snapshot and fresh filters.
    upload(Path('tests/fixtures/SYNTHETIC-cfpb-timestamps.csv'))
    expect(page.get_by_test_id('stMetricValue').first).to_have_text('10', timeout=60000); idle()
    page.get_by_text('1 missing · 2 invalid', exact=False).wait_for()
    assert graphs.nth(0).evaluate('(e)=>Array.from(e._fullData[0].y)') == [2,1,1,1,1,1]
    page.get_by_text('3 records excluded from date trends', exact=False).wait_for()
    graphs.nth(0).screenshot(path=str(args.output/'SYNTHETIC-daily-trend.png'))
    page.get_by_role('heading', name='Executive Overview', exact=True).scroll_into_view_if_needed()
    page.screenshot(path=str(args.output/'SYNTHETIC-date-reimport.png'))
    checks.append({'original_csv_reimport': True,'records':10,'dated':7,'missing_dates':1,'invalid_dates':2,'stale_filters':False})
    assert not errors, errors
    report = {'scope':'SYNTHETIC regression records using the reported timestamp syntax. The user’s 81,915-row file was unavailable.',
              'checks':checks,'uncaught_browser_errors':errors}
    (args.output/'browser-date-regression.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2)); browser.close()
