"""Time imports and real Streamlit pages on a large generated table.

Everything runs in a new temporary folder and is deleted afterwards; existing
projects are never read. All data is synthetic. Totals are checked against values
known from the generator, so a fast wrong answer fails the run. Timings are
server-side page runs (AppTest), without browser rendering.

  .venv/bin/python scripts/benchmark_large.py --rows 30000000 --output outputs/large.json
"""
import argparse
import json
import os
from pathlib import Path
import platform
import resource
import sys
import tempfile
import time

import duckdb
from streamlit.testing.v1 import AppTest

from insightpilot.engine_settings import memory_limit, thread_count
from insightpilot.universal import Workspace
from insightpilot.universal.dax import Field, Model, evaluate
from insightpilot.universal.engine import Engine
from insightpilot.universal.file_readers import scan_source
from insightpilot.universal.metrics import Metric
from insightpilot.universal.projects import ProjectSession
from insightpilot.universal.sql_lab import SQLLab
from insightpilot.universal.ui_workflow import PAGES

APP = str(Path(__file__).parents[1] / 'app.py')
SELECT = '''SELECT i AS trip_id,'zone-'||(i%250) AS zone,'vendor-'||(i%4) AS vendor,
    (DATE '2023-01-01'+(i%365)::INTEGER)::VARCHAR AS trip_date,
    (i%100+1)::DOUBLE AS fare,(i%5+1)::BIGINT AS passengers,
    CASE WHEN i%20=0 THEN NULL ELSE 'pay-'||(i%6) END AS payment FROM range({start},{stop}) t(i)'''


def timed(results, label, action):
    started = time.perf_counter()
    try:
        value = action()
    except Exception as exc:
        results[label] = {'seconds': round(time.perf_counter() - started, 2), 'error': f'{type(exc).__name__}: {str(exc)[:300]}'}
        print(f'{label}: FAILED after {results[label]["seconds"]}s: {results[label]["error"]}', flush=True)
        return None
    results[label] = round(time.perf_counter() - started, 2)
    print(f'{label}: {results[label]}s', flush=True)
    return value


def generate(directory, rows, files):
    """Parquet files (typed, like taxi data) and one CSV of the same rows (text, like Land Registry)."""
    parquet = directory / 'monthly'; parquet.mkdir()
    step = rows // files
    with duckdb.connect() as con:
        for index in range(files):
            stop = rows if index == files - 1 else (index + 1) * step
            query = SELECT.format(start=index * step, stop=stop)
            con.execute(f"COPY ({query}) TO '{parquet}/part-{index + 1:02d}.parquet' (FORMAT parquet)")
        con.execute(f"COPY ({SELECT.format(start=0, stop=rows)}) TO '{directory}/all.csv' (HEADER)")
    return parquet, directory / 'all.csv'


