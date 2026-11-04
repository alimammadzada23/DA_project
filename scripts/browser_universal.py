"""Real Chromium validation against the local Streamlit app, never a public preview.

Launch with the verified flights workspace selected via environment variable.
"""
import argparse
import json
from pathlib import Path
import time
import tempfile
import uuid
import shutil
from datetime import datetime, timezone

from playwright.sync_api import expect, sync_playwright


def select(page,label,value):
    page.get_by_role('combobox',name=label,exact=True).click()
    page.get_by_role('option',name=value,exact=True).click()


def ready(page):
    expect(page.get_by_test_id('stStatusWidget')).not_to_be_visible(timeout=60000)
    expect(page.get_by_test_id('stException')).to_have_count(0)
    expect(page.get_by_test_id('stAlert').filter(has_text='could not finish')).to_have_count(0)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--url',default='http://127.0.0.1:8501')
    parser.add_argument('--chromium',default=shutil.which('chromium') or shutil.which('google-chrome'))
    parser.add_argument('--output',type=Path,default=Path('docs/screenshots/next-generation'))
    args=parser.parse_args(); args.output.mkdir(parents=True,exist_ok=True)
    Path('docs/next-generation-browser.json').write_text(json.dumps({'status':'in_progress',
        'started_at':datetime.now(timezone.utc).isoformat()})+'\n')
    checks=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=args.chromium,headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
        page=browser.new_page(viewport={'width':1512,'height':1050})
        errors=[]; page.on('pageerror',lambda e:errors.append(str(e)))
        response=page.goto(args.url,wait_until='domcontentloaded',timeout=60000)
        expect(page.get_by_role('button',name='Open Universal Intelligence',exact=True)).to_be_visible(timeout=60000)
        page.get_by_role('button',name='Open Universal Intelligence',exact=True).click()
        expect(page.get_by_role('heading',name='Import and organize related tables',exact=True)).to_be_visible(timeout=60000)
        ready(page); checks.append({'data_hub':True,'http_status':response.status})
        page.get_by_text('Data Quality',exact=True).click()
        expect(page.get_by_role('combobox',name='Analysis table',exact=True)).to_be_visible(timeout=60000)
        select(page,'Analysis table','flights')
        expect(page.get_by_role('heading',name='Understand every field',exact=True)).to_be_visible(timeout=60000)
        expect(page.get_by_text('336,776 rows · 21 fields · 0 exact duplicate rows retained',exact=True)).to_be_visible(timeout=60000)
        ready(page)
        page.screenshot(path=str(args.output/'flights-data-quality.png'))
        checks.append({'full_column_catalog':True,'source_rows':336776})
        page.get_by_text('Executive Intelligence',exact=True).click()
        expect(page.get_by_test_id('stMetricValue').first).to_have_text('336,776',timeout=60000)
        started=time.perf_counter()
        select(page,'Analysis metric','Mean recorded arrival delay')
        expect(page.get_by_test_id('stMetricValue').nth(1)).to_have_text('6.895',timeout=60000)
        expect(page.locator('[data-testid="stPlotlyChart"] .js-plotly-plot').first).to_be_visible(timeout=60000)
        page.wait_for_function("[...document.querySelectorAll('.js-plotly-plot')].some(el=>String(el.layout?.title?.text||'').includes('How does Mean recorded arrival delay change over time?'))",timeout=60000)
        page.wait_for_function("[...document.querySelectorAll('.js-plotly-plot')].some(el=>String(el.layout?.xaxis?.title?.text||'').includes('minutes'))",timeout=60000)
        page.locator('[data-testid="stPlotlyChart"] .js-plotly-plot').first.wait_for(timeout=60000)
        render_seconds=time.perf_counter()-started
        ready(page); page.screenshot(path=str(args.output/'flights-executive.png'))
        checks.append({'real_data_kpi':6.89537675731489,'charts_rendered':True,
                       'metric_selection_to_render_seconds':round(render_seconds,3)})
        plot=page.locator('[data-testid="stPlotlyChart"] .js-plotly-plot').first
        plot.hover()
        with page.expect_download(timeout=60000) as image_info:
            page.get_by_test_id('stPlotlyChart').first.get_by_role('button',name='Download plot as a PNG',exact=True).click()
        image_info.value.save_as(str(args.output/'flights-trend-chart.png'))
        assert (args.output/'flights-trend-chart.png').read_bytes().startswith(b'\x89PNG\r\n')
        checks.append({'plotly_png_download':True})
        page.get_by_text('Visual Explorer',exact=True).click()
        expect(page.get_by_role('combobox',name='Group by',exact=True)).to_be_visible(timeout=60000)
        select(page,'Group by','origin')
        ready(page)
        graph=page.locator('[data-testid="stPlotlyChart"] .js-plotly-plot').first
        expect(graph.locator('.barlayer .point')).to_have_count(3,timeout=60000)
        # Actual pointer box selection, not a synthetic JS selection event.
        page.get_by_test_id('stPlotlyChart').first.get_by_role('button',name='Box Select',exact=True).click()
        page.wait_for_function("document.querySelector('[data-testid=stPlotlyChart] .js-plotly-plot')?.layout?.dragmode==='select'",timeout=10000)
        bar=graph.locator('.barlayer .point path').first
        bar.scroll_into_view_if_needed(); box=bar.bounding_box()
        page.mouse.move(box['x']+2,box['y']-2); page.mouse.down()
        page.mouse.move(box['x']+box['width']+2,box['y']+box['height']+2,steps=15)
        page.mouse.up()
        expect(page.get_by_text('Chart selection: origin = EWR',exact=True)).to_be_visible(timeout=60000)
        expect(page.get_by_text('120,835 matching rows',exact=False)).to_be_visible(timeout=60000)
        ready(page); page.screenshot(path=str(args.output/'flights-drilldown.png'))
        checks.append({'chart_selection_filters_records':True,'pointer_box_selection':True,'selected_origin':'EWR','scope_preserved':True})
        page.get_by_text('AI Business Analyst',exact=True).click()
        expect(page.get_by_role('button',name='Calculate evidence-backed answer',exact=True)).to_be_visible(timeout=60000)
        page.get_by_role('button',name='Calculate evidence-backed answer',exact=True).click()
        expect(page.get_by_text('Deterministic guided analyst; no model or tokens used',exact=True)).to_be_visible(timeout=60000)
        ready(page); checks.append({'guided_analyst_executes':True,'external_model':False})
        page.get_by_text('Reports and Exports',exact=True).click()
        page.get_by_role('button',name='Build report from this selection',exact=True).click()
        download=page.get_by_role('button',name='Download standalone HTML report',exact=True)
        expect(download).to_be_visible(timeout=60000)
        with page.expect_download(timeout=60000) as info: download.click()
        artifact=info.value; artifact.save_as(str(args.output/'verified-report.html'))
        text=(args.output/'verified-report.html').read_text()
        assert 'source_sha256' in text and '6.89537675731489' in text and '<script>alert' not in text
        checks.append({'report_download':True,'numerical_evidence_and_source_hash':True})
        # Exercise browser file upload too; these records are explicitly synthetic
        # and are kept in separate tables from the genuine flight observations.
        page.get_by_text('Data Hub',exact=True).click()
        expect(page.get_by_role('button',name='Import tables',exact=True)).to_be_visible(timeout=60000)
        suffix=uuid.uuid4().hex[:8]
        order_name='SYNTHETIC_browser_orders_'+suffix
        customer_name='SYNTHETIC_browser_customers_'+suffix
        with tempfile.TemporaryDirectory(prefix='insightpilot-browser-') as directory:
            root=Path(directory); orders=root/(order_name+'.csv'); customers=root/(customer_name+'.csv')
            orders.write_text('date,customer_id,amount,region\n2025-01-01,A,10,East\n2025-01-02,A,20,East\n2025-02-01,B,30,West\n2025-02-02,B,40,West\n')
            customers.write_text('customer_id,segment\nA,SYNTHETIC One\nB,SYNTHETIC Two\n')
            page.get_by_test_id('stFileUploader').locator('input[type=file]').set_input_files([str(orders),str(customers)])
            page.get_by_text('These files are synthetic test data',exact=True).click()
            expect(page.get_by_role('checkbox',name='These files are synthetic test data',exact=True)).to_be_checked()
            page.get_by_role('button',name='Import tables',exact=True).click()
            expect(page.get_by_text(customer_name+': 2 rows imported.',exact=False)).to_be_visible(timeout=60000)
            page.get_by_text('Data Quality',exact=True).click()
            select(page,'Analysis table',customer_name)
            expect(page.get_by_text('2 rows · 2 fields · 0 exact duplicate rows retained',exact=True)).to_be_visible(timeout=60000)
            select(page,'Analysis table',order_name)
            expect(page.get_by_text('4 rows · 4 fields · 0 exact duplicate rows retained',exact=True)).to_be_visible(timeout=60000)
            expect(page.get_by_text('SYNTHETIC DATA · Software validation only; no genuine business findings are implied.',exact=True)).to_be_visible(timeout=60000)
            page.get_by_text('Executive Intelligence',exact=True).click()
            expect(page.get_by_test_id('stMetricValue').first).to_have_text('4',timeout=60000)
            page.get_by_text('Define a reusable metric',exact=True).click()
            page.get_by_role('textbox',name='Metric name',exact=True).fill('SYNTHETIC total '+suffix)
            page.get_by_role('textbox',name='Aggregate formula',exact=True).fill('sum("amount")')
            page.get_by_role('textbox',name='Metric definition / interpretation',exact=True).fill('Sum of explicitly synthetic uploaded amounts')
            page.get_by_role('textbox',name='Units or currency',exact=True).fill('synthetic units')
            page.get_by_role('button',name='Validate and save metric',exact=True).click()
            select(page,'Analysis metric','SYNTHETIC total '+suffix)
            expect(page.get_by_test_id('stMetricValue').nth(1)).to_have_text('100',timeout=60000)
            page.wait_for_function("[...document.querySelectorAll('.js-plotly-plot')].some(el=>String(el.layout?.xaxis?.title?.text||'').includes('synthetic units'))",timeout=60000)
            ready(page); page.screenshot(path=str(args.output/'SYNTHETIC-upload-overview.png'))
            checks.append({'browser_multi_file_import':True,'synthetic_orders':4,'synthetic_customers':2,
                           'explicit_metric_sum_verified':100,'synthetic_label_visible':True})
        page.set_viewport_size({'width':768,'height':1024})
        page.screenshot(path=str(args.output/'responsive-768.png'))
        overflow=page.evaluate('document.documentElement.scrollWidth > window.innerWidth + 2')
        assert not overflow,'Horizontal page overflow at 768px'
        assert not errors,errors
        report={'scope':'Actual Chromium on local Linux; authentic NYC flights plus explicitly labeled synthetic browser upload tests. No native Mac/Power BI/live-provider acceptance implied.',
            'status':'passed','validated_at':datetime.now(timezone.utc).isoformat(),
            'checks':checks,'uncaught_browser_errors':errors,'responsive_768px_no_page_overflow':True}
        Path('docs/next-generation-browser.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2)); browser.close()


if __name__=='__main__': main()
