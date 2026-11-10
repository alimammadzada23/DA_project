"""Run automatic analysis on public datasets, time it, and verify reported numbers independently.

Data files are read from outputs/public-datasets (or INSIGHTPILOT_PUBLIC_DATA); see
docs/AUTOMATIC_ANALYSIS.md for download commands. Each dataset is imported into its own
temporary workspace, so existing projects are never touched. Reported numbers are
compared with pandas/SciPy on the raw source file (or, for the 1M-row workbook, with
direct DuckDB SQL on the untouched original version).

  .venv/bin/python scripts/auto_analysis_datasets.py --output outputs/auto-analysis-datasets.json
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

import numpy as np
import pandas as pd
from scipy import stats

from insightpilot.universal import Workspace
from insightpilot.universal import auto_analysis as aa
from insightpilot.universal.auto_report import excel_report, html_report
from insightpilot.universal.excel_stream import ALL_SHEETS

ROOT = Path(__file__).parents[1]
DATA = Path(os.environ.get('INSIGHTPILOT_PUBLIC_DATA', ROOT / 'outputs/public-datasets'))
ADULT_COLUMNS = ['age', 'workclass', 'fnlwgt', 'education', 'education_num', 'marital_status', 'occupation', 'relationship', 'race',
                 'sex', 'capital_gain', 'capital_loss', 'hours_per_week', 'native_country', 'income']
COVER_COLUMNS = (['Elevation', 'Aspect', 'Slope', 'Horizontal_Distance_To_Hydrology', 'Vertical_Distance_To_Hydrology',
                  'Horizontal_Distance_To_Roadways', 'Hillshade_9am', 'Hillshade_Noon', 'Hillshade_3pm', 'Horizontal_Distance_To_Fire_Points']
                 + [f'Wilderness_Area{i}' for i in range(1, 5)] + [f'Soil_Type{i}' for i in range(1, 41)] + ['Cover_Type'])


def item(output, item_id):
    return next(i for i in aa.iter_items(output) if i.get('id') == item_id)


def table(output, item_id):
    found = item(output, item_id)
    return pd.DataFrame(found['rows'], columns=[c['name'] for c in found['columns']])


def check(checks, label, reported, expected, tolerance=1e-9):
    if isinstance(expected, (int, float, np.number)) and reported is not None:
        match = bool(np.isclose(float(reported), float(expected), rtol=tolerance, atol=tolerance))
    else:
        match = reported == expected
    checks.append({'check': label, 'reported': reported, 'independent': expected if not isinstance(expected, np.generic) else expected.item(),
                   'match': match})


def rate_check(checks, output, field, category, expected, label):
    rates = table(output, aa.item_id(field, 'rates')).set_index('Category')['Positive rate']
    check(checks, label, rates[category], expected)


# ---------------- datasets ----------------
def telco(workspace, source):
    path = Path(os.environ.get('INSIGHTPILOT_TELCO_CSV', Path.home() / 'Downloads/telco_churn.csv'))
    if not path.is_file(): return None
    workspace.add_file(path, 'telco')
    return {'name': 'IBM Telco Customer Churn', 'source': 'https://github.com/IBM/telco-customer-churn-on-icp4d (local copy)',
            'table': 'telco', 'kwargs': dict(target='Churn', target_type='binary', positive='Yes'), 'raw': pd.read_csv(path, dtype=str)}


def verify_telco(output, raw, checks):
    rate = (raw.Churn == 'Yes').mean()
    summary = table(output, 'target-baseline').set_index('Measure')
    check(checks, 'Churn positive rate', summary.iloc[1]['Share'], rate)
    check(checks, 'Baseline accuracy', summary.loc['Accuracy of always predicting the majority class', 'Share'], 1 - rate)
    for field in ('Contract', 'InternetService', 'PaymentMethod'):
        for category, value in raw.groupby(field).Churn.apply(lambda s: (s == 'Yes').mean()).items():
            rate_check(checks, output, field, category, value, f'Churn rate, {field} = {category}')
    for field in ('tenure', 'MonthlyCharges'):
        medians = table(output, aa.item_id(field, 'summary')).set_index('Class').Median
        for label, value in raw.assign(v=raw[field].astype(float)).groupby('Churn').v.median().items():
            check(checks, f'Median {field}, Churn = {label}', medians[label], value)
    blank = raw.TotalCharges.str.strip() == ''
    patterns = table(output, 'missing-patterns')
    tenure = patterns[(patterns['Column with missing values'] == 'TotalCharges') & (patterns['Other column'] == 'tenure')].iloc[0]
    check(checks, 'TotalCharges missing rows', int(tenure['Missing rows']), int(blank.sum()))
    check(checks, 'All missing TotalCharges have tenure = 0', tenure['Value in every missing row'], '0')
    check(checks, 'tenure = 0 rows in total', int(tenure['Rows with that value in total']), int((raw.tenure == '0').sum()))
    check(checks, 'customerID skipped as identifier', dict(output['skipped']).get('customerID', '').startswith('identifier'), True)


def adult(workspace, source):
    path = DATA / 'adult/adult.data'
    if not path.is_file(): return None
    copy = source / 'adult.csv'; shutil.copyfile(path, copy)
    workspace.add_file(copy, 'adult', column_names=ADULT_COLUMNS)
    raw = pd.read_csv(path, header=None, names=ADULT_COLUMNS, skipinitialspace=True, dtype=str, keep_default_na=False)
    return {'name': 'UCI Adult (census income)', 'source': 'https://archive.ics.uci.edu/dataset/2/adult', 'table': 'adult',
            'kwargs': dict(target='income', target_type='binary', positive='>50K'), 'raw': raw}


def verify_adult(output, raw, checks):
    positive = raw.income == '>50K'
    check(checks, 'Positive rate (income >50K)', table(output, 'target-baseline').iloc[1]['Share'], positive.mean())
    problems = table(output, 'quality-problems')
    for column in ('workclass', 'occupation', 'native_country'):
        reported = problems[(problems.Column == column) & (problems.Issue == 'NA-like markers')].Rows
        check(checks, f"'?' markers in {column}", int(reported.iloc[0]), int((raw[column] == '?').sum()))
    for category, value in raw.assign(y=positive).groupby('sex').y.mean().items():
        rate_check(checks, output, 'sex', category, value, f'Positive rate, sex = {category}')
    test = table(output, aa.item_id('sex', 'test')).iloc[0]
    chi = stats.chi2_contingency(pd.crosstab(raw.sex, positive), correction=False)
    check(checks, 'Chi-square sex × income', test['Statistic'], chi.statistic, 1e-6)
    age = raw.age.astype(float)
    expected = stats.mannwhitneyu(age[positive], age[~positive], method='asymptotic')
    test = table(output, aa.item_id('age', 'test')).iloc[0]
    check(checks, 'Mann–Whitney U, age', test['Statistic'], expected.statistic)
    check(checks, 'Mann–Whitney p, age', test['p-value'], expected.pvalue, 1e-6)
    medians = table(output, aa.item_id('age', 'summary')).set_index('Class').Median
    check(checks, 'Median age, income >50K', medians['>50K'], age[positive].median())
    countries = item(output, aa.item_id('native_country', 'rates'))
    check(checks, 'native_country grouped into top 30 + other + missing', len(countries['rows']), 32)


def penguins(workspace, source):
    path = DATA / 'penguins/penguins.csv'
    if not path.is_file(): return None
    workspace.add_file(path, 'penguins')
    return {'name': 'Palmer Penguins', 'source': 'https://github.com/allisonhorst/palmerpenguins (CC0)', 'table': 'penguins',
            'kwargs': dict(target='species', target_type='multiclass'), 'raw': pd.read_csv(path, dtype=str, keep_default_na=False)}


def verify_penguins(output, raw, checks):
    classes = table(output, 'target-classes').set_index('Class').Rows
    for species, n in raw.species.value_counts().items():
        check(checks, f'Rows, species = {species}', classes[species], n)
    check(checks, "'NA' markers in sex", int(table(output, 'quality-problems').query("Column == 'sex' and Issue == 'NA-like markers'").Rows.iloc[0]),
          int((raw.sex == 'NA').sum()))
    flipper = raw[raw.flipper_length_mm != 'NA'].assign(v=lambda d: d.flipper_length_mm.astype(float))
    expected = stats.kruskal(*[g.v for _, g in flipper.groupby('species')])
    test = table(output, aa.item_id('flipper_length_mm', 'test')).iloc[0]
    check(checks, 'Kruskal–Wallis H, flipper length', test['Statistic'], expected.statistic, 1e-9)
    check(checks, 'Epsilon-squared, flipper length', test['Effect size'], expected.statistic / (len(flipper) - 1), 1e-9)
    chi = stats.chi2_contingency(pd.crosstab(raw.island, raw.species), correction=False)
    check(checks, 'Chi-square island × species', table(output, aa.item_id('island', 'test')).iloc[0]['Statistic'], chi.statistic, 1e-9)


def bike(workspace, source):
    path = DATA / 'bike/hour.csv'
    if not path.is_file(): return None
    workspace.add_file(path, 'bike')
    return {'name': 'UCI Bike Sharing (hourly)', 'source': 'https://archive.ics.uci.edu/dataset/275/bike+sharing+dataset', 'table': 'bike',
            'kwargs': dict(target='cnt', target_type='numeric', time_axis='dteday'), 'raw': pd.read_csv(path)}


def verify_bike(output, raw, checks):
    summary = table(output, 'target-summary').iloc[0]
    check(checks, 'Median cnt', summary['median'], raw.cnt.median())
    check(checks, 'Mean cnt', summary['mean'], raw.cnt.mean())
    test = table(output, aa.item_id('temp', 'test')).set_index('Test')
    expected = stats.spearmanr(raw.temp, raw.cnt)
    check(checks, 'Spearman ρ temp × cnt', test.loc['Spearman rank correlation', 'Statistic'], expected.statistic, 1e-9)
    hours = table(output, aa.item_id('hr', 'test')).set_index('Test')
    check(checks, 'Spearman ρ hr × cnt', hours.loc['Spearman rank correlation', 'Statistic'], stats.spearmanr(raw.hr, raw.cnt).statistic, 1e-9)
    periods = table(output, 'dteday-periods-time-target')
    # A 730-day range is grouped by month under the documented rule.
    expected = raw.groupby(pd.to_datetime(raw.dteday).dt.to_period('M')).cnt.median()
    check(checks, 'Median cnt per month (all 24 months)', [float(v) for v in periods['Median cnt']], [float(v) for v in expected])
    check(checks, 'instant skipped as identifier', dict(output['skipped']).get('instant', '').startswith('identifier'), True)


def covertype(workspace, source):
    path = DATA / 'covertype/covtype.data'
    if not path.is_file(): return None
    copy = source / 'covtype.csv'; shutil.copyfile(path, copy)
    workspace.add_file(copy, 'covertype', column_names=COVER_COLUMNS)
    return {'name': 'UCI Covertype', 'source': 'https://archive.ics.uci.edu/dataset/31/covertype', 'table': 'covertype',
            'kwargs': dict(target='Cover_Type', target_type='multiclass'), 'raw': pd.read_csv(path, header=None, names=COVER_COLUMNS)}


def verify_covertype(output, raw, checks):
    classes = table(output, 'target-classes').set_index('Class').Rows
    for label, n in raw.Cover_Type.value_counts().items():
        check(checks, f'Rows, Cover_Type = {label}', classes[str(label)], n)
    expected = stats.kruskal(*[g.Elevation for _, g in raw.groupby('Cover_Type')])
    check(checks, 'Kruskal–Wallis H, Elevation', table(output, aa.item_id('Elevation', 'test')).iloc[0]['Statistic'], expected.statistic, 1e-9)
    chi = stats.chi2_contingency(pd.crosstab(raw.Wilderness_Area1, raw.Cover_Type), correction=False)
    check(checks, 'Chi-square Wilderness_Area1 × Cover_Type', table(output, aa.item_id('Wilderness_Area1', 'test')).iloc[0]['Statistic'], chi.statistic, 1e-9)
    check(checks, 'Columns analysed', len(output['sections']['fields']) + len(output['skipped']), 54)


def retail(workspace, source):
    path = DATA / 'retail/online_retail_II.xlsx'
    if not path.is_file(): return None
    workspace.add_file(path, 'retail', sheet=ALL_SHEETS)
    return {'name': 'UCI Online Retail II', 'source': 'https://archive.ics.uci.edu/dataset/502/online+retail+ii', 'table': 'retail',
            'kwargs': dict(time_axis='InvoiceDate'), 'raw': None}


def verify_retail(output, raw, checks, workspace=None):
    original = output['original_version']
    with workspace.connect() as con:
        total, countries = con.execute(f'SELECT count(*),count(DISTINCT Country) FROM "{original}"').fetchone()
        uk = con.execute(f"SELECT count(*) FROM \"{original}\" WHERE Country='United Kingdom'").fetchone()[0]
        missing_customer = con.execute(f'SELECT count(*) FROM "{original}" WHERE "Customer ID" IS NULL').fetchone()[0]
        # A 738-day range is grouped by quarter under the documented rule.
        quarters = con.execute(f'SELECT count(*) FROM "{original}" GROUP BY date_trunc(\'quarter\',CAST(InvoiceDate AS TIMESTAMP)) ORDER BY 1 DESC LIMIT 1').fetchone()[0]
    check(checks, 'Rows (both sheets)', output['rows'], total)
    country = table(output, aa.item_id('Country', 'counts')).set_index('Category')
    check(checks, 'Rows, Country = United Kingdom', country.loc['United Kingdom', 'Rows'], uk)
    check(checks, 'Country categories (30 shown + other)', len(country) - 1 + int(country.index[-1].split()[1]), countries)
    missing = table(output, 'quality-missing').set_index('Column')
    check(checks, 'Missing Customer ID', missing.loc['Customer ID', 'Rows'], missing_customer)
    periods = table(output, 'invoicedate-periods-time')
    check(checks, 'Largest quarter row count', int(periods.Rows.max()), quarters)
    check(checks, 'Rows over all quarters', int(periods.Rows.sum()), total)


DATASETS = [('telco', telco, verify_telco), ('adult', adult, verify_adult), ('penguins', penguins, verify_penguins),
            ('bike', bike, verify_bike), ('covertype', covertype, verify_covertype), ('retail', retail, verify_retail)]


def run_dataset(key, loader, verify, folder):
    workspace = Workspace.create(folder / 'workspace'); timings = {}
    started = time.perf_counter(); spec = loader(workspace, folder)
    if spec is None: return None
    timings['import'] = time.perf_counter() - started
    started = time.perf_counter(); prepared = aa.prepare(workspace, spec['table']); timings['profile'] = time.perf_counter() - started
    started = time.perf_counter()
    output = aa.run(workspace, spec['table'], prepared=prepared, **spec['kwargs']); timings['analysis'] = time.perf_counter() - started
    started = time.perf_counter(); html = html_report(output); timings['html'] = time.perf_counter() - started
    started = time.perf_counter(); excel = excel_report(output); timings['excel'] = time.perf_counter() - started
    checks = []
    if key == 'retail': verify(output, None, checks, workspace)
    else: verify(output, spec['raw'], checks)
    info = workspace.info(spec['table'])
    return {'dataset': spec['name'], 'source': spec['source'], 'rows': info['rows'], 'columns': len(info['columns']),
            'settings': {k: v for k, v in spec['kwargs'].items()}, 'stages': {s['key']: s['status'] for s in output['stages']},
            'fields_analysed': len(output['sections'].get('fields', [])), 'skipped': output['skipped'], 'failures': output['field_failures'],
            'sampling': output['sampling'], 'seconds': {k: round(v, 2) for k, v in timings.items()},
            'stage_seconds': {s['key']: s['seconds'] for s in output['stages']}, 'html_bytes': len(html), 'excel_bytes': len(excel),
            'checks': checks, 'all_checks_match': all(c['match'] for c in checks)}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path); parser.add_argument('--only', nargs='*')
    args = parser.parse_args(); results = []
    for key, loader, verify in DATASETS:
        if args.only and key not in args.only: continue
        with tempfile.TemporaryDirectory(prefix=f'auto-{key}-') as folder:
            result = run_dataset(key, loader, verify, Path(folder))
        if result is None:
            print(f'{key}: data file not found, skipped', flush=True); continue
        results.append(result)
        failed = [c['check'] for c in result['checks'] if not c['match']]
        print(f'{key}: {result["rows"]:,} rows × {result["columns"]} columns · import {result["seconds"]["import"]}s · profile '
              f'{result["seconds"]["profile"]}s · analysis {result["seconds"]["analysis"]}s · checks {len(result["checks"]) - len(failed)}/{len(result["checks"])}'
              + (f' · MISMATCH: {failed}' if failed else ''), flush=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2, default=str) + '\n')
    return 0 if all(r['all_checks_match'] and all(s != 'failed' for s in r['stages'].values()) for r in results) else 1


if __name__ == '__main__':
    sys.exit(main())
