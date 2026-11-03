"""Real Chromium acceptance on explicitly synthetic references, never real-data findings.

Prepare with scripts/prepare_browser_fixtures.py; run the app with the printed
environment settings, then supply --url. Requires optional Playwright + Chromium.
"""
import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--url', required=True)
parser.add_argument('--chromium', default='/usr/bin/chromium')
parser.add_argument('--output', type=Path, default=Path('outputs/multi-browser'))
args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
checks = []

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(executable_path=args.chromium, headless=True, args=['--no-sandbox'])
    page = browser.new_page(viewport={'width': 1512, 'height': 1050})
    errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
    response = page.goto(args.url, wait_until='domcontentloaded', timeout=60000)
    workspace = page.get_by_role('combobox', name='Dataset workspace', exact=True)

    def choose_workspace(name):
        workspace.click(); page.get_by_role('option', name=name, exact=True).click()

    def settled():
        expect(page.get_by_test_id('stStatusWidget')).not_to_be_visible(timeout=60000)
        assert not page.get_by_test_id('stException').count()
        assert not page.get_by_test_id('stAlert').filter(has_text='This dataset view could not finish').count()

    def selected_count(count):
        page.get_by_text(f'Selected cohort · {count} of 8 imported complaints', exact=False).wait_for(timeout=60000)
        settled()

    choose_workspace('Customer Experience & Risk Intelligence')
    selected_count(8)
    expect(page.get_by_test_id('stMetricValue').first).to_have_text('8')
    expect(page.get_by_test_id('stMetricValue').nth(2)).to_have_text('66.7%')
    expect(page.get_by_test_id('stMetricValue').nth(3)).to_have_text('75.0%')
    page.get_by_text('SYNTHETIC DATA', exact=False).wait_for()
    expect(page.locator('[data-testid="stPlotlyChart"] .js-plotly-plot')).to_have_count(4)
    assert page.locator('[data-testid="stJson"]:visible').count() == 0
    page.screenshot(path=str(args.output / 'complaints-executive.png'))
    checks.append({'workspace': 'complaints', 'http_status': response.status, 'records': 8, 'timely_share': '66.7%', 'known_coverage': '75.0%', 'visible_json': 0})

    # Actual chart selection, checked against the hand-derived issue-A count.
    graph = page.locator('[data-testid="stPlotlyChart"] .js-plotly-plot').nth(2)
    bar = graph.locator('.barlayer .point path').nth(1)
    bar.scroll_into_view_if_needed(); box = bar.bounding_box()
    assert graph.evaluate('(node)=>node.data[0].customdata[1][0]') == 'A'
    page.mouse.move(box['x'] + 2, box['y'] - 2); page.mouse.down()
    page.mouse.move(box['x'] + box['width'] + 2, box['y'] + box['height'] + 2, steps=15); page.mouse.up()
    page.get_by_text('A · 3 matching records · page 1', exact=True).wait_for(timeout=60000)
    checks.append({'complaint_chart_box_selection': 'issue A', 'drilldown_records': 3})

    page.get_by_role('combobox', name='Company', exact=True).click()
    page.get_by_role('option', name='SYNTHETIC Alpha', exact=True).click(); page.keyboard.press('Escape')
    selected_count(3)
    expect(page.get_by_test_id('stMetricValue').first).to_have_text('3')
    page.get_by_text('Company Comparison', exact=True).click(); selected_count(3)
    page.get_by_text('Select a cohort containing at least two represented companies', exact=False).wait_for()
    page.get_by_role('button', name='Reset dataset filters', exact=True).click(); selected_count(8)
    page.locator('[data-testid="stPlotlyChart"] .js-plotly-plot').nth(1).wait_for(timeout=60000)
    settled(); page.screenshot(path=str(args.output / 'complaints-companies.png'))
    checks.append({'company_filter': 'SYNTHETIC Alpha', 'selected_records': 3, 'reset_records': 8, 'company_comparison_rendered': True})

    page.get_by_text('Root-Cause Investigation', exact=True).click(); selected_count(8)
    expect(page.locator('[data-testid="stPlotlyChart"] .js-plotly-plot')).to_have_count(3)
    page.get_by_role('heading', name='Root-Cause Investigation', exact=True).scroll_into_view_if_needed()
    page.screenshot(path=str(args.output / 'complaints-investigation.png'))
    checks.append({'complaint_investigation': 'heatmap, equal-period changes, response stacks'})

    page.get_by_text('AI Business Analyst', exact=True).click(); selected_count(8)
    page.get_by_role('button', name='Run business investigation', exact=True).click()
    page.get_by_test_id('stChatMessage').last.get_by_text('largest reported volume', exact=False).wait_for(timeout=60000)
    assert page.locator('[data-testid="stJson"]:visible').count() == 0
    page.get_by_role('heading', name='AI Business Analyst', exact=True).scroll_into_view_if_needed()
    page.screenshot(path=str(args.output / 'complaints-analyst.png'))
    checks.append({'complaint_guided_sql': 'executed and rendered', 'visible_json': 0})

    choose_workspace('Analyze My Dataset')
    page.get_by_text('SYNTHETIC reference sales', exact=False).wait_for(timeout=60000)
    expect(page.get_by_test_id('stMetricValue').first).to_have_text('4', timeout=60000)
    expect(page.get_by_test_id('stMetricValue').nth(1)).to_have_text('35.00')
    settled(); page.screenshot(path=str(args.output / 'custom-overview.png'))
    checks.append({'workspace': 'custom', 'records': 4, 'mapped_numeric_total': 35, 'old_complaint_messages': page.get_by_test_id('stChatMessage').count()})

    choose_workspace('Procurement Intelligence')
    page.get_by_text('Executive Overview', exact=True).first.wait_for(timeout=60000)
    expect(page.get_by_test_id('stMetricValue').first).to_have_text('1,200', timeout=60000)
    settled()
    choose_workspace('Customer Experience & Risk Intelligence'); selected_count(8)
    expect(page.get_by_test_id('stChatMessage')).to_have_count(0)
    checks.append({'dataset_switching': 'complaints → custom → procurement → complaints', 'stale_messages': 0})
    choose_workspace('Choose a workspace')
    page.get_by_role('heading', name='InsightPilot — AI Business Intelligence', exact=True).wait_for(timeout=60000)
    page.screenshot(path=str(args.output / 'workspace-selector.png'))
    assert not errors, errors
    report = {'scope': 'ALL RECORDS ARE SYNTHETIC SOFTWARE REFERENCES. No genuine CFPB extract was available.', 'checks': checks, 'uncaught_browser_errors': errors}
    (args.output / 'workspace-browser-verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2)); browser.close()