def expected_fare(rows):
    return (rows // 100) * 5050 + sum(range(1, rows % 100 + 1))


def check_engine(workspace, table, rows, results, prefix):
    metric = Metric('Fare', 'sum("fare")', 'Synthetic fare total', 'synthetic units')
    engine = Engine(workspace, table)
    total = expected_fare(rows)
    assert timed(results, prefix + 'count', engine.count) == rows
    value = timed(results, prefix + 'sum metric', lambda: engine.metric(metric))
    assert value is None or value['rows'][0][0] == total, 'sum metric mismatch'
    grouped = timed(results, prefix + 'group by zone (top 100)', lambda: engine.groups('zone', metric, top=100))
    assert grouped is None or len(grouped['rows']) == 100
    trend = timed(results, prefix + 'monthly trend', lambda: engine.trend('trip_date', metric))
    assert trend is None or sum(row[1] for row in trend['rows']) == total, 'trend mismatch'
    timed(results, prefix + 'histogram', lambda: engine.distribution('fare'))
    timed(results, prefix + 'numeric summary with median', lambda: engine.numeric_summary('fare'))
    timed(results, prefix + 'categories, low cardinality', lambda: engine.categories('payment'))
    timed(results, prefix + 'categories, every value unique', lambda: engine.categories('trip_id'))
    first = timed(results, prefix + 'records, first page', lambda: engine.records(0))
    assert first is None or len(first['rows']) == 100
    timed(results, prefix + 'records, deep page', lambda: engine.records(100_000))
    timed(results, prefix + 'scatter (first 2,000 rows)', lambda: engine.scatter('fare', 'passengers'))
    timed(results, prefix + 'filtered sum', lambda: Engine(workspace, table, []).run(
        "SELECT count(*) FROM selected WHERE zone='zone-7'"))


def check_studio(workspace, table, rows, results):
    """DAX compiles to correlated subqueries, the most expensive path in the app."""
    engine = Engine(workspace, table)
    model = Model.from_engine(engine, measures=[
        {'name': 'Total', 'expression': f'SUM({table}[fare])'},
        {'name': 'Share', 'expression': f'DIVIDE([Total], CALCULATE([Total], ALL({table}[vendor])))'},
        {'name': 'Last year', 'expression': "CALCULATE([Total], SAMEPERIODLASTYEAR('Date'[Date]))"},
        {'name': 'YTD', 'expression': "TOTALYTD([Total], 'Date'[Date])"},
        {'name': 'Rank', 'expression': f'RANKX(ALL({table}[vendor]), [Total])'}])
    total = timed(results, 'DAX: card, total', lambda: evaluate(engine, model, ['Total']))
    assert total is None or total['rows'][0][0] == expected_fare(rows), 'DAX total mismatch'
    vendor = [Field(table, 'vendor')]
    timed(results, 'DAX: total by vendor', lambda: evaluate(engine, model, ['Total'], vendor))
    timed(results, 'DAX: share of total by vendor', lambda: evaluate(engine, model, ['Share'], vendor))
    timed(results, 'DAX: rank by vendor', lambda: evaluate(engine, model, ['Rank'], vendor))
    months = [Field('Date', 'Month')]
    timed(results, 'DAX: total by month', lambda: evaluate(engine, model, ['Total'], months))
    timed(results, 'DAX: last year by month', lambda: evaluate(engine, model, ['Last year'], months))
    timed(results, 'DAX: YTD by month', lambda: evaluate(engine, model, ['YTD'], months))


def check_sql_lab(workspace, table, rows, results):
    lab = SQLLab(Engine(workspace, table))
    sql = f'SELECT zone,sum(fare) AS total FROM {table} GROUP BY zone ORDER BY total DESC'
    grouped = timed(results, 'SQL Lab: group by', lambda: lab.execute(sql, size=100))
    assert grouped is None or len(grouped['rows']) == 100
    timed(results, 'SQL Lab: first rows', lambda: lab.execute(f'SELECT * FROM {table}', size=100))
    timed(results, 'SQL Lab: sort whole table', lambda: lab.execute(f'SELECT * FROM {table} ORDER BY fare DESC, trip_id', size=100))
    timed(results, 'SQL Lab: distinct on unique id', lambda: lab.execute(f'SELECT count(DISTINCT trip_id) FROM {table}'))


def run_pages(root, session_directory, results):
    os.environ['INSIGHTPILOT_WORKSPACE_ROOT'] = str(root)
    os.environ['INSIGHTPILOT_UNIVERSAL_WORKSPACE'] = str(session_directory)
    app = timed(results, 'page: first app load', lambda: AppTest.from_file(APP, default_timeout=900).run())
    if app is None:
        return
    for page in PAGES:
        def visit(target=page):
            app.radio(key='universal-page').set_value(target).run()
            if app.exception:
                raise RuntimeError(app.exception[0].value)
        timed(results, 'page: ' + page, visit)
    def choose(label, change):
        def action():
            change()
            app.run()
            if app.exception:
                raise RuntimeError(app.exception[0].value)
        timed(results, label, action)

    def explore_chart(mode):
        app.selectbox(key='explore-chart-type').set_value(mode)
    choose('page: open Explore', lambda: app.radio(key='universal-page').set_value('Explore'))
    choose('Explore: Ranked groups, group by zone', lambda: (explore_chart('Ranked groups'), app.selectbox(key='explore-groupby').set_value('zone')))
    for mode in ('Numeric distribution', 'Numeric relationship', 'Calendar trend', 'Distribution by group',
                 'Two-dimensional counts', 'Composition & Pareto', 'Records'):
        choose('Explore: ' + mode, lambda mode=mode: explore_chart(mode))
    choose('Explore: Ranked groups again (repeat)', lambda: explore_chart('Ranked groups'))
    choose('page: open Data Hub', lambda: app.radio(key='universal-page').set_value('Data Hub'))
    choose('Data Hub: dataset library', lambda: next(item for item in app.radio if item.label == 'Data Hub view').set_value('Dataset library'))
    for page in PAGES:
        def again(target=page):
            app.radio(key='universal-page').set_value(target).run()
            if app.exception:
                raise RuntimeError(app.exception[0].value)
        timed(results, 'page again: ' + page, again)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rows', type=int, default=30_000_000)
    parser.add_argument('--files', type=int, default=12)
    parser.add_argument('--skip-csv', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    results = {}
    with tempfile.TemporaryDirectory(prefix='insightpilot-large-') as directory:
        root = Path(directory)
        parquet, csv_path = timed(results, 'generate synthetic files', lambda: generate(root, args.rows, args.files))
        sizes = {'parquet_bytes': sum(path.stat().st_size for path in parquet.iterdir()), 'csv_bytes': csv_path.stat().st_size}
        print(sizes, flush=True)
        catalog = root / 'catalog'
        session = ProjectSession.start(catalog)
        workspace = session.workspace
        info = timed(results, 'import folder of Parquet files', lambda: workspace.add_files(scan_source(parquet), 'trips', provenance={'origin': 'synthetic'}))
        assert info['rows'] == args.rows
        sizes['database_bytes_after_parquet'] = workspace.database.stat().st_size
        check_engine(workspace, 'trips', args.rows, results, 'typed: ')
        if not args.skip_csv:
            info = timed(results, 'import single CSV', lambda: workspace.add_file(csv_path, 'trips_text', provenance={'origin': 'synthetic'}))
            assert info['rows'] == args.rows
            sizes['database_bytes_after_csv'] = workspace.database.stat().st_size
            check_engine(workspace, 'trips_text', args.rows, results, 'text: ')
            timed(results, 'text: full profile (Quality page cold)', lambda: workspace.profile('trips_text'))
        timed(results, 'typed: full profile (Quality page cold)', lambda: workspace.profile('trips'))
        workspace.approve_semantics('trips', {'trip_date': 'date', 'zone': 'geography', 'fare': 'revenue', 'vendor': 'category'}, 'One synthetic trip',
                                    units={'fare': 'synthetic units'}, date_purposes={'trip_date': 'Trip date'}, date_basis='trip_date')
        check_studio(workspace, 'trips', args.rows, results)
        check_sql_lab(workspace, 'trips', args.rows, results)
        run_pages(catalog, session.directory, results)
        saved = timed(results, 'Save Project (full snapshot)', lambda: session.save('Large benchmark'))
        if saved is not None or session.target:
            timed(results, 'Open saved project (working copy)', lambda: ProjectSession.start(catalog, session.target))
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 ** 2 if platform.system() == 'Darwin' else 1024)
        report = {'scope': 'Synthetic data on this machine. Server-side timings, no browser rendering. OS file cache not cleared.',
                  'rows': args.rows, 'files': args.files, 'memory_limit': memory_limit(), 'threads': thread_count(),
                  'python': platform.python_version(), 'duckdb': duckdb.__version__, 'platform': platform.platform(),
                  'peak_process_rss_mib': round(peak), 'sizes': sizes, 'seconds': results}
    print(json.dumps(report, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n')
    failures = [label for label, value in results.items() if isinstance(value, dict)]
    print('Failed steps:', failures or 'none')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
