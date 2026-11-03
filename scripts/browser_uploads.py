"""Upload/mapping acceptance in a real browser with explicitly synthetic files."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser(); parser.add_argument('--url', required=True)
parser.add_argument('--chromium', default='/usr/bin/chromium')
parser.add_argument('--output', type=Path, default=Path('outputs/multi-browser'))
args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
directory = Path('data/browser-fixtures/uploads'); checks = []

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=args.chromium, headless=True, args=['--no-sandbox'])
    page = browser.new_page(viewport={'width': 1512, 'height': 1050})
    errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(args.url, timeout=60000)
    page.get_by_role('combobox', name='Dataset workspace', exact=True).click()
    page.get_by_role('option', name='Analyze My Dataset', exact=True).click()
    page.get_by_text('SYNTHETIC reference sales', exact=False).wait_for(timeout=60000)
    def inspect(file):
        expander = page.get_by_text('Import another dataset', exact=True)
        if expander.locator('xpath=ancestor::details').get_attribute('open') is None: expander.click()
        page.get_by_test_id('stFileUploader').filter(has_text='Business dataset').locator('input[type="file"]').set_input_files(str(file))
        page.get_by_role('button', name='Inspect dataset', exact=True).click()
        page.get_by_role('heading', name='3 · Approve analytical field roles', exact=True).wait_for(timeout=60000)
        expect(page.get_by_test_id('stStatusWidget')).not_to_be_visible(timeout=60000)
    revenue_label = 'I confirm the mapped amount represents revenue, or quantity × unit price is a valid revenue calculation.'
    def checkbox(label, value=True):
        widget = page.get_by_role('checkbox', name=label, exact=True)
        if widget.is_checked() != value: page.get_by_text(label, exact=True).click()
        expect(widget).to_be_checked() if value else expect(widget).not_to_be_checked()
    def profile(name):
        combo = page.get_by_role('combobox', name='Analytical profile', exact=True)
        combo.scroll_into_view_if_needed()
        combo.locator('..').get_by_role('button', name='Open', exact=True).click()
        page.get_by_role('option', name=name, exact=True).click()
    for extension in ['csv', 'xlsx', 'parquet']:
        file = directory / ('SYNTHETIC-sales.' + extension)
        inspect(file)
        page.get_by_role('textbox', name='Dataset name', exact=True).fill('SYNTHETIC browser ' + extension)
        profile('Transactions')
        checkbox(revenue_label)
        checkbox('This dataset is synthetic demonstration data')
        page.get_by_role('button', name='Approve mapping and create workspace', exact=True).click()
        page.get_by_text('Active dataset · SYNTHETIC browser ' + extension, exact=False).wait_for(timeout=60000)
        expect(page.get_by_test_id('stMetricValue').first).to_have_text('4', timeout=60000)
        expect(page.get_by_test_id('stMetricValue').nth(1)).to_have_text('35.00', timeout=60000)
        expect(page.get_by_test_id('stStatusWidget')).not_to_be_visible(timeout=60000)
        assert not page.get_by_test_id('stException').count()
        checks.append({'format': extension, 'browser_upload': True, 'mapping_approved': True, 'records': 4, 'confirmed_revenue': 35, 'invalid_numeric_excluded': 1})
        print(json.dumps(checks[-1]), flush=True)
    page.get_by_role('heading', name='Analyze My Dataset', exact=True).scroll_into_view_if_needed()
    page.screenshot(path=str(args.output / 'custom-upload-verified.png'))
    inspect(directory / 'SYNTHETIC-events.csv')
    page.get_by_role('textbox', name='Dataset name', exact=True).fill('SYNTHETIC browser event log')
    profile('Process event log')
    checkbox('This dataset is synthetic demonstration data')
    # A previous profile's revenue confirmation must be explicitly cleared.
    checkbox(revenue_label, False)
    page.get_by_role('button', name='Approve mapping and create workspace', exact=True).click()
    page.get_by_text('Active dataset · SYNTHETIC browser event log', exact=False).wait_for(timeout=60000)
    expect(page.get_by_test_id('stMetricValue').first).to_have_text('2', timeout=60000)
    expect(page.get_by_test_id('stMetricValue').nth(1)).to_have_text('3')
    page.get_by_role('combobox', name='Cases containing activity', exact=True).click()
    page.get_by_role('option', name='Close', exact=True).click(); page.keyboard.press('Escape')
    expect(page.get_by_test_id('stMetricValue').first).to_have_text('1', timeout=60000)
    expect(page.get_by_test_id('stMetricValue').nth(1)).to_have_text('2')
    expect(page.get_by_test_id('stStatusWidget')).not_to_be_visible(timeout=60000)
    assert not page.get_by_test_id('stException').count()
    page.screenshot(path=str(args.output / 'custom-process-verified.png'))
    checks.append({'format': 'mapped event CSV', 'cases': 2, 'events': 3, 'activity_filter_cases': 1, 'activity_filter_events': 2})
    assert not errors, errors
    result = {'scope': 'SYNTHETIC SOFTWARE ACCEPTANCE ONLY', 'checks': checks, 'uncaught_browser_errors': errors}
    (args.output / 'upload-browser-verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2)); browser.close()
