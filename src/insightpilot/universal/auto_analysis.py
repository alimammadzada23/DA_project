"""Automatic analysis: deterministic profiling, safe corrections and field comparisons.

Every number comes from DuckDB aggregates or SciPy on those aggregates. Nothing is
interpreted: the run records tables, charts, the queries behind them and what each
stage did. Field types come from the data; column names are only hints for target
suggestions. No streamlit code lives here.
"""
from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import time
import uuid

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from ..datasets import quote
from . import auto_stats, visuals
from .auto import findings as auto_findings
from .auto import confounding as auto_confounding, leakage as auto_leakage, multivariate as auto_multivariate, segments as auto_segments
from .auto import quality as auto_quality, structure as auto_structure, timeseries as auto_time, univariate as auto_univariate
from .auto import textgeo as auto_textgeo, unsupervised as auto_unsupervised
from .auto.config import DEFAULT as DEFAULT_CONFIG
from .auto import stats as auto_stats_ci
from .auto.stats import bootstrap_interval
from scipy.stats import t as stats_t
from .context import AnalysisContext
from .engine import Engine, frame_of
from .metrics import Metric
from .profile import date_expression, numeric_expression, profile
from .store import literal

FORMAT_VERSION = 1
# Documented thresholds. They appear in every report's method section.
SETTINGS = {
    'category_limit': 30,            # categories shown per field; the rest are grouped as Other
    'small_group_rows': 30,          # groups with fewer rows are flagged
    'expected_count_minimum': 5,     # chi-square cells below this expected count are flagged
    'multiclass_maximum': 20,        # most classes a categorical target may have
    'imbalance_share': 0.10,         # smallest class share below this is noted
    'identifier_share': 0.99,        # distinct/nonmissing at or above this marks an identifier
    'near_constant_share': 0.99,     # most common value share at or above this is flagged
    'text_mean_length': 30,          # high-cardinality text with this mean length is free text
    'numeric_success': 0.99,         # share of values that must parse before a text column becomes numeric
    'extreme_iqr_multiple': 3,       # extreme values lie beyond quartiles ± this many IQRs
    'quantile_bins': 5,
    'median_bins': 10,
    'histogram_bins': 25,
    'scatter_sample_rows': 5000,
    'sample_seed': 42,
    'relationship_fields': 8,        # top-ranked fields compared with each other when a target exists
    'descriptive_relationship_fields': 12,
    'relationship_pairs_shown': 3,
    'missing_row_ids': 1000,
    'missing_pattern_columns': 50,
    'missing_patterns_per_column': 10,
    'variant_groups_per_column': 20,
    'duplicate_groups': 20,
}
NA_MARKERS = ('N/A', 'NA', 'NULL', '?', '-')
# Generic outcome-like name fragments. They raise a suggestion's rank but never decide it.
TARGET_HINTS = ('target', 'label', 'outcome', 'result', 'status', 'class', 'flag', 'churn', 'default', 'converted',
                'fraud', 'revenue', 'price', 'score')
POSITIVE_HINTS = ('yes', 'true', '1', '1.0', 'y', 't', 'positive')
MISSING_KEY, OTHER_KEY = '␞MISSING␞', '␞OTHER␞'
STAGES = [('profile', 'Data profile'), ('target_selection', 'Target selection'), ('corrections', 'Safe data corrections'), ('structure', 'Structure discovery'),
          ('target_overview', 'Target overview'), ('fields', 'Fields versus the target'), ('leakage', 'Leakage screen'), ('ranking', 'Ranking'), ('multivariate', 'Multivariate model'), ('confounding', 'Confounding and interactions'), ('segments', 'Segments'),
          ('unsupervised', 'Clusters and unusual rows'), ('relationships', 'Relationships between fields'), ('time', 'Time analysis'), ('findings', 'Findings digest'), ('evidence', 'Saved evidence')]
RUN_FOLDER = 'automatic_analysis'


def _clean(value):
    """JSON-safe values; floats keep 12 significant digits so repeated runs compare equal."""
    if isinstance(value, float):
        return None if not math.isfinite(value) else float(f'{value:.12g}')
    if isinstance(value, (datetime, date)): return value.isoformat()
    if hasattr(value, 'item') and not isinstance(value, (list, tuple, dict, str)):
        return _clean(value.item())
    return value


def deep_clean(value):
    """Plain JSON values throughout (numpy numbers become Python numbers with 12 significant digits)."""
    if isinstance(value, dict): return {k: deep_clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [deep_clean(v) for v in value]
    if isinstance(value, (bool, np.bool_)): return bool(value)
    return _clean(value)


def table(item_id, title, columns, rows, *, evidence, note=None):
    """columns: (name, kind) with kind in text, count, percent, number, p, effect."""
    return {'type': 'table', 'id': item_id, 'title': title, 'columns': [{'name': n, 'kind': k} for n, k in columns],
            'rows': [[_clean(v) for v in row] for row in rows], 'note': note, 'evidence': evidence}


def figure(item_id, title, fig, *, evidence):
    return {'type': 'figure', 'id': item_id, 'title': title, 'figure': fig.to_json(), 'evidence': evidence}


def text(value):
    return {'type': 'text', 'text': value}


def missing_sql(column):
    q = quote(column)
    return f"({q} IS NULL OR trim(CAST({q} AS VARCHAR))='')"


def marker_sql(column):
    return f"upper(trim(CAST({quote(column)} AS VARCHAR))) IN ({','.join(literal(m) for m in NA_MARKERS)})"


def absent_sql(column):
    """Missing or an NA-like marker: no usable value."""
    return f'({missing_sql(column)} OR {marker_sql(column)})'


def category_order(value, count):
    """Categories by row count, then label; the grouped '(other)' and '(missing)' rows always last."""
    return (value == OTHER_KEY, value == MISSING_KEY, -count, str(value))


def category_label(value, other_count=0):
    if value == MISSING_KEY: return '(missing)'
    if value == OTHER_KEY: return f'(other: {other_count:,} categories)'
    return str(value)


class Analysis:
    def __init__(self, workspace, table_name, version):
        self.workspace, self.table, self.version = workspace, table_name, version
        self.engine = Engine(workspace, table_name, version=version, context=AnalysisContext())
        self.results = {}   # item id -> Engine result, for saving charts as evidence blocks

    def run(self, sql, computation, *, limit=50_000):
        result = self.engine.run(sql, limit=limit, method={'automatic_analysis': computation})
        if result['truncated']: raise ValueError(f'{computation}: more than {limit:,} result rows.')
        return result

    def frame(self, sql, computation):
        """Run a query whose size the caller has bounded, returning a DataFrame and its evidence record."""
        full = f'WITH selected AS (SELECT * FROM {quote(self.version)}) ' + sql
        with self.workspace.connect() as con:
            data = con.execute(full).fetchdf()
        return data, {'sql': full, 'parameters': [], 'version': self.version, 'method': {'automatic_analysis': computation}}

    def evidence(self, computation, *results):
        return {'version': self.version, 'computation': computation,
                'queries': [{'sql': r['sql'], 'parameters': [str(p) for p in r['parameters']]} for r in results]}

    # ---------- per-column facts ----------
    def facts(self):
        with self.workspace.connect() as con:
            report = profile(con, self.version)
            types = {r[0]: r[1] for r in con.execute(f'DESCRIBE {quote(self.version)}').fetchall() if r[0] != '_row_id'}
        rows = report['rows']; facts = {}; queries = []
        for item in report['columns']:
            name = item['column']; q = quote(name); dtype = types[name]
            value = f'trim(CAST({q} AS VARCHAR))'
            usable = f'NOT {absent_sql(name)}'
            x = numeric_expression(name)
            slash = f"regexp_matches({value},'^[0-9]{{1,2}}/[0-9]{{1,2}}/[0-9]{{4}}$')"
            result = self.run(f'''SELECT
                count(*) FILTER (WHERE {q} IS NOT NULL AND CAST({q} AS VARCHAR)<>{value}) AS padded,
                count(*) FILTER (WHERE {q} IS NOT NULL AND {value}='') AS blank,
                count(*) FILTER (WHERE {marker_sql(name)}) AS markers,
                count(DISTINCT CASE WHEN {usable} THEN {value} END) AS distinct_clean,
                avg(length({value})) FILTER (WHERE {usable}) AS mean_length,
                count(*) FILTER (WHERE {usable} AND regexp_matches({value},'^[+-]?0[0-9]')) AS leading_zero,
                count(*) FILTER (WHERE {usable} AND regexp_matches({value},'\\s')) AS spaced,
                count(*) FILTER (WHERE {usable} AND regexp_matches({value},'^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$')) AS iso_dates,
                count(*) FILTER (WHERE {usable} AND {slash}) AS slash_dates,
                count(*) FILTER (WHERE {usable} AND {slash} AND try_cast(split_part({value},'/',1) AS INTEGER)>12) AS first_over_12,
                count(*) FILTER (WHERE {usable} AND {slash} AND try_cast(split_part({value},'/',2) AS INTEGER)>12) AS second_over_12,
                count({x}) AS numeric_present,
                count(*) FILTER (WHERE {x} IS NOT NULL AND {x}<>floor({x})) AS fractional,
                (SELECT max(n) FROM (SELECT count(*) AS n FROM selected WHERE {usable} GROUP BY {value})) AS top_count
                FROM selected''', f'Column facts for {name}')
            queries.append(result)
            values = dict(zip(result['columns'], result['rows'][0]))
            present = rows - item['missing']
            clean = present - values['markers']
            values.update(column=name, storage=dtype, missing=item['missing'], present=present, clean=clean,
                          distinct=item['distinct'], recognized_dates=item['recognized_dates'], statistics=item['statistics'],
                          numeric_like=bool(clean) and values['numeric_present'] >= SETTINGS['numeric_success'] * clean
                                       and values['leading_zero'] == 0)
            values['type'] = classify(values)
            facts[name] = values
        return report, facts, queries


def classify(f):
    clean, distinct = f['clean'], f['distinct_clean']
    if not clean: return 'empty'
    if distinct == 1: return 'constant'
    if distinct == 2: return 'binary'
    if f['numeric_like']:
        if not f['fractional'] and distinct == clean and clean >= 20: return 'identifier'
        return 'numeric'
    if f['recognized_dates'] >= clean: return 'date'
    if f['slash_dates'] == clean: return 'date' if slash_format(f) else 'ambiguous_date'
    long_text = distinct > SETTINGS['category_limit'] and (f['mean_length'] or 0) >= SETTINGS['text_mean_length']
    # Unique values with spaces and a long mean length are free text (comments), not identifiers.
    if long_text and f.get('spaced', 0) >= 0.5 * clean: return 'text'
    if clean >= 20 and distinct >= SETTINGS['identifier_share'] * clean: return 'identifier'
    if distinct > SETTINGS['category_limit'] and (f['mean_length'] or 0) >= SETTINGS['text_mean_length']: return 'text'
    return 'categorical'


ANALYSED_TYPES = ('categorical', 'binary', 'numeric', 'date')
SKIP_REASONS = {'identifier': 'identifier-like: all or almost all values are unique',
                'text': 'free text: many distinct values with a mean length of {mean_length:.0f} characters',
                'constant': 'constant: a single value', 'empty': 'no usable values',
                'ambiguous_date': 'dates with ambiguous day/month order, not converted'}


# ---------------- stage 1 and 2: profile and suggestions ----------------
def prepare(workspace, table_name):
    """Stages 1–2 on the active version: the data profile and ranked target suggestions."""
    info = workspace.info(table_name)
    analysis = Analysis(workspace, table_name, info['current'])
    started = time.perf_counter()
    report, facts, queries = analysis.facts()
    quality = quality_section(analysis, report, facts, queries)
    columns = {}
    for position, (name, f) in enumerate(facts.items()):
        values = []
        if f['distinct_clean'] <= SETTINGS['multiclass_maximum'] and f['type'] not in ('empty',):
            result = analysis.run(f'''SELECT trim(CAST({quote(name)} AS VARCHAR)) AS value,count(*) AS n FROM selected
                WHERE NOT {absent_sql(name)} GROUP BY 1 ORDER BY n DESC,value''', f'Values of {name}')
            values = [[v, n] for v, n in result['rows']]
        allowed = {'binary': ['binary'], 'categorical': ['multiclass'] if 3 <= f['distinct_clean'] <= SETTINGS['multiclass_maximum'] else [],
                   'numeric': ['numeric'] + (['multiclass'] if f['distinct_clean'] <= SETTINGS['multiclass_maximum'] else [])}.get(f['type'], [])
        columns[name] = {'type': f['type'], 'position': position, 'values': values, 'allowed_target_types': allowed,
                         'distinct': f['distinct_clean']}
    suggestions = suggest_targets(columns)
    return {'table': table_name, 'version': info['current'], 'columns': columns, 'suggestions': suggestions,
            'date_columns': [n for n, c in columns.items() if c['type'] == 'date'],
            'quality': quality, 'facts': facts, 'seconds': round(time.perf_counter() - started, 3)}


def suggest_targets(columns):
    last = max((c['position'] for c in columns.values()), default=0)
    ranked = []
    for name, column in columns.items():
        if not column['allowed_target_types']: continue
        kind = column['allowed_target_types'][0]
        reasons = {'binary': ['two distinct values: ' + ', '.join(str(v) for v, _ in column['values'])],
                   'multiclass': [f'{column["distinct"]} distinct categories'],
                   'numeric': ['numeric values']}[kind]
        score = {'binary': 3, 'multiclass': 2, 'numeric': 1}[kind]
        lowered = re.sub(r'[^a-z0-9]+', ' ', re.sub(r'([a-z])([A-Z])', r'\1 \2', name).lower())
        hints = [hint for hint in TARGET_HINTS if hint in lowered.split() or hint in lowered.replace(' ', '')]
        if hints:
            score += 2; reasons.append('name contains ' + ', '.join(repr(h) for h in hints) + ' (a hint only)')
        if column['position'] == last:
            score += 0.5; reasons.append('last column')
        suggestion = {'column': name, 'target_type': kind, 'score': score, 'reasons': reasons}
        if kind == 'binary': suggestion['positive'] = suggest_positive(column['values'])
        ranked.append(suggestion)
    ranked.sort(key=lambda s: (-s['score'], -columns[s['column']]['position']))
    return ranked


def suggest_positive(values):
    labels = [v for v, _ in values]
    for label in labels:
        if str(label).strip().lower() in POSITIVE_HINTS: return label
    # Otherwise the less frequent value; values are already ordered by count, then label.
    return labels[-1] if labels else None


def quality_section(analysis, report, facts, queries):
    rows = report['rows']; items = []; base = analysis.evidence('Full-table profile() of every column, plus one facts query per column', *queries)
    items.append(table('quality-overview', 'Dataset overview', [('Measure', 'text'), ('Value', 'count')], [
        ['Rows', rows], ['Columns', len(facts)], ['Exact duplicate rows (beyond the first copy)', report['exact_duplicate_rows']],
        ['Missing or blank cells', sum(f['missing'] for f in facts.values())],
        ['NA-like marker cells', sum(f['markers'] for f in facts.values())]], evidence=base))
    items.append(table('quality-columns', 'Columns', [('Column', 'text'), ('Stored as', 'text'), ('Detected type', 'text'),
        ('Values', 'count'), ('Distinct', 'count'), ('Missing or blank', 'count'), ('NA-like markers', 'count'), ('Missing share', 'percent')],
        [[n, f['storage'], f['type'], f['clean'], f['distinct_clean'], f['missing'], f['markers'], (f['missing'] + f['markers']) / rows if rows else None]
         for n, f in facts.items()], evidence=base,
        note='Missing share counts missing, blank and NA-like marker values together.'))
    problems = []
    for name, f in facts.items():
        stored_text = f['storage'] == 'VARCHAR'
        if stored_text and f['numeric_like'] and f['type'] == 'numeric':
            problems.append([name, 'numbers stored as text', f['numeric_present'], f'{f["clean"] - f["numeric_present"]:,} values do not parse as numbers'])
        if stored_text and f['leading_zero'] and f['numeric_present'] >= SETTINGS['numeric_success'] * max(f['clean'], 1):
            problems.append([name, 'numeric-looking values with leading zeros', f['leading_zero'], 'kept as text'])
        if stored_text and f['iso_dates'] and f['iso_dates'] == f['clean']:
            problems.append([name, 'dates stored as text (YYYY-MM-DD)', f['iso_dates'], ''])
        elif stored_text and f['recognized_dates'] >= f['clean'] > 0:
            problems.append([name, 'timestamps stored as text', f['recognized_dates'], 'analysed by calendar date'])
        if stored_text and f['slash_dates'] and f['slash_dates'] == f['clean']:
            problems.append([name, 'dates stored as text (day/month order: ' + slash_format_text(f) + ')', f['slash_dates'], ''])
        if f['markers']: problems.append([name, 'NA-like markers', f['markers'], ', '.join(NA_MARKERS)])
        if f['blank']: problems.append([name, 'empty or whitespace-only values', f['blank'], ''])
        if f['padded']: problems.append([name, 'leading or trailing whitespace', f['padded'], ''])
        if f['type'] in ('identifier', 'constant', 'text', 'empty'):
            problems.append([name, f['type'], f['clean'], SKIP_REASONS[f['type']].format(mean_length=f['mean_length'] or 0)])
        if f['clean'] and f['top_count'] and f['type'] != 'constant' and f['top_count'] / f['clean'] >= SETTINGS['near_constant_share']:
            problems.append([name, 'near-constant', f['top_count'], f'most common value is {f["top_count"] / f["clean"]:.1%} of values'])
        if f['type'] == 'categorical' and f['distinct_clean'] > SETTINGS['category_limit']:
            problems.append([name, 'high-cardinality categories', f['distinct_clean'], f'{f["distinct_clean"]:,} distinct values'])
    items.append(table('quality-problems', 'Detected issues', [('Column', 'text'), ('Issue', 'text'), ('Rows', 'count'), ('Detail', 'text')],
                       problems, evidence=base))
    missing_rows = []; missing_queries = []
    for name, f in facts.items():
        if not (f['missing'] + f['markers']): continue
        result = analysis.run(f'SELECT _row_id FROM selected WHERE {absent_sql(name)} ORDER BY _row_id LIMIT {SETTINGS["missing_row_ids"]}',
                              f'Rows without a usable value in {name}')
        missing_queries.append(result)
        missing_rows.append([name, f['missing'] + f['markers'], (f['missing'] + f['markers']) / rows,
                             ', '.join(str(r[0]) for r in result['rows'])])
    items.append(table('quality-missing', 'Missing values by column', [('Column', 'text'), ('Rows', 'count'), ('Share', 'percent'),
        (f'_row_id of affected rows (first {SETTINGS["missing_row_ids"]:,})', 'text')], missing_rows,
        evidence=analysis.evidence('Rows that are NULL, blank or an NA-like marker', *missing_queries)))
    variants = []; variant_queries = []
    for name, f in facts.items():
        if f['storage'] != 'VARCHAR' or f['type'] in ('identifier', 'text', 'empty') or f['distinct_clean'] > 10_000: continue
        key = f"lower(regexp_replace(trim(CAST({quote(name)} AS VARCHAR)),'\\s+',' ','g'))"
        result = analysis.run(f'''SELECT {key} AS normalized,list_sort(list(DISTINCT CAST({quote(name)} AS VARCHAR))) AS spellings,count(*) AS n
            FROM selected WHERE NOT {absent_sql(name)} GROUP BY 1 HAVING count(DISTINCT CAST({quote(name)} AS VARCHAR))>1
            ORDER BY 1 LIMIT {SETTINGS["variant_groups_per_column"]}''', f'Spellings of {name} that differ only by case or whitespace')
        variant_queries.append(result)
        variants += [[name, ' | '.join(repr(s) for s in spellings), n] for _, spellings, n in result['rows']]
    items.append(table('quality-variants', 'Category spellings that differ only by case or whitespace', [('Column', 'text'),
        ('Spellings', 'text'), ('Rows', 'count')], variants, evidence=analysis.evidence('Grouped by lower-case, whitespace-normalized value', *variant_queries),
        note='Reported only; these values are not merged.'))
    extremes = []; extreme_queries = []
    for name, f in facts.items():
        if f['type'] != 'numeric': continue
        x = numeric_expression(name); k = SETTINGS['extreme_iqr_multiple']
        result = analysis.run(f'''SELECT q1,q3,q1-{k}*(q3-q1) AS lower_fence,q3+{k}*(q3-q1) AS upper_fence,
            (SELECT count(*) FROM selected WHERE {x}<q1-{k}*(q3-q1)) AS below,(SELECT count(*) FROM selected WHERE {x}>q3+{k}*(q3-q1)) AS above,
            minimum,maximum FROM (SELECT quantile_cont({x},.25) AS q1,quantile_cont({x},.75) AS q3,min({x}) AS minimum,max({x}) AS maximum FROM selected)''',
            f'Extreme values of {name}')
        extreme_queries.append(result)
        q1, q3, low, high, below, above, minimum, maximum = result['rows'][0]
        if below or above: extremes.append([name, q1, q3, low, high, below, above, minimum, maximum])
    items.append(table('quality-extremes', 'Extreme numeric values (screening signal, not errors)', [('Column', 'text'), ('Q1', 'number'),
        ('Q3', 'number'), ('Lower fence', 'number'), ('Upper fence', 'number'), ('Below', 'count'), ('Above', 'count'), ('Minimum', 'number'),
        ('Maximum', 'number')], extremes, evidence=analysis.evidence(f'Fences at Q1 − {SETTINGS["extreme_iqr_multiple"]}×IQR and Q3 + {SETTINGS["extreme_iqr_multiple"]}×IQR', *extreme_queries)))
    if report['exact_duplicate_rows']:
        columns = ','.join(quote(n) for n in facts)
        result = analysis.run(f'''SELECT list(_row_id ORDER BY _row_id) AS row_ids,count(*) AS copies FROM selected GROUP BY {columns}
            HAVING count(*)>1 ORDER BY min(_row_id) LIMIT {SETTINGS["duplicate_groups"]}''', 'Exact duplicate row groups')
        items.append(table('quality-duplicates', f'Exact duplicate rows (first {SETTINGS["duplicate_groups"]} groups)',
            [('_row_id values', 'text'), ('Copies', 'count')], [[', '.join(map(str, ids)), n] for ids, n in result['rows']],
            evidence=analysis.evidence('Rows equal in every column except _row_id', result), note='Reported only; no rows are removed.'))
    return items


def slash_format_text(f):
    fmt = slash_format(f)
    return {'%d/%m/%Y': 'day first', '%m/%d/%Y': 'month first'}.get(fmt, 'ambiguous')


def slash_format(f):
    if f['first_over_12'] and not f['second_over_12']: return '%d/%m/%Y'
    if f['second_over_12'] and not f['first_over_12']: return '%m/%d/%Y'
    return None


# ---------------- stage 3: corrections ----------------
def correction_steps(facts, target=None, target_type=None):
    """Conservative, mechanical steps with the rows each affects and why."""
    steps = []
    for name, f in facts.items():
        if f['storage'] != 'VARCHAR': continue
        if f['padded']:
            steps.append(({'kind': 'trim', 'column': name}, f['padded'], 'leading or trailing whitespace'))
        markers = f['markers'] + f['blank']
        if markers:
            steps.append(({'kind': 'nullify_many', 'columns': [name], 'markers': None}, markers, 'NA-like markers and empty values become missing'))
        keep_labels = name == target and target_type in ('binary', 'multiclass')
        if f['type'] == 'numeric' and f['numeric_like'] and not keep_labels:
            steps.append(({'kind': 'numeric', 'column': name}, f['numeric_present'], 'numbers stored as text'))
        elif f['type'] == 'date' and f['iso_dates'] == f['clean']:
            steps.append(({'kind': 'date', 'column': name}, f['iso_dates'], 'dates stored as text (YYYY-MM-DD)'))
        elif f['type'] == 'date' and f['slash_dates'] == f['clean'] and slash_format(f):
            steps.append(({'kind': 'date', 'column': name, 'format': slash_format(f)}, f['slash_dates'],
                          'dates stored as text (' + slash_format_text(f) + ')'))
    return steps


def apply_corrections(analysis, facts, target, target_type):
    workspace, name = analysis.workspace, analysis.table
    steps = correction_steps(facts, target, target_type)
    for step, _, _ in steps:
        if step['kind'] == 'nullify_many':
            column = step['columns'][0]
            # The exact spellings present after trimming, so only real markers are matched.
            result = analysis.run(f'''SELECT DISTINCT trim(CAST({quote(column)} AS VARCHAR)) AS v FROM selected
                WHERE {absent_sql(column)} AND {quote(column)} IS NOT NULL ORDER BY 1''', f'NA-like spellings in {column}')
            step['markers'] = [r[0] for r in result['rows']][:20]
    rows = []; version = None; info = workspace.info(name); previous = info['current']
    if steps:
        version = workspace.transform(name, {'kind': 'batch', 'reason': 'automatic analysis: safe corrections',
                                             'steps': [step for step, _, _ in steps]})
        # The corrected version stays available, but the version the user had active is restored.
        workspace.activate(name, previous)
        errors = [s['errors'] for s in version['operation']['steps']]
        for (step, affected, reason), failed in zip(steps, errors):
            column = step.get('column') or step['columns'][0]
            if step['kind'] == 'nullify_many': change = 'set to missing: ' + ', '.join(repr(m) for m in step['markers'])
            else: change = {'trim': 'trimmed whitespace', 'numeric': 'converted to numbers', 'date': 'converted to dates'}[step['kind']]
            rows.append([column, change, affected, failed, reason, version['table']])
    return steps, version, rows


# ---------------- the run ----------------
def software_versions():
    """Versions of the packages whose numerics the results depend on."""
    import platform
    from importlib import metadata
    versions = {'python': platform.python_version()}
    for label, name in (('engine', 'insightpilot'), ('duckdb', 'duckdb'), ('numpy', 'numpy'), ('pandas', 'pandas'), ('scipy', 'scipy'),
                        ('scikit-learn', 'scikit-learn'), ('statsmodels', 'statsmodels')):
        try: versions[label] = metadata.version(name)
        except metadata.PackageNotFoundError: versions[label] = 'not installed'
    return versions


def run(workspace, table_name, *, target=None, target_type=None, positive=None, time_axis=None, progress=None,
        prepared=None, save_evidence=True, apply_corrections_step=True, config=None):
    progress = progress or (lambda *args: None)
    cfg = config or DEFAULT_CONFIG
    info = workspace.info(table_name)
    if prepared is None or prepared.get('version') != info['current']:
        prepared = None
    run_id = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6]
    output = {'format_version': FORMAT_VERSION, 'run_id': run_id, 'created_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
              'table': table_name, 'dataset': info['source'].get('filename', table_name), 'source_sha256': info['source']['sha256'],
              'rows': info['rows'], 'column_count': len(info['columns']), 'original_version': info['current'], 'analysis_version': info['current'],
              'target': target, 'target_type': target_type if target else None, 'positive': positive if target_type == 'binary' else None,
              'time_axis': time_axis, 'settings': SETTINGS, 'stages': [], 'sections': {}, 'skipped': [], 'field_failures': [],
              'sampling': [], 'small_sample_warnings': [], 'warnings': [], 'nonlinear': [], 'leakage': [],
              'config': cfg.to_dict(), 'config_hash': cfg.hash, 'seed': cfg.seed, 'versions': software_versions()}
    state = {'prepared': prepared, 'cfg': cfg, 'time_axis': time_axis,
             'time_sql': auto_time.timestamp_expression(time_axis) if time_axis else None}

    def stage(key, action, *, skip_reason=None):
        title = dict(STAGES)[key]
        if skip_reason:
            output['stages'].append({'key': key, 'title': title, 'status': 'skipped', 'detail': skip_reason, 'seconds': 0})
            progress(key, title, 'skipped', skip_reason); return
        progress(key, title, 'running', '')
        started = time.perf_counter()
        try:
            detail = action() or ''
            status = 'done with errors' if key == 'fields' and output['field_failures'] else 'done'
            output['stages'].append({'key': key, 'title': title, 'status': status, 'detail': detail,
                                     'seconds': round(time.perf_counter() - started, 3)})
            progress(key, title, status, detail)
        except Exception as exc:  # A failed stage is reported; later stages continue where they can.
            message = f'{type(exc).__name__}: {exc}'
            output['stages'].append({'key': key, 'title': title, 'status': 'failed', 'error': message,
                                     'seconds': round(time.perf_counter() - started, 3)})
            progress(key, title, 'failed', message)

    def profile_stage():
        state['prepared'] = state['prepared'] or prepare(workspace, table_name)
        output['sections']['quality'] = state['prepared']['quality']
        return f'{len(state["prepared"]["columns"])} columns profiled'

    def selection_stage():
        validate_target(state['prepared'], target, target_type, positive, time_axis)
        rows = [[s['column'], s['target_type'], s['score'], '; '.join(s['reasons'])] for s in state['prepared']['suggestions']]
        output['sections']['target_selection'] = [table('target-suggestions', 'Suggested targets (names are hints only)',
            [('Column', 'text'), ('Target type', 'text'), ('Score', 'number'), ('Reasons', 'text')], rows,
            evidence={'version': info['current'], 'computation': 'Ranked from detected column types; see the method section', 'queries': []}),
            table('target-choice', 'Chosen settings', [('Setting', 'text'), ('Value', 'text')],
                  [['Target', target or 'none (descriptive report)'], ['Target type', target_type or ''], ['Positive class', positive or ''],
                   ['Time axis', time_axis or 'none']], evidence={'version': info['current'], 'computation': 'Chosen by the analyst', 'queries': []})]
        return 'target: ' + (target or 'none')

    stage('profile', profile_stage)
    stage('target_selection', selection_stage)
    if not state['prepared'] or any(s['key'] == 'target_selection' and s['status'] == 'failed' for s in output['stages']):
        for key, title in STAGES[2:]:
            stage(key, None, skip_reason='an earlier stage failed')
        return output

    analysis = Analysis(workspace, table_name, info['current'])

    def corrections_stage():
        facts = state['prepared']['facts']; rows = []; version = None
        if apply_corrections_step:
            _, version, rows = apply_corrections(analysis, facts, target, target_type)
        if version:
            output['analysis_version'] = version['table']
        items = [table('corrections-applied', 'Corrections applied', [('Column', 'text'), ('Change', 'text'), ('Rows affected', 'count'),
            ('Conversion failures', 'count'), ('Reason', 'text'), ('Version created', 'text')], rows,
            evidence={'version': output['original_version'], 'computation': 'Workspace.transform(batch); receipts in the version history', 'queries': []},
            note=f'The original version {output["original_version"]} stays available. Failed conversions become missing and are kept in the conversion error ledger.')]
        state['corrected'] = Analysis(workspace, table_name, output['analysis_version'])
        report, facts_after, queries = state['corrected'].facts()
        state['facts'] = facts_after
        not_changed = []
        for name, f in state['prepared']['facts'].items():
            if f['storage'] == 'VARCHAR' and f['slash_dates'] and f['slash_dates'] == f['clean'] and not slash_format(f):
                not_changed.append([name, 'dates with ambiguous day/month order', f['slash_dates'], 'not converted'])
            if f['storage'] == 'VARCHAR' and f['recognized_dates'] >= f['clean'] > 0 and f['iso_dates'] < f['clean']:
                not_changed.append([name, 'timestamps stored as text', f['recognized_dates'], 'not converted; analysed by calendar date'])
            if f['leading_zero'] and f['numeric_present'] >= SETTINGS['numeric_success'] * max(f['clean'], 1):
                not_changed.append([name, 'leading zeros', f['leading_zero'], 'kept as text'])
        if report['exact_duplicate_rows']:
            not_changed.append(['(all columns)', 'exact duplicate rows', report['exact_duplicate_rows'], 'not removed'])
        variants = next((i for i in output['sections'].get('quality', []) if i['id'] == 'quality-variants'), None)
        for row in (variants['rows'] if variants else []):
            not_changed.append([row[0], 'spellings differing by case or whitespace: ' + row[1], row[2], 'not merged'])
        for name, f in facts_after.items():
            if f['missing']: not_changed.append([name, 'missing values', f['missing'], 'not filled'])
        items.append(table('corrections-not-changed', 'Issues reported but not changed', [('Column', 'text'), ('Issue', 'text'),
            ('Rows', 'count'), ('Action', 'text')], not_changed, evidence=state['corrected'].evidence('Facts of the analysed version', *queries)))
        items.append(explained_missing(state['corrected'], facts_after, cfg, output))
        output['sections']['corrections'] = items
        return f'{len(rows)} corrections' + (f'; version {version["table"]}' if version else '; no version needed')

    stage('corrections', corrections_stage)
    worker = state.get('corrected') or analysis
    facts = state.get('facts') or state['prepared']['facts']
    spec = TargetSpec(target, target_type, positive)

    stage('structure', lambda: structure_stage(worker, facts, spec, output, state))
    stage('target_overview', lambda: target_overview(worker, spec, output), skip_reason=None if target else 'no target chosen')
    stage('fields', lambda: fields_stage(worker, spec, facts, time_axis, output, state))
    stage('leakage', lambda: leakage_stage(worker, spec, facts, output, state), skip_reason=None if target else 'no target chosen')
    stage('ranking', lambda: ranking_stage(output, state), skip_reason=None if target else 'no target chosen')
    stage('multivariate', lambda: multivariate_stage(worker, spec, facts, time_axis, output, state), skip_reason=None if target else 'no target chosen')
    stage('confounding', lambda: confounding_stage(worker, spec, facts, output, state), skip_reason=None if target else 'no target chosen')
    stage('segments', lambda: segments_stage(worker, spec, facts, output, state), skip_reason=None if target else 'no target chosen')
    stage('unsupervised', lambda: unsupervised_stage(worker, facts, time_axis, output, state), skip_reason='a target is chosen' if target else None)
    finish_structure(output, state, bool(target))
    stage('relationships', lambda: relationships_stage(worker, spec, facts, time_axis, output, state))
    stage('time', lambda: time_stage(worker, spec, facts, time_axis, output, state), skip_reason=None if time_axis else 'no time axis chosen')
    stage('findings', lambda: findings_stage(output, state))
    stage('evidence', lambda: save_blocks(worker, output) if save_evidence else 'not requested')
    return output


def validate_target(prepared, target, target_type, positive, time_axis):
    columns = prepared['columns']
    if time_axis is not None:
        if time_axis not in columns or columns[time_axis]['type'] != 'date': raise ValueError('The time axis must be a date column.')
        if time_axis == target: raise ValueError('The time axis and the target must be different columns.')
    if target is None: return
    if target not in columns: raise ValueError('Choose an existing target column.')
    allowed = columns[target]['allowed_target_types']
    if target_type not in allowed:
        raise ValueError(f'{target} can be analysed as: ' + (', '.join(allowed) or 'nothing (identifier, text, constant or date)') + '.')
    if target_type == 'binary':
        labels = [v for v, _ in columns[target]['values']]
        if positive not in labels: raise ValueError('Choose the positive class from: ' + ', '.join(map(str, labels)) + '.')


class TargetSpec:
    def __init__(self, column, kind, positive, extra=None):
        self.column, self.kind, self.positive, self.extra = column, kind, positive, extra

    def restricted(self, where):
        """The same target, analysed only on rows where `where` holds."""
        return TargetSpec(self.column, self.kind, self.positive, where)

    @property
    def ok(self):
        if self.column is None: base = 'true'
        elif self.kind == 'numeric': base = f'{numeric_expression(self.column)} IS NOT NULL'
        else: base = f'NOT {absent_sql(self.column)}'
        return f'({base}) AND ({self.extra})' if self.extra else base

    @property
    def label(self): return f'trim(CAST({quote(self.column)} AS VARCHAR))'

    @property
    def positive_flag(self): return f'CASE WHEN {self.label}={literal(self.positive)} THEN 1 ELSE 0 END'

    @property
    def value(self): return numeric_expression(self.column)


# ---------------- stage 4 ----------------
def target_overview(a, spec, output):
    items = []
    if spec.kind in ('binary', 'multiclass'):
        result = a.run(f'''SELECT {spec.label} AS class,count(*) AS n FROM selected WHERE {spec.ok} GROUP BY 1 ORDER BY n DESC,class''',
                       'Rows per target class')
        missing = a.run(f'SELECT count(*) FROM selected WHERE NOT ({spec.ok})', 'Rows without a target value')
        total = sum(n for _, n in result['rows']); excluded = missing['rows'][0][0]
        rows = [[c, n, n / total] for c, n in result['rows']]
        baseline = result['rows'][0][1] / total
        smallest = min(n for _, n in result['rows']) / total
        items.append(table('target-classes', 'Target classes', [('Class', 'text'), ('Rows', 'count'), ('Share', 'percent')], rows,
                           evidence=a.evidence('Count per class', result, missing),
                           note=f'Rows without a target value, excluded from target comparisons: {excluded:,}.'))
        summary = [['Rows with a target value', total, None],
                   ['Accuracy of always predicting the majority class', None, baseline]]
        if spec.kind == 'binary':
            positives = dict(result['rows']).get(spec.positive, 0)
            summary.insert(1, [f'Positive class ({spec.positive}): rows and positive rate', positives, positives / total])
        items.append(table('target-baseline', 'Target summary', [('Measure', 'text'), ('Rows', 'count'), ('Share', 'percent')],
                           summary, evidence=a.evidence('Majority class share = largest class / rows with a target value', result)))
        if smallest < SETTINGS['imbalance_share']:
            items.append(text(f'Class imbalance: the smallest class is {smallest:.1%} of rows, below the {SETTINGS["imbalance_share"]:.0%} threshold.'))
        cfg = DEFAULT_CONFIG; minority_rows = min(n for _, n in result['rows'])
        if smallest < cfg.imbalance_share or (spec.kind == 'binary' and smallest > 1 - cfg.imbalance_share):
            output['warnings'].append(f'Imbalanced target: the smallest class is {smallest:.1%} of rows. Rates have wide intervals; AUC is shown with PR-AUC.')
        if minority_rows < cfg.minority_rows_minimum:
            output['warnings'].append(f'Small minority class: {minority_rows:,} rows, below {cfg.minority_rows_minimum}. Estimates for it are uncertain.')
        fig = go.Figure(go.Bar(x=[visuals.category_label(c) for c, _, _ in rows], y=[n for _, n, _ in rows],
                               text=[f'{n:,} · {share:.1%}' for _, n, share in rows], textposition='outside', marker_color=visuals.TEAL))
        items.append(figure('target-classes-chart', 'Rows per target class', visuals.style(fig, 'Rows per target class', spec.column, 'Rows'),
                            evidence=a.evidence('Count per class', result)))
        a.results['target-classes-chart'] = result
    else:
        y = spec.value
        result = a.run(f'''SELECT count({y}) AS n,count(*)-count({y}) AS missing,min({y}) AS minimum,quantile_cont({y},.25) AS q1,
            median({y}) AS median,quantile_cont({y},.75) AS q3,max({y}) AS maximum,avg({y}) AS mean,stddev_samp({y}) AS std,
            skewness({y}) AS skewness FROM selected''', 'Numeric target summary')
        values = result['rows'][0]
        items.append(table('target-summary', 'Target summary', [(c, 'count' if c in ('n', 'missing') else 'number') for c in result['columns']],
                           [values], evidence=a.evidence('Full-column aggregates', result)))
        histogram = a.engine.distribution(spec.column, SETTINGS['histogram_bins'])
        items.append(figure('target-histogram', 'Target distribution', visuals.histogram(histogram, f'{spec.column} distribution'),
                            evidence=a.evidence('Equal-width histogram (Engine.distribution)', histogram)))
        a.results['target-histogram'] = histogram
        fig = go.Figure(go.Box(q1=[values[3]], median=[values[4]], q3=[values[5]], lowerfence=[values[2]], upperfence=[values[6]],
                               x=[spec.column], marker_color=visuals.TEAL, name=spec.column))
        items.append(figure('target-box', 'Target box plot', visuals.style(fig, f'{spec.column}: quartiles, minimum and maximum', None, spec.column),
                            evidence=a.evidence('Full-column quartiles', result)))
        a.results['target-box'] = result
    output['sections']['target'] = items
    return ''


# ---------------- stage 5 ----------------
def category_expression(a, column, where):
    """Top categories by row count; the rest become Other and missing values their own group."""
    limit = SETTINGS['category_limit']
    result = a.run(f'''SELECT CAST({quote(column)} AS VARCHAR) AS v,count(*) AS n FROM selected WHERE {where} AND NOT {absent_sql(column)}
        GROUP BY 1 ORDER BY n DESC,v LIMIT {limit + 1}''', f'Most frequent categories of {column}')
    values = [r[0] for r in result['rows'][:limit]]; other = 0
    if len(result['rows']) > limit:
        other = a.run(f'SELECT count(DISTINCT CAST({quote(column)} AS VARCHAR)) FROM selected WHERE {where} AND NOT {absent_sql(column)}',
                      f'Distinct categories of {column}')['rows'][0][0] - limit
    listed = ','.join(literal(v) for v in values) or 'NULL'
    sql = (f"CASE WHEN {absent_sql(column)} THEN {literal(MISSING_KEY)} WHEN CAST({quote(column)} AS VARCHAR) IN ({listed}) "
           f"THEN CAST({quote(column)} AS VARCHAR) ELSE {literal(OTHER_KEY)} END")
    return sql, other, result


def fields_stage(a, spec, facts, time_axis, output, state):
    sections = []; state['effects'] = []
    for name, f in facts.items():
        if name == spec.column: continue
        if name == time_axis:
            output['skipped'].append([name, 'used as the time axis']); continue
        if f['type'] == 'text':
            try:
                sections.append({'field': name, 'type': 'text', 'items': text_field(a, spec, name, state['cfg'])})
            except Exception as exc:
                output['field_failures'].append([name, f'{type(exc).__name__}: {exc}'])
            continue
        if f['type'] not in ANALYSED_TYPES:
            output['skipped'].append([name, SKIP_REASONS[f['type']].format(mean_length=f['mean_length'] or 0)]); continue
        try:
            items, effect = analyse_field(a, spec, name, f, output)
            items, effect = enrich_field(a, spec, name, f, items, effect, output, state)
            if f['type'] == 'categorical': items += country_codes(a, spec, name, f)
        except Exception as exc:
            output['field_failures'].append([name, f'{type(exc).__name__}: {exc}']); continue
        sections.append({'field': name, 'type': f['type'], 'items': items})
        if effect: state['effects'].append(effect)
    for lat, lon in auto_textgeo.coordinate_pairs({n: f for n, f in facts.items() if n != spec.column}):
        try:
            sections.append({'field': f'{lat}, {lon}', 'type': 'geo', 'items': geo_pair(a, spec, lat, lon, state['cfg'])})
        except Exception as exc:
            output['field_failures'].append([f'{lat}, {lon}', f'{type(exc).__name__}: {exc}'])
    output['sections']['fields'] = sections
    return f'{len(sections)} fields analysed, {len(output["skipped"])} skipped, {len(output["field_failures"])} failed'


class _SuffixedResults(dict):
    """Stores chart evidence under suffixed ids while a field is analysed a second time."""
    def __init__(self, target, suffix):
        super().__init__(); self.target, self.suffix = target, suffix

    def __setitem__(self, key, value):
        self.target[key + self.suffix] = value


def enrich_field(a, spec, name, f, items, effect, output, state):
    """Applicable-subset analysis for conditional fields, predictive power and structure flags."""
    structure = state.get('structure'); cfg = state['cfg']
    kind = 'categorical' if f['type'] in ('categorical', 'binary') else f['type']
    conditional = structure.conditional_for(name) if structure else None
    derived = structure.derived_for(name) if structure else None
    notes = []
    if derived:
        notes.append(text(f'{name} is approximately derived: {name} ≈ {derived.factor:.4g} × ({derived.formula}), R² {derived.r2_full:.4f} on '
                          f'{derived.rows:,} rows. It is listed under its source columns in the ranking, not as a separate field.'))
    target_spec = spec
    if conditional and spec.column:
        target_spec = spec.restricted(conditional.applicable_where)
        real = a.results; a.results = _SuffixedResults(real, '-applicable')
        try:
            subset_items, subset_effect = analyse_field(a, target_spec, name, f, output)
        finally:
            a.results = real
        prefix = f'Applicable rows ({conditional.parent} ≠ {conditional.parent_value}): '
        for item in subset_items:
            if item['type'] != 'text': item['id'] += '-applicable'; item['title'] = prefix + item['title']
        notes.append(text(f'{name} is conditional: its level "{conditional.level}" occurs exactly when {conditional.parent} = '
                          f'{conditional.parent_value} ({conditional.rows:,} rows). Results on applicable rows come first and are used for ranking; '
                          'results on all rows follow for reference.'))
        reference = effect
        items = subset_items + [text('Reference: all rows, including rows where the field does not apply.')] + items
        effect = subset_effect
        if effect and reference: effect['unconditional_effect'] = reference['effect']
    if not spec.column:
        return notes + items, effect
    power = auto_univariate.field_power(a, target_spec, name, kind, cfg, absent_sql, state.get('time_sql'))
    state.setdefault('powers', {})[name] = power
    if effect is None or kind == 'date':
        return notes + items, effect
    effect.update(power=_clean(power['value']), power_metric=power['metric'], power_sd=_clean(power['sd']), power_auc=_clean(power['auc']),
                  nmi_adjusted=_clean(power['nmi_adjusted']), nmi_p=_clean(power['nmi_p']), derived=derived.formula if derived else None,
                  subset=f'{conditional.parent} ≠ {conditional.parent_value}' if conditional else 'all rows')
    folds = ', '.join(f'{v:.3f}' for v in power['folds'])
    power_rows = [[power['metric'], power['value'], power['auc'], power['sd'], folds, power['nmi'], power['nmi_adjusted'], power['nmi_p'],
                   power['grouping'], effect['subset']]]
    notes.append(table(item_id(name, 'power'), f'{name}: cross-validated predictive power', [('Metric', 'text'), ('Value', 'effect'),
        ('AUC', 'effect'), ('Fold sd', 'effect'), ('Per fold', 'text'), ('NMI', 'effect'), ('NMI minus permutation mean', 'effect'),
        ('Permutation p', 'p'), ('Grouping', 'text'), ('Rows', 'text')], power_rows,
        evidence=a.evidence(f'{cfg.cv_folds}-fold cross-validated target encoding (smoothing {cfg.encoding_smoothing:g}); '
                            f'NMI against {cfg.permutations} permutations, seed {cfg.seed}', *power['queries']),
        note='ΔAUC is the out-of-fold AUC minus 0.5 for a model that uses only this field; R² is its out-of-fold R². '
             + (f'Folds are {cfg.cv_folds + 1} consecutive time blocks; each later block is scored from the earlier ones only.' if state.get('time_sql')
                else 'Folds are stratified and seeded.')))
    return notes + items, effect


def analyse_field(a, spec, name, f, output):
    kind = 'categorical' if f['type'] in ('categorical', 'binary') else f['type']
    if spec.column is None:
        return describe_field(a, name, kind, output), None
    if kind == 'date':
        return date_versus_target(a, spec, name), None
    return {('categorical', 'binary'): categorical_binary, ('categorical', 'multiclass'): categorical_multiclass,
            ('categorical', 'numeric'): categorical_numeric, ('numeric', 'binary'): numeric_binary,
            ('numeric', 'multiclass'): numeric_multiclass, ('numeric', 'numeric'): numeric_numeric}[(kind, spec.kind)](a, spec, name, output)


def item_id(name, suffix):
    return re.sub(r'[^A-Za-z0-9]+', '-', name).strip('-').lower()[:40] + '-' + suffix


def flag_small(n):
    return f'fewer than {SETTINGS["small_group_rows"]} rows' if n < SETTINGS['small_group_rows'] else ''


def note_other(other):
    return f'The {SETTINGS["category_limit"]} most frequent categories are shown; {other:,} other categories are grouped as "other".' if other else None


def heatmap(pivot, title, x, y):
    fig = px.imshow(pivot, color_continuous_scale='Teal', labels={'color': 'Rows'}, text_auto=',d' if pivot.size <= 150 else False, aspect='auto')
    fig.update_traces(hovertemplate=y + ': %{y}<br>' + x + ': %{x}<br>%{z:,} rows<extra></extra>')
    return visuals.style(fig, title, x, y)


def chi_rows(chi):
    if not chi: return [['Chi-square test of independence', None, None, None, None, None, 'undefined: fewer than two represented groups']]
    flag = f'{chi["cells_expected_below_5"]:,} of {chi["cells"]:,} cells have an expected count below {SETTINGS["expected_count_minimum"]}' if chi['cells_expected_below_5'] else ''
    return [['Chi-square test of independence', chi['statistic'], chi['df'], chi['p_value'], chi['cramers_v'], chi['n'], flag]]


TEST_COLUMNS = [('Test', 'text'), ('Statistic', 'number'), ('df', 'count'), ('p-value', 'p'), ('Effect size', 'effect'), ('n', 'count'), ('Flags', 'text')]


def categorical_binary(a, spec, name, output):
    expression, other, top = category_expression(a, name, spec.ok)
    result = a.run(f'SELECT {expression} AS category,count(*) AS total,sum({spec.positive_flag}) AS positives FROM selected WHERE {spec.ok} GROUP BY 1',
                   f'{name} by target')
    rows = sorted(result['rows'], key=lambda r: category_order(r[0], r[1]))
    all_positive = sum(r[2] for r in rows) or 1; overall = sum(r[2] for r in rows) / (sum(r[1] for r in rows) or 1)
    confidence = DEFAULT_CONFIG.confidence
    table_rows = [[category_label(c, other), t, p, t - p, *auto_univariate.rate_columns(p, t, overall, confidence), p / all_positive, flag_small(t)]
                  for c, t, p in rows]
    chi = auto_stats.chi_square([[p, t - p] for _, t, p in rows])
    evidence = a.evidence('Counts per category; chi-square on the category × target table (SciPy)', top, result)
    items = [table(item_id(name, 'rates'), f'{name}: rows and positive rate ({spec.column} = {spec.positive})', [('Category', 'text'),
        ('Rows', 'count'), ('Positives', 'count'), ('Negatives', 'count'), *RATE_COLUMNS, ('Share of all positives', 'percent'),
        ('Flags', 'text')], table_rows, evidence=evidence, note=rate_note(overall, other)),
        table(item_id(name, 'test'), f'{name}: test of independence', TEST_COLUMNS, chi_rows(chi), evidence=evidence,
              note="Effect size: Cramér's V.")]
    labels = [r[0] for r in table_rows]
    negative = 'not ' + str(spec.positive)
    pivot = pd.DataFrame({str(spec.positive): [r[2] for r in table_rows], negative: [r[3] for r in table_rows]}, index=labels)
    items.append(figure(item_id(name, 'heatmap'), f'{name} × {spec.column}: rows', heatmap(pivot, f'{name} × {spec.column}: rows', spec.column, name), evidence=evidence))
    ordered = sorted(table_rows, key=lambda r: (-r[4], r[0]))
    bars = go.Figure(go.Bar(y=[r[0] for r in ordered], x=[r[4] for r in ordered], orientation='h', marker_color=visuals.TEAL,
                            error_x={'type': 'data', 'symmetric': False, 'array': [r[6] - r[4] for r in ordered],
                                     'arrayminus': [r[4] - r[5] for r in ordered], 'color': visuals.MUTED},
                            text=[f'{r[4]:.1%} of {r[1]:,}' for r in ordered], textposition='outside', cliponaxis=False))
    bars.add_vline(x=overall, line_dash='dot', line_color=visuals.AMBER, annotation_text=f'overall {overall:.1%}')
    bars.update_xaxes(tickformat='.0%'); bars.update_yaxes(autorange='reversed')
    items.append(figure(item_id(name, 'rate'), f'{name}: positive rate', visuals.style(bars, f'{name}: share of rows with {spec.column} = {spec.positive}',
                        'Positive rate', name), evidence=evidence))
    a.results[item_id(name, 'heatmap')] = a.results[item_id(name, 'rate')] = result
    small_groups(output, name, table_rows, 1)
    return items, effect_row(name, 'categorical', "Chi-square", chi, 'cramers_v')


RATE_COLUMNS = [('Positive rate', 'percent'), ('95% CI low', 'percent'), ('95% CI high', 'percent'), ('Difference vs overall (pp)', 'number'),
                ('Lift', 'number')]


def rate_note(overall, other=0):
    note = f'Overall positive rate {overall:.1%}. Intervals are 95% Wilson intervals; lift is the group rate divided by the overall rate.'
    return note + (' ' + note_other(other) if other else '')


def small_groups(output, name, rows, count_index):
    for row in rows:
        if row[count_index] < SETTINGS['small_group_rows']:
            output['small_sample_warnings'].append([name, row[0], row[count_index]])


def effect_row(name, kind, test, result, key):
    if not result: return {'field': name, 'type': kind, 'test': test, 'measure': key, 'effect': None, 'p_value': None, 'n': None}
    return {'field': name, 'type': kind, 'test': test, 'measure': key, 'effect': _clean(result[key]), 'p_value': _clean(result['p_value']), 'n': result['n']}


def categorical_multiclass(a, spec, name, output):
    expression, other, top = category_expression(a, name, spec.ok)
    result = a.run(f'SELECT {expression} AS category,{spec.label} AS class,count(*) AS n FROM selected WHERE {spec.ok} GROUP BY 1,2',
                   f'{name} by target class')
    frame = pd.DataFrame(result['rows'], columns=['category', 'class', 'n'])
    classes = frame.groupby('class').n.sum().reset_index().sort_values(['n', 'class'], ascending=[False, True])['class'].tolist()
    pivot = frame.pivot(index='category', columns='class', values='n').fillna(0).astype(int)[classes]
    pivot['total_rows'] = pivot[classes].sum(axis=1)
    pivot = pivot.reset_index()
    pivot['label'] = pivot.category.map(lambda c: category_label(c, other))
    pivot = pivot.iloc[sorted(range(len(pivot)), key=lambda i: category_order(pivot.category.iloc[i], pivot.total_rows.iloc[i]))]
    rows = [[r['label'], r['total_rows'], *[r[c] for c in classes], *[r[c] / r['total_rows'] for c in classes], flag_small(r['total_rows'])]
            for _, r in pivot.iterrows()]
    chi = auto_stats.chi_square(pivot[classes].to_numpy())
    evidence = a.evidence('Category × class counts; chi-square (SciPy)', top, result)
    columns = [('Category', 'text'), ('Rows', 'count'), *[(f'{c} rows', 'count') for c in classes], *[(f'{c} share', 'percent') for c in classes], ('Flags', 'text')]
    items = [table(item_id(name, 'classes'), f'{name}: rows and row shares by {spec.column}', columns, rows, evidence=evidence, note=note_other(other)),
             table(item_id(name, 'test'), f'{name}: test of independence', TEST_COLUMNS, chi_rows(chi), evidence=evidence, note="Effect size: Cramér's V.")]
    fig = go.Figure()
    for index, c in enumerate(classes):
        fig.add_trace(go.Bar(x=pivot['label'], y=pivot[c] / pivot['total_rows'], name=str(c), marker_color=visuals.COLORS[index % len(visuals.COLORS)]))
    fig.update_layout(barmode='stack'); fig.update_yaxes(tickformat='.0%')
    items.append(figure(item_id(name, 'stacked'), f'{name}: class shares', visuals.style(fig, f'{name}: share of each {spec.column} class', name, 'Share of rows'),
                        evidence=evidence))
    a.results[item_id(name, 'stacked')] = result
    small_groups(output, name, rows, 1)
    return items, effect_row(name, 'categorical', 'Chi-square', chi, 'cramers_v')


def group_summary_sql(group_sql, value_sql, where):
    return (f'SELECT g AS grp,count(*) AS rows_,count(v) AS n,count(*)-count(v) AS missing,min(v) AS minimum,quantile_cont(v,.25) AS q1,'
            f'median(v) AS median,quantile_cont(v,.75) AS q3,max(v) AS maximum,avg(v) AS mean,stddev_samp(v) AS std '
            f'FROM (SELECT {group_sql} AS g,{value_sql} AS v FROM selected WHERE {where}) GROUP BY 1')


SUMMARY_COLUMNS = [('Rows', 'count'), ('n', 'count'), ('Missing', 'count'), ('Minimum', 'number'), ('Q1', 'number'), ('Median', 'number'),
                   ('Q3', 'number'), ('Maximum', 'number'), ('Mean', 'number'), ('Std', 'number')]


def box(rows, title, x, y):
    fig = go.Figure(go.Box(q1=[r[5] for r in rows], median=[r[6] for r in rows], q3=[r[7] for r in rows], lowerfence=[r[4] for r in rows],
                           upperfence=[r[8] for r in rows], x=[visuals.category_label(r[0]) for r in rows], marker_color=visuals.TEAL, name=y))
    return visuals.style(fig, title, x, y)


def kruskal(a, value_sql, group_sql, where, label):
    result = a.run(auto_stats.rank_groups_sql(value_sql, group_sql, f'selected WHERE {where}'), label)
    rows = result['rows']
    return auto_stats.kruskal_wallis([(n, r) for _, n, r, _ in rows], rows[0][3] if rows else 0), result


def categorical_numeric(a, spec, name, output):
    expression, other, top = category_expression(a, name, spec.ok)
    summary = a.run(group_summary_sql(expression, spec.value, spec.ok), f'{spec.column} by {name}')
    rows = sorted(summary['rows'], key=lambda r: category_order(r[0], r[1]))
    intervals, sample, note = median_intervals(a, expression, spec.value, spec.ok, len(rows), f'{spec.column} by {name}')
    rows = [[category_label(r[0], other), *r[1:], *intervals.get(r[0], (None, None)), flag_small(r[2])] for r in rows]
    test, ranks = kruskal(a, spec.value, expression, spec.ok, f'Ranks of {spec.column} by {name}')
    evidence = a.evidence('Exact quantiles per category; Kruskal–Wallis from average ranks; ' + note, top, summary, ranks, sample)
    test_rows = [['Kruskal–Wallis H', test['statistic'], test['df'], test['p_value'], test['epsilon_squared'], test['n'], '']] if test else \
        [['Kruskal–Wallis H', None, None, None, None, None, 'undefined: fewer than two groups or all values tied']]
    items = [table(item_id(name, 'summary'), f'{spec.column} by {name}', [('Category', 'text'), *SUMMARY_COLUMNS, ('Median 95% CI low', 'number'),
                   ('Median 95% CI high', 'number'), ('Flags', 'text')], rows, evidence=evidence, note=' '.join(filter(None, [note, note_other(other)]))),
             table(item_id(name, 'test'), f'{name}: test of differences in {spec.column}', TEST_COLUMNS, test_rows, evidence=evidence,
                   note='Effect size: epsilon-squared.'),
             figure(item_id(name, 'box'), f'{spec.column} by {name}', box(rows, f'{spec.column} by {name}', name, spec.column), evidence=evidence)]
    a.results[item_id(name, 'box')] = summary
    small_groups(output, name, rows, 2)
    return items, effect_row(name, 'categorical', 'Kruskal–Wallis', test, 'epsilon_squared')


def median_intervals(a, group_sql, value_sql, where, groups, label):
    """Percentile-bootstrap intervals for each group's median from a seeded sample of rows per group."""
    cfg = DEFAULT_CONFIG
    cap = max(30, min(cfg.bootstrap_rows_cap, 50_000 // max(groups, 1)))
    sample = a.run(f'''SELECT g,v FROM (SELECT g,v,row_number() OVER (PARTITION BY g ORDER BY hash(_row_id + {cfg.seed}),_row_id) AS r FROM
        (SELECT _row_id,{group_sql} AS g,{value_sql} AS v FROM selected WHERE {where}) WHERE v IS NOT NULL) WHERE r<={cap}''', f'Bootstrap sample: {label}')
    values = {}
    for g, v in sample['rows']: values.setdefault(g, []).append(float(v))
    intervals = {}; sampled = False
    for index, (g, group) in enumerate(sorted(values.items(), key=lambda t: str(t[0]))):
        sampled = sampled or len(group) >= cap
        resamples = cfg.bootstrap_resamples if len(group) <= cfg.bootstrap_large_n else 200
        intervals[g] = bootstrap_interval(group, np.median, resamples, cfg.confidence, cfg.seed + index)
    note = (f'Median intervals: percentile bootstrap, {cfg.bootstrap_resamples:,} resamples, seed {cfg.seed}'
            + (f', from at most {cap:,} sampled rows per group' if sampled else '') + '.')
    return intervals, sample, note


def numeric_by_class(a, spec, name):
    x = numeric_expression(name)
    summary = a.run(group_summary_sql(spec.label, x, spec.ok), f'{name} by target class')
    rows = sorted(summary['rows'], key=lambda r: (-r[1], str(r[0])))
    return x, summary, [[r[0], *r[1:]] for r in rows]


def numeric_binary(a, spec, name, output):
    x, summary, rows = numeric_by_class(a, spec, name)
    ranks = a.run(auto_stats.rank_groups_sql(x, spec.positive_flag, f'selected WHERE {spec.ok}'), f'Ranks of {name} by target')
    groups = {g: (n, r, ties) for g, n, r, ties in ranks['rows']}
    test = auto_stats.mann_whitney(groups[1][0], groups[1][1], groups[0][0], groups[1][2]) if 1 in groups and 0 in groups else None
    bins = a.run(f'''SELECT bin,min(x) AS lower,max(x) AS upper,count(*) AS n,sum(y) AS positives FROM
        (SELECT x,y,ntile({DEFAULT_CONFIG.numeric_bins}) OVER (ORDER BY x,_row_id) AS bin FROM
        (SELECT {x} AS x,{spec.positive_flag} AS y,_row_id FROM selected WHERE {spec.ok}) WHERE x IS NOT NULL) GROUP BY 1 ORDER BY 1''',
        f'Positive rate by quantile bins of {name}')
    overall = sum(r[4] for r in bins['rows']) / (sum(r[3] for r in bins['rows']) or 1)
    bin_rows = [[b, low, high, n, p, *auto_univariate.rate_columns(p, n, overall, DEFAULT_CONFIG.confidence)] for b, low, high, n, p in bins['rows']]
    evidence = a.evidence('Exact quantiles per class; Mann–Whitney U from average ranks; equal-count bins by value then _row_id', summary, ranks, bins)
    test_rows = [['Mann–Whitney U (normal approximation, tie and continuity corrected)', test['statistic'], None, test['p_value'],
                  test['rank_biserial'], test['n'], '']] if test else [['Mann–Whitney U', None, None, None, None, None, 'undefined']]
    items = [table(item_id(name, 'summary'), f'{name} by {spec.column}', [('Class', 'text'), *SUMMARY_COLUMNS], rows, evidence=evidence),
             table(item_id(name, 'test'), f'{name}: test of differences between classes', TEST_COLUMNS, test_rows, evidence=evidence,
                   note=f'Effect size: rank-biserial correlation r = 2U/(n₁n₂) − 1, where group 1 is {spec.column} = {spec.positive}; '
                        'positive r means larger values are more common in that group.'),
             table(item_id(name, 'bins'), f'{name}: positive rate by {DEFAULT_CONFIG.numeric_bins} equal-count bins', [('Bin', 'count'),
                   ('Lowest value', 'number'), ('Highest value', 'number'), ('Rows', 'count'), ('Positives', 'count'), *RATE_COLUMNS],
                   bin_rows, evidence=evidence, note=rate_note(overall) + ' Rows with tied values can fall into adjacent bins; ties are ordered by _row_id.'),
             figure(item_id(name, 'box'), f'{name} by {spec.column}', box(rows, f'{name} by {spec.column}', spec.column, name), evidence=evidence)]
    line = go.Figure(go.Scatter(x=[f'{r[0]}: {visuals.compact(r[1])}–{visuals.compact(r[2])}' for r in bin_rows], y=[r[5] for r in bin_rows],
                                mode='lines+markers', line={'color': visuals.TEAL}, text=[f'{r[5]:.1%} of {r[3]:,}' for r in bin_rows],
                                error_y={'type': 'data', 'symmetric': False, 'array': [r[7] - r[5] for r in bin_rows],
                                         'arrayminus': [r[5] - r[6] for r in bin_rows], 'color': visuals.MUTED}))
    line.add_hline(y=overall, line_dash='dot', line_color=visuals.AMBER, annotation_text=f'overall {overall:.1%}')
    line.update_yaxes(tickformat='.0%')
    items.append(figure(item_id(name, 'bins-chart'), f'{name}: positive rate by bin', visuals.style(line, f'{name}: positive rate by equal-count bin',
                        name, 'Positive rate'), evidence=evidence))
    a.results[item_id(name, 'box')] = summary; a.results[item_id(name, 'bins-chart')] = bins
    share = auto_univariate.monotone_share([r[5] for r in bin_rows], [r[3] for r in bin_rows])
    chi = auto_stats.chi_square([[r[4], r[3] - r[4]] for r in bin_rows])
    spread = max(r[5] for r in bin_rows) - min(r[5] for r in bin_rows) if bin_rows else 0
    if share is not None and chi and share < DEFAULT_CONFIG.monotone_share_minimum and chi['p_value'] < 0.01 and spread >= 0.05:
        flag_nonlinear(output, items, name, share, f'positive rates across bins range from {min(r[5] for r in bin_rows):.1%} to {max(r[5] for r in bin_rows):.1%}')
    return items, effect_row(name, 'numeric', 'Mann–Whitney U', test, 'rank_biserial')


def flag_nonlinear(output, items, name, share, detail):
    output['nonlinear'].append({'field': name, 'monotone_share': _clean(share), 'detail': detail})
    items.insert(0, text(f'Non-linear relationship: a monotone curve explains {share:.0%} of the variation between bins ({detail}). '
                         'A single direction or correlation does not describe this field.'))


def numeric_multiclass(a, spec, name, output):
    x, summary, rows = numeric_by_class(a, spec, name)
    test, ranks = kruskal(a, x, spec.label, spec.ok, f'Ranks of {name} by target class')
    evidence = a.evidence('Exact quantiles per class; Kruskal–Wallis from average ranks', summary, ranks)
    test_rows = [['Kruskal–Wallis H', test['statistic'], test['df'], test['p_value'], test['epsilon_squared'], test['n'], '']] if test else \
        [['Kruskal–Wallis H', None, None, None, None, None, 'undefined']]
    items = [table(item_id(name, 'summary'), f'{name} by {spec.column}', [('Class', 'text'), *SUMMARY_COLUMNS], rows, evidence=evidence),
             table(item_id(name, 'test'), f'{name}: test of differences between classes', TEST_COLUMNS, test_rows, evidence=evidence,
                   note='Effect size: epsilon-squared.'),
             figure(item_id(name, 'box'), f'{name} by {spec.column}', box(rows, f'{name} by {spec.column}', spec.column, name), evidence=evidence)]
    a.results[item_id(name, 'box')] = summary
    return items, effect_row(name, 'numeric', 'Kruskal–Wallis', test, 'epsilon_squared')


def correlation(a, x, y, label):
    result = a.run(f'''SELECT count(*) AS n,corr(rx,ry) AS spearman,corr(x,y) AS pearson FROM
        (SELECT x,y,{auto_stats.average_rank("x")} AS rx,{auto_stats.average_rank("y")} AS ry FROM
        (SELECT {x} AS x,{y} AS y FROM selected) WHERE x IS NOT NULL AND y IS NOT NULL)''', label)
    n, rho, r = result['rows'][0]
    return {'n': n, 'spearman': rho, 'pearson': r, 'p_value': auto_stats.correlation_p_value(rho, n),
            'pearson_p': auto_stats.correlation_p_value(r, n)}, result


def scatter_sample(a, x, y, label, output, name):
    limit = SETTINGS['scatter_sample_rows']; seed = SETTINGS['sample_seed']
    result = a.run(f'''SELECT x,y FROM (SELECT _row_id,{x} AS x,{y} AS y FROM selected) WHERE x IS NOT NULL AND y IS NOT NULL
        ORDER BY hash(_row_id + {seed}),_row_id LIMIT {limit}''', label, limit=limit)
    total = a.run(f'SELECT count(*) FROM selected WHERE {x} IS NOT NULL AND {y} IS NOT NULL', label + ' (complete pairs)')['rows'][0][0]
    if total > limit:
        output['sampling'].append([name, limit, total, seed, 'rows ordered by hash(_row_id + seed)'])
    return result, total


def numeric_numeric(a, spec, name, output):
    x = numeric_expression(name); y = spec.value
    stats_, result = correlation(a, x, y, f'Correlation of {name} and {spec.column}')
    medians = a.run(f'''SELECT bin,min(x) AS lower,max(x) AS upper,count(*) AS n,median(y) AS median,avg(y) AS mean,var_pop(y) AS variance FROM
        (SELECT x,y,ntile({SETTINGS["median_bins"]}) OVER (ORDER BY x,_row_id) AS bin FROM
        (SELECT {x} AS x,{y} AS y,_row_id FROM selected) WHERE x IS NOT NULL AND y IS NOT NULL) GROUP BY 1 ORDER BY 1''',
        f'Median {spec.column} by bins of {name}')
    sample, total = scatter_sample(a, x, y, f'{name} and {spec.column} points', output, name)
    evidence = a.evidence('Spearman = Pearson correlation of average ranks; p from t with n − 2 df', result, medians, sample)
    items = [table(item_id(name, 'test'), f'{name} and {spec.column}: correlation', TEST_COLUMNS, [
        ['Spearman rank correlation', stats_['spearman'], None, stats_['p_value'], stats_['spearman'], stats_['n'], ''],
        ['Pearson correlation (reference)', stats_['pearson'], None, stats_['pearson_p'], stats_['pearson'], stats_['n'], '']], evidence=evidence),
        table(item_id(name, 'medians'), f'{name}: median {spec.column} by {SETTINGS["median_bins"]} equal-count bins', [('Bin', 'count'),
              ('Lowest value', 'number'), ('Highest value', 'number'), ('Rows', 'count'), (f'Median {spec.column}', 'number'), (f'Mean {spec.column}', 'number'),
              ('Variance', 'number')], medians['rows'], evidence=evidence)]
    bins_ = medians['rows']
    if len(bins_) >= 3:
        from scipy import stats as scipy_stats
        n_total = sum(r[3] for r in bins_); grand = sum(r[3] * r[5] for r in bins_) / n_total
        between = sum(r[3] * (r[5] - grand) ** 2 for r in bins_); within = sum(r[3] * (r[6] or 0) for r in bins_)
        k = len(bins_)
        p_value = scipy_stats.f.sf((between / (k - 1)) / (within / (n_total - k)), k - 1, n_total - k) if within > 0 and n_total > k else None
        share = auto_univariate.monotone_share([r[5] for r in bins_], [r[3] for r in bins_])
        eta2 = between / (between + within) if between + within else 0
        if share is not None and p_value is not None and share < DEFAULT_CONFIG.monotone_share_minimum and p_value < 0.01 and eta2 >= 0.01:
            flag_nonlinear(output, items, name, share, f'mean {spec.column} across bins ranges from {min(r[5] for r in bins_):,.4g} to {max(r[5] for r in bins_):,.4g}')
    points = frame_of(sample)
    fig = go.Figure(go.Scattergl(x=points.x, y=points.y, mode='markers', marker={'color': visuals.TEAL, 'size': 5, 'opacity': .55}))
    title = f'{name} and {spec.column}' + (f' ({len(points):,} of {total:,} rows, seed {SETTINGS["sample_seed"]})' if total > len(points) else '')
    items.append(figure(item_id(name, 'scatter'), title, visuals.style(fig, title, name, spec.column), evidence=evidence))
    a.results[item_id(name, 'scatter')] = sample
    return items, {'field': name, 'type': 'numeric', 'test': 'Spearman', 'measure': 'spearman', 'effect': _clean(stats_['spearman']),
                   'p_value': _clean(stats_['p_value']), 'n': stats_['n']}


def date_grain(a, column):
    d = date_expression(column)
    result = a.run(f'SELECT min({d}),max({d}) FROM selected', f'Date range of {column}')
    first, last = result['rows'][0]
    if first is None: raise ValueError(f'{column} has no usable dates.')
    span = (last - first).days
    return ('month' if span <= 731 else 'quarter' if span <= 2922 else 'year'), first, last, result


def period_bounds(period, grain):
    months = {'month': 1, 'quarter': 3, 'year': 12}[grain]
    month = period.month - 1 + months
    end = date(period.year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    return period, end


def completeness(periods, grain, first, last):
    flags = []
    for index, period in enumerate(periods):
        start, end = period_bounds(period, grain); flag = []
        if index == 0 and first > start: flag.append(f'starts {first.isoformat()}')
        if index == len(periods) - 1 and last < end: flag.append(f'ends {last.isoformat()}')
        flags.append('incomplete: ' + ', '.join(flag) if flag else '')
    return flags


def date_versus_target(a, spec, name, *, title_prefix=None, suffix=''):
    grain, first, last, span = date_grain(a, name)
    d = f"date_trunc('{grain}',{date_expression(name)})::DATE"
    where = f'{spec.ok} AND {date_expression(name)} IS NOT NULL' if spec.column else f'{date_expression(name)} IS NOT NULL'
    if spec.kind == 'binary':
        result = a.run(f'SELECT {d} AS period,count(*) AS n,sum({spec.positive_flag}) AS positives FROM selected WHERE {where} GROUP BY 1 ORDER BY 1',
                       f'Positive rate per {grain} of {name}')
        flags = completeness([r[0] for r in result['rows']], grain, first, last)
        rows = [[p, n, pos, pos / n, flag] for (p, n, pos), flag in zip(result['rows'], flags)]
        columns = [('Period', 'text'), ('Rows', 'count'), ('Positives', 'count'), ('Positive rate', 'percent'), ('Flags', 'text')]
        trace = go.Scatter(x=[r[0] for r in rows], y=[r[3] for r in rows], mode='lines+markers', line={'color': visuals.TEAL}); y_title = 'Positive rate'
    elif spec.kind == 'multiclass':
        result = a.run(f'SELECT {d} AS period,{spec.label} AS class,count(*) AS n FROM selected WHERE {where} GROUP BY 1,2 ORDER BY 1,2',
                       f'Class shares per {grain} of {name}')
        frame = pd.DataFrame(result['rows'], columns=['period', 'class', 'n'])
        pivot = frame.pivot(index='period', columns='class', values='n').fillna(0).astype(int)
        classes = list(pivot.columns); flags = completeness(list(pivot.index), grain, first, last)
        rows = [[p, int(r.sum()), *[r[c] / r.sum() for c in classes], flag] for (p, r), flag in zip(pivot.iterrows(), flags)]
        columns = [('Period', 'text'), ('Rows', 'count'), *[(f'{c} share', 'percent') for c in classes], ('Flags', 'text')]
        trace = None; y_title = 'Share of rows'
    elif spec.kind == 'numeric':
        result = a.run(f'SELECT {d} AS period,count(*) AS n,median({spec.value}) AS median FROM selected WHERE {where} GROUP BY 1 ORDER BY 1',
                       f'Median {spec.column} per {grain} of {name}')
        flags = completeness([r[0] for r in result['rows']], grain, first, last)
        rows = [[p, n, m, flag] for (p, n, m), flag in zip(result['rows'], flags)]
        columns = [('Period', 'text'), ('Rows', 'count'), (f'Median {spec.column}', 'number'), ('Flags', 'text')]
        trace = go.Scatter(x=[r[0] for r in rows], y=[r[2] for r in rows], mode='lines+markers', line={'color': visuals.TEAL}); y_title = f'Median {spec.column}'
    else:
        result = a.run(f'SELECT {d} AS period,count(*) AS n FROM selected WHERE {where} GROUP BY 1 ORDER BY 1', f'Rows per {grain} of {name}')
        flags = completeness([r[0] for r in result['rows']], grain, first, last)
        rows = [[p, n, flag] for (p, n), flag in zip(result['rows'], flags)]
        columns = [('Period', 'text'), ('Rows', 'count'), ('Flags', 'text')]
        trace = go.Scatter(x=[r[0] for r in rows], y=[r[1] for r in rows], mode='lines+markers', line={'color': visuals.TEAL}); y_title = 'Rows'
    evidence = a.evidence(f'Grouped by {grain} (chosen from a {(last - first).days:,}-day range); first and last periods checked for completeness', span, result)
    prefix = title_prefix or name
    items = [table(item_id(name, 'periods' + suffix), f'{prefix} per {grain}', columns, rows, evidence=evidence)]
    fig = go.Figure(trace) if trace else go.Figure([go.Bar(x=[r[0] for r in rows], y=[r[2 + i] for r in rows], name=str(c),
                                                           marker_color=visuals.COLORS[i % len(visuals.COLORS)]) for i, c in enumerate(classes)])
    if spec.kind == 'multiclass': fig.update_layout(barmode='stack'); fig.update_yaxes(tickformat='.0%')
    if spec.kind == 'binary': fig.update_yaxes(tickformat='.0%')
    items.append(figure(item_id(name, 'periods-chart' + suffix), f'{prefix} per {grain}', visuals.style(fig, f'{prefix} per {grain}', name, y_title), evidence=evidence))
    a.results[item_id(name, 'periods-chart' + suffix)] = result
    return items


def describe_field(a, name, kind, output):
    if kind == 'date':
        return date_versus_target(a, TargetSpec(None, None, None), name)
    if kind == 'categorical':
        expression, other, top = category_expression(a, name, 'true')
        result = a.run(f'SELECT {expression} AS category,count(*) AS n FROM selected GROUP BY 1', f'Rows per category of {name}')
        total = sum(n for _, n in result['rows'])
        rows = [[category_label(c, other), n, n / total] for c, n in sorted(result['rows'], key=lambda r: category_order(r[0], r[1]))]
        evidence = a.evidence('Count per category', top, result)
        fig = go.Figure(go.Bar(y=[r[0] for r in rows], x=[r[1] for r in rows], orientation='h', marker_color=visuals.TEAL,
                               text=[f'{r[1]:,} · {r[2]:.1%}' for r in rows], textposition='outside', cliponaxis=False))
        fig.update_yaxes(autorange='reversed')
        a.results[item_id(name, 'counts-chart')] = result
        return [table(item_id(name, 'counts'), f'{name}: rows per category', [('Category', 'text'), ('Rows', 'count'), ('Share', 'percent')],
                      rows, evidence=evidence, note=note_other(other)),
                figure(item_id(name, 'counts-chart'), f'{name}: rows per category', visuals.style(fig, f'{name}: rows per category', 'Rows', name), evidence=evidence)]
    x = numeric_expression(name)
    summary = a.run(f'''SELECT count({x}) AS n,count(*)-count({x}) AS missing,min({x}) AS minimum,quantile_cont({x},.25) AS q1,median({x}) AS median,
        quantile_cont({x},.75) AS q3,max({x}) AS maximum,avg({x}) AS mean,stddev_samp({x}) AS std,skewness({x}) AS skewness FROM selected''', f'Summary of {name}')
    histogram = a.engine.distribution(name, SETTINGS['histogram_bins'])
    evidence = a.evidence('Full-column aggregates; equal-width histogram (Engine.distribution)', summary, histogram)
    a.results[item_id(name, 'histogram')] = histogram
    return [table(item_id(name, 'summary'), f'{name}: summary', [(c, 'count' if c in ('n', 'missing') else 'number') for c in summary['columns']],
                  summary['rows'], evidence=evidence),
            figure(item_id(name, 'histogram'), f'{name}: distribution', visuals.histogram(histogram, f'{name} distribution'), evidence=evidence)]


# ---------------- structure ----------------
def structure_stage(a, facts, spec, output, state):
    cfg = state['cfg']
    result = auto_structure.discover(a, facts, cfg, absent_sql)
    # The target is compared with each field elsewhere; associations here are between fields only.
    fields = {name: f for name, f in facts.items() if name != spec.column}
    auto_structure.associations(a, fields, result, cfg, absent_sql)
    state['structure'] = result
    return (f'{len(result.conditional)} conditional, {len(result.dependencies)} dependencies, {len(result.derived)} derived, '
            f'{len(result.clusters)} clusters')


def finish_structure(output, state, has_target):
    """Structure tables, written after ranking so cluster representatives can use predictive power."""
    result = state.get('structure')
    if not result: return
    if not has_target:
        total = max(1, output['rows'])
        auto_structure.choose_representatives(result, None, {n: f['missing'] / total for n, f in (state.get('facts') or {}).items()})
    version = output['analysis_version']

    def evidence(section, computation):
        return {'version': version, 'computation': computation,
                'queries': [{'sql': q['sql'], 'parameters': [str(p) for p in q['parameters']]} for q in result.queries.get(section, [])]}
    items = []
    items.append(table('structure-conditional', 'Conditional categories', [('Column', 'text'), ('Level', 'text'), ('Occurs exactly when', 'text'),
        ('Rows', 'count'), ('Overlap (level → parent)', 'percent'), ('Overlap (parent → level)', 'percent')],
        [[c.column, c.level, f'{c.parent} = {c.parent_value}', c.rows, c.overlap_level, c.overlap_parent] for c in result.conditional],
        evidence=evidence('conditional', f'A level and a value of another column cover the same rows (both overlaps ≥ {state["cfg"].dependency_overlap:.0%})'),
        note='Target comparisons for these columns are run on applicable rows, where the parent column does not have that value.'))
    items.append(table('structure-dependencies', 'Functional dependencies', [('Column', 'text'), ('Determines', 'text'), ('Kind', 'text'),
        ('Groups checked', 'count'), ('Groups with one value', 'percent')],
        [[d.determinant, d.dependent, d.kind, d.groups_checked, d.share_consistent] for d in result.dependencies],
        evidence=evidence('dependencies', 'Groups of the first column with at least two rows, and how many have a single value of the second')))
    items.append(table('structure-derived', 'Approximately derived columns', [('Column', 'text'), ('Formula', 'text'), ('Factor k', 'number'),
        ('R² (sample, trimmed)', 'effect'), ('R² (all rows)', 'effect'), ('Rows', 'count')],
        [[d.column, f'{d.column} ≈ k × ({d.formula})', d.factor, d.r2_sample, d.r2_full, d.rows] for d in result.derived],
        evidence=evidence('derived', f'k fitted through the origin; trimmed R² on a seeded sample of {state["cfg"].derived_sample_rows:,} rows, '
                          f'confirmed with R² ≥ {state["cfg"].derived_r2} on all complete rows')))
    pairs = sorted(result.associations, key=lambda p: (-abs(p.value), p.first, p.second))
    items.append(table('structure-associations', 'Associations between fields, largest first', [('Field', 'text'), ('Field', 'text'), ('Measure', 'text'),
        ('Value', 'effect'), ('n', 'count'), ('Rows', 'text')], [[p.first, p.second, p.measure, p.value, p.n, p.subset or 'all rows'] for p in pairs[:20]],
        evidence=evidence('associations', "Bias-corrected Cramér's V, Spearman ρ or correlation ratio η for every pair of checked fields"),
        note='The 20 largest of ' + f'{len(pairs):,} pairs. Pairs that include a conditional column use its applicable rows.'))
    items.append(table('structure-clusters', 'Redundancy clusters', [('Members', 'text'), ('Representative', 'text'), ('Chosen by', 'text')],
        [[', '.join(c.members), c.representative, c.reason] for c in result.clusters],
        evidence={'version': version, 'computation': f'Average-linkage clustering on 1 − |association|, cut at association {state["cfg"].redundancy_threshold}',
                  'queries': []}))
    items += [text(limit) for limit in result.limits]
    output['sections']['structure'] = items
    output['structure'] = result.to_dict()


def explained_missing(a, facts, cfg, output):
    explanations, queries = auto_quality.explain_missing(a, facts, cfg, missing_sql, display_value)
    output['missing_explanations'] = [e.to_dict() for e in explanations]
    return table('missing-explanations', 'Explained missing values', [('Column', 'text'), ('Missing rows', 'count'), ('Condition', 'text'),
        ('Rows meeting the condition', 'count'), ('Coverage', 'percent'), ('Lift', 'number'), ('Precision', 'percent'), ('Missing ⇔ condition', 'text'),
        ('Statement', 'text')],
        [[e.column, e.missing_rows, f'{e.condition_column} = {e.condition_value}' if e.condition_value != '(missing)' else f'{e.condition_column} is missing',
          e.condition_rows, e.coverage, e.lift, e.precision, 'yes' if e.equivalent else 'no', e.statement()] for e in explanations],
        evidence=a.evidence('Most common value of every other column within the missing rows, then condition counts', *queries),
        note=f'Listed only when the condition covers at least {cfg.missing_explanation_coverage:.0%} of the missing rows, has lift of at least '
             f'{cfg.missing_explanation_lift:g} and at least {cfg.missing_explanation_precision:.0%} of the rows meeting it are missing. '
             'Reported only; no values are filled.')


# ---------------- leakage ----------------
def leakage_stage(a, spec, facts, output, state):
    cfg = state['cfg']
    candidates = {n: f for n, f in facts.items() if n != spec.column}
    suspects, queries = auto_leakage.screen(a, spec, state.get('powers', {}), candidates, cfg, absent_sql,
                                            lambda c: f'trim(CAST({quote(c)} AS VARCHAR))', state.get('time_axis'))
    output['leakage'] = [s.to_dict() for s in suspects]
    for suspect in suspects:
        output['warnings'].append(f'Possible leakage: {suspect.field} ({"; ".join(suspect.reasons)}). It is excluded from the multivariate model.')
    output['sections']['leakage'] = [table('leakage-suspects', 'Leakage suspects', [('Field', 'text'), ('Reasons', 'text'), ('Power metric', 'text'),
        ('Predictive power alone', 'effect')], [[s.field, '; '.join(s.reasons), s.power_metric, s.power] for s in suspects],
        evidence=a.evidence('Single-field cross-validated power, exact dependency checks with the target and date-name hints', *queries),
        note=f'A field is a suspect when it alone reaches AUC ≥ {cfg.leakage_auc} (R² ≥ {cfg.leakage_r2} for numeric targets), when it determines '
             'the target or takes one value per target class, when a date name suggests it is recorded after the outcome (a weak hint), or, with a '
             f'time axis, when a date falls after the time axis on at least {cfg.temporal_leakage_share:.0%} of rows. '
             'Suspects stay in the ranking with a flag and are excluded from the multivariate model.')]
    return f'{len(suspects)} suspects'


def multivariate_stage(a, spec, facts, time_axis, output, state):
    cfg = state['cfg']; structure = state.get('structure')
    suspects = {s['field'] for s in output.get('leakage', [])}
    ranked = [e for e in output.get('ranking', []) if e['field'] not in suspects]
    fields = [e['field'] for e in ranked]
    clusters = structure.clusters if structure else []
    result, queries = auto_multivariate.analyse(a, spec, fields, facts, cfg, time_axis, absent_sql,
                                                lambda c: f'trim(CAST({quote(c)} AS VARCHAR))', clusters, ranked,
                                                {c.column: c.level for c in structure.conditional} if structure else None,
                                                {n['field'] for n in output.get('nonlinear', [])})
    evidence = {'version': output['analysis_version'], 'computation': (
        f'HistGradientBoosting ({cfg.model_iterations} iterations, seed {cfg.seed}), {cfg.cv_folds}-fold cross-validation; permutation importance on at most '
        f'{cfg.permutation_rows:,} held-out rows per fold, {cfg.permutation_repeats} repeats'), 'queries': [{'sql': q['sql'], 'parameters': []} for q in queries]}
    items = [text(note) for note in result.notes]
    items.append(table('multivariate-metrics', 'Model performance against the baseline (cross-validated)', [('Measure', 'text'), ('Model', 'effect'),
        ('Fold sd', 'effect'), ('Baseline', 'effect'), ('Per fold', 'text')], result.metrics, evidence=evidence,
        note='Baselines: AUC 0.5; accuracy of always predicting the majority class; class shares as predicted probabilities; '
             'the training mean (R²) or median (MAE) for numeric targets.'))
    if not result.no_signal:
        items.append(table('multivariate-importance', 'Permutation importance (drop in held-out score when a field or cluster is shuffled)',
            [('Field or cluster', 'text'), ('Columns shuffled together', 'text'), ('Mean drop', 'effect'), ('sd', 'effect'), ('Repeats × folds', 'count')],
            result.importance, evidence=evidence,
            note='Shuffling a cluster together shows the information its members share; shuffling one member alone can understate it.'))
        lr = spec.kind != 'multiclass'
        items.append(table('multivariate-verdicts', 'Univariate vs adjusted', [('Field', 'text'),
            ('Univariate LR χ²' if lr else 'Univariate power', 'number'), ('Adjusted LR χ²' if lr else 'Adjusted importance', 'number'),
            ('df', 'count'), ('Effect retained', 'percent'), ('Adjusted p', 'p'), ('Univariate direction', 'text'), ('Adjusted direction', 'text'),
            ('Verdict', 'text'), ('Note', 'text')], result.verdicts, evidence=evidence,
            note=(f'Likelihood-ratio contribution of each field alone and after the other fields in the adjusted model. Effect retained = '
                  f'√(adjusted χ² / univariate χ²), because χ² grows with the square of an effect. Holds: at least {cfg.weakens_share:.0%} retained. '
                  'Weakens substantially: less. Disappears: adjusted '
                  'p ≥ 0.05. Reverses: the adjusted coefficient has the opposite sign, both intervals excluding no effect (numeric fields). '
                  'Directions: + higher values go with the positive class or larger target, − the opposite, 0 interval includes no effect.') if lr else
                 'Multiclass: univariate cross-validated power vs permutation importance in the model (AUC units); AUC gains are not additive, '
                 'so these verdicts understate fields that hold.'))
        if result.regression or result.regression_note:
            label = 'Odds ratio' if spec.kind == 'binary' else 'Coefficient'
            items.append(table('multivariate-adjusted', 'Adjusted model (holding the other listed fields constant)', [('Field', 'text'), ('Term', 'text'),
                (label, 'number'), ('95% CI low', 'number'), ('95% CI high', 'number'), ('p-value', 'p')], result.regression, evidence=evidence,
                note=result.regression_note))
        if result.dropped_vif:
            items.append(table('multivariate-vif', f'Fields left out of the adjusted model (VIF above {cfg.vif_maximum:g})', [('Field', 'text'), ('VIF', 'number')],
                               result.dropped_vif, evidence=evidence))
        for name, curve in result.ale.items():
            fig = go.Figure(go.Scatter(x=curve['upper_edges'], y=curve['ale'], mode='lines+markers', line={'color': visuals.TEAL}))
            fig.add_hline(y=0, line_dash='dot', line_color=visuals.AMBER)
            label = 'probability' if spec.kind == 'binary' else spec.column
            items.append(figure(item_id(name, 'ale'), f'{name}: accumulated local effect', visuals.style(fig, f'{name}: accumulated local effect on predicted {label}',
                                name, f'Effect on predicted {label}'), evidence=evidence))
    output['sections']['multivariate'] = items
    state['model_data'] = result.data
    output['multivariate'] = deep_clean({'metrics': result.metrics, 'no_signal': result.no_signal, 'signal': result.signal, 'importance': result.importance,
                              'verdicts': result.verdicts, 'regression': result.regression, 'dropped_vif': result.dropped_vif, 'ale': result.ale,
                              'sample_rows': result.sample_rows, 'total_rows': result.total_rows, 'fields': result.fields})
    if result.total_rows > result.sample_rows:
        output['sampling'].append(['multivariate model', result.sample_rows, result.total_rows, cfg.seed, 'rows ordered by hash(_row_id + seed)'])
    return f'{len(fields)} fields; ' + ('little joint signal' if result.no_signal else f'model gain {result.signal:.3f}')


# ---------------- confounding, interactions and segments ----------------
def model_fields(output, state, facts):
    """Ranked, non-leakage fields with each redundancy cluster represented once; derived fields are already excluded."""
    suspects = {s['field'] for s in output.get('leakage', [])}
    structure = state.get('structure')
    others = {m for c in (structure.clusters if structure else []) for m in c.members if m != c.representative}
    return [e['field'] for e in output.get('ranking', []) if e['field'] not in suspects and e['field'] not in others and e['field'] in facts]


def confounding_stage(a, spec, facts, output, state):
    cfg = state['cfg']; structure = state.get('structure')
    # Overlapping fields are exactly what this screen examines, so redundancy clusters are not collapsed here.
    suspects = {s['field'] for s in output.get('leakage', [])}
    ranked = [e['field'] for e in output.get('ranking', []) if e['field'] not in suspects and e['field'] in facts]
    kinds = {n: 'numeric' if facts[n]['type'] == 'numeric' else 'categorical' for n in ranked}
    applicable = {c.column: c.applicable_where for c in structure.conditional} if structure else {}
    checks, queries, note = auto_confounding.screen(a, spec, ranked, kinds, cfg, absent_sql, applicable)
    evidence = a.evidence('Exact counts per stratum, exposure and target on all rows; CMH (statsmodels StratifiedTable) or weighted mean differences', *queries)
    items = [text(note)] if note else []
    ratio = spec.kind == 'binary'
    items.append(table('confounding-checks', 'Confounding screen: each field compared within levels of another field', [('Field', 'text'),
        ('Comparison', 'text'), ('Stratified by', 'text'), ('Crude ' + ('odds ratio' if ratio else 'difference'), 'number'),
        ('Stratified ' + ('odds ratio (CMH)' if ratio else 'difference'), 'number'), ('95% CI low', 'number'), ('95% CI high', 'number'),
        ('p-value', 'p'), ('Homogeneity p (Breslow–Day)' if ratio else 'Homogeneity p', 'p'), ('Strata', 'count'), ('Flag', 'text')],
        [[c.field, c.comparison, c.stratifier, c.crude, c.stratified, c.low, c.high, c.p_value, c.homogeneity_p, c.strata_used, c.flag] for c in checks],
        evidence=evidence, note=f'The {cfg.confounding_fields} highest-ranked fields, each within the strata of the next {cfg.confounding_strata} '
        'ranked fields (numbers in quartiles, categories by their 5 most frequent levels). Reverses: the direction changes and the stratified '
        'interval excludes no effect. Drops by more than half: the stratified effect is under half the crude one (on the log scale for odds ratios). '
        'Statistical associations, not causes.'))
    for c in [c for c in checks if c.flag][:10]:
        items.append(table(item_id(f'{c.field}-{c.stratifier}', 'strata'), f'{c.comparison}, within {c.stratifier}', [(c.stratifier, 'text'),
            ('Rows in comparison group', 'count'), ('Rows in other group', 'count'), ('Odds ratio' if ratio else 'Difference', 'number')],
            c.strata, evidence=evidence, note=f'Crude {c.crude:,.3g}; stratified {c.stratified:,.3g}; flag: {c.flag}.'))
    output['confounding'] = deep_clean([vars(c) for c in checks])
    interactions = []; data = state.get('model_data')
    if data is not None:
        fields = [f for f in ranked if f in data.fields][:cfg.interaction_fields]
        rows, note = auto_confounding.interaction_screen(data.raw, data.y, spec.kind, fields, {f for f in fields if facts[f]['type'] == 'numeric'}, cfg,
                                                         data.time_ordered)
        state['interactions'] = [(r[0], r[1]) for r in rows if r[4]]
        if note: items.append(text(note))
        interactions = rows
        model_evidence = {'version': output['analysis_version'], 'computation': f'For each pair: {cfg.cv_folds}-fold cross-validated '
                          + ('L2 logistic regression (AUC)' if spec.kind == 'binary' else 'ridge regression (R²)') + ' with the two fields as separate levels '
                          'vs their joint levels; numbers in quintiles, categories by their most frequent levels', 'queries': []}
        items.append(table('interactions', 'Interaction screen: gain from joint levels over separate effects', [('Field', 'text'), ('Field', 'text'),
            ('Gain (' + ('ΔAUC' if spec.kind == 'binary' else 'ΔR²') + ')', 'effect'), ('Standard error', 'effect'), ('LR p-value', 'p'),
            ('Benjamini–Hochberg adjusted p', 'p'), ('Reported', 'text')],
            [[r[0], r[1], r[2], r[3], r[5], r[6], 'yes' if r[4] else ''] for r in rows], evidence=model_evidence,
            note=f'Reported when the likelihood-ratio test of the joint levels (vs separate levels) has a Benjamini–Hochberg adjusted p below 0.05 '
                 f'across all pairs, and the cross-validated gain is at least {cfg.interaction_minimum} and more than twice its standard error.'))
        for first, second, *_ in [r for r in rows if r[4]][:cfg.interactions_shown]:
            kinds2 = {first: kinds[first], second: kinds[second]}
            query = auto_confounding.two_way_table(a, spec, first, second, kinds2, absent_sql)
            items.append(table(item_id(f'{first}-{second}', 'interaction'), f'{first} × {second}: target by joint levels', [(first, 'text'), (second, 'text'),
                ('Rows', 'count'), ('Positive rate' if spec.kind == 'binary' else f'Mean {spec.column}', 'percent' if spec.kind == 'binary' else 'number')],
                query['rows'], evidence=a.evidence('Counts and target by joint levels on all rows', query)))
    output['interactions'] = deep_clean([{'first': r[0], 'second': r[1], 'gain': r[2], 'se': r[3], 'reported': r[4], 'p_value': r[5], 'adjusted_p': r[6]}
                                         for r in interactions])
    output['sections']['confounding'] = items
    return f'{sum(1 for c in checks if c.flag)} flagged of {len(checks)} checks; {sum(1 for r in interactions if r[4])} interactions'


def segments_stage(a, spec, facts, output, state):
    cfg = state['cfg']; data = state.get('model_data')
    if data is None: return 'no model sample'
    fields = [f for f in model_fields(output, state, facts) if f in data.fields]
    joint = [(x, y) for x, y in state.get('interactions', []) if facts[x]['type'] != 'numeric' and facts[y]['type'] != 'numeric' and x in data.fields and y in data.fields]
    segments, note = auto_segments.discover(a, spec, data.raw, data.y, fields, {f for f in fields if facts[f]['type'] == 'numeric'}, cfg, absent_sql, joint,
                                            data.time_ordered)
    kept = [s for s in segments if s.kept]
    overall, queries = auto_segments.full_data(a, spec, kept) if kept else (None, [])
    output['segment_overall'] = _clean(overall) if overall is not None else None
    higher = sorted([s for s in kept if s.direction == 'higher'], key=lambda s: (-s.impact, s.rule))[:cfg.segments_high]
    lower = sorted([s for s in kept if s.direction == 'lower'], key=lambda s: (-s.impact, s.rule))[:cfg.segments_low]
    evidence = a.evidence(f'Decision tree (depth {cfg.segment_depth}, minimum leaf max({cfg.segment_min_rows}, {cfg.segment_min_share:.0%} of rows), seed {cfg.seed}); '
                          'rules re-counted on every row', *queries)
    binary = spec.kind == 'binary'
    columns = [('Rule', 'text'), ('Rows', 'count'), ('Share of rows', 'percent'), ('Rate' if binary else 'Mean', 'percent' if binary else 'number'),
               ('95% CI low', 'percent' if binary else 'number'), ('95% CI high', 'percent' if binary else 'number'), ('Lift', 'number'),
               ('Share of all positives' if binary else 'Share of target total', 'percent'), ('Held-out rows', 'count'),
               ('Held-out ' + ('rate' if binary else 'mean'), 'percent' if binary else 'number'), ('Held-out lift', 'number')]

    def rows(group):
        return [[s.rule, s.rows, s.share_of_rows, s.rate, s.low, s.high, s.lift, s.share_of_target, s.held_out_rows, s.held_out_rate, s.held_out_lift]
                for s in group]
    items = [text(note)]
    items.append(table('segments-higher', 'Segments with a higher ' + ('rate' if binary else 'mean'), columns, rows(higher), evidence=evidence,
                       note=f'Validated on held-out rows: lift at least {cfg.segment_lift_minimum} and a held-out 95% interval excluding the overall '
                            f'{"rate" if binary else "mean"}. Ordered by rows × |difference from overall|. Rates and sizes are on every row.'))
    items.append(table('segments-lower', 'Segments with a lower ' + ('rate' if binary else 'mean'), columns, rows(lower), evidence=evidence))
    items.append(table('segments-dropped', 'Tree leaves that did not pass validation', [('Rule', 'text'), ('Held-out rows', 'count'),
        ('Held-out lift', 'number'), ('Reason', 'text')], [[s.rule, s.held_out_rows, s.held_out_lift, s.reason] for s in segments if not s.kept],
        evidence=evidence))
    output['segments'] = deep_clean([vars(s) | {'conditions': [list(c) for c in s.conditions]} for s in higher + lower])
    output['sections']['segments'] = items
    return f'{len(higher)} higher, {len(lower)} lower of {len(segments)} leaves'


# ---------------- findings ----------------
def findings_stage(output, state):
    cfg = state['cfg']
    findings = auto_findings.build(output, cfg)
    output['findings'] = deep_clean([f.to_dict() for f in findings])
    counts = {s: sum(1 for f in findings if f.severity == s) for s in ('critical', 'important', 'informative')}
    output['finding_counts'] = counts
    return ', '.join(f'{n} {s}' for s, n in counts.items())


# ---------------- stage 6 ----------------
MEASURE_COLUMNS = [('cramers_v', "Cramér's V"), ('rank_biserial', '|rank-biserial r|'), ('epsilon_squared', 'epsilon-squared'), ('spearman', '|Spearman ρ|')]


def ranking_stage(output, state):
    effects = [e for e in state.get('effects', []) if e.get('power') is not None or e['effect'] is not None]
    adjusted = auto_stats.benjamini_hochberg([e['p_value'] for e in effects])
    for effect, q in zip(effects, adjusted): effect['adjusted_p'] = q
    structure = state.get('structure')
    ranked = [e for e in effects if not e.get('derived')]
    derived = [e for e in effects if e.get('derived')]
    ranked.sort(key=lambda e: (e.get('power') is None, -(e.get('power') or 0), e['field']))
    state['ranked'] = [e['field'] for e in ranked]
    # Field sections follow the ranking; derived and untested fields keep column order after them.
    order = {name: index for index, name in enumerate(state['ranked'])}
    fields = output['sections'].get('fields', [])
    output['sections']['fields'] = sorted(fields, key=lambda f: (f['field'] not in order, order.get(f['field'], 0), fields.index(f)))
    if structure:
        auto_structure.choose_representatives(structure, {e['field']: e.get('power') for e in effects}, {})
    rows = []
    for rank, e in enumerate(ranked, 1):
        measures = [abs(e['effect']) if e['effect'] is not None and e['measure'] == key else None for key, _ in MEASURE_COLUMNS]
        cluster = structure.cluster_for(e['field']) if structure else None
        cluster_text = '' if not cluster else ('representative of ' if cluster.representative == e['field'] else 'with ') + ', '.join(
            m for m in cluster.members if m != e['field'])
        suspect = next((s for s in output.get('leakage', []) if s['field'] == e['field']), None)
        rows.append([rank, e['field'], e['type'], e.get('subset', 'all rows'), e.get('power_metric'), e.get('power'), e.get('power_sd'),
                     e.get('nmi_adjusted'), e['test'], e['n'], *measures, e['p_value'], e['adjusted_p'], cluster_text,
                     'yes: ' + '; '.join(suspect['reasons']) if suspect else ''])
    columns = [('Rank', 'count'), ('Field', 'text'), ('Field type', 'text'), ('Rows analysed', 'text'), ('Power metric', 'text'),
               ('Predictive power', 'effect'), ('Fold sd', 'effect'), ('NMI minus permutation mean', 'effect'), ('Test', 'text'), ('n', 'count'),
               *[(label, 'effect') for _, label in MEASURE_COLUMNS], ('p-value', 'p'), ('Benjamini–Hochberg adjusted p', 'p'), ('Redundancy cluster', 'text'),
               ('Leakage suspect', 'text')]
    items = [table('ranking', 'Fields ranked by cross-validated predictive power: statistical associations, not causes', columns, rows,
        evidence={'version': output['analysis_version'], 'computation': 'Predictive power, effect sizes and p-values from each field section; '
                  'Benjamini–Hochberg adjustment across all ranked fields', 'queries': []},
        note='Predictive power is comparable across field types: out-of-fold ΔAUC (AUC − 0.5) for classification, out-of-fold R² for regression, '
             'from a model that uses only that field. The test-specific effect sizes are detail and are not directly comparable with each other. '
             'Conditional fields are measured on the rows where they apply.')]
    if derived:
        items.append(table('ranking-derived', 'Approximately derived columns (not ranked separately)', [('Field', 'text'), ('Formula', 'text'),
            ('Predictive power', 'effect'), ('p-value', 'p'), ('Benjamini–Hochberg adjusted p', 'p')],
            [[e['field'], e['derived'], e.get('power'), e['p_value'], e['adjusted_p']] for e in derived],
            evidence={'version': output['analysis_version'], 'computation': 'See the structure section', 'queries': []}))
    undefined = [[e['field'], e['test']] for e in state.get('effects', []) if e['effect'] is None]
    if undefined:
        items.append(table('ranking-undefined', 'Fields with an undefined test', [('Field', 'text'), ('Test', 'text')],
            undefined, evidence={'version': output['analysis_version'], 'computation': 'Test undefined for the data', 'queries': []}))
    output['sections']['ranking'] = items
    output['ranking'] = [{k: v for k, v in e.items()} for e in ranked]
    return f'{len(rows)} fields ranked'


# ---------------- stage 7 ----------------
def relationships_stage(a, spec, facts, time_axis, output, state):
    if spec.column:
        fields = state.get('ranked', [])[:SETTINGS['relationship_fields']]
        basis = f'the {SETTINGS["relationship_fields"]} highest-ranked fields'
    else:
        fields = [n for n, f in facts.items() if f['type'] in ('categorical', 'binary', 'numeric') and n != time_axis][:SETTINGS['descriptive_relationship_fields']]
        basis = f'the first {SETTINGS["descriptive_relationship_fields"]} categorical or numeric fields in column order'
    if len(fields) < 2:
        output['sections']['relationships'] = [text(f'Fewer than two fields are available ({basis}).')]; return 'fewer than two fields'
    kinds = {n: ('numeric' if facts[n]['type'] == 'numeric' else 'categorical') for n in fields}
    pairs = []; queries = []
    for i, first in enumerate(fields):
        for second in fields[i + 1:]:
            value, measure, n, results, (low, high) = association(a, first, second, kinds)
            # Rounded before sorting, so last-bit differences from parallel sums cannot reorder near-ties.
            pairs.append([first, second, measure, _clean(value), _clean(low), _clean(high), n]); queries += results
    evidence = a.evidence("Cramér's V for categorical pairs, Spearman ρ for numeric pairs, correlation ratio η for mixed pairs", *queries)
    matrix = pd.DataFrame(1.0, index=fields, columns=fields)
    for first, second, _, value, *_ in pairs:
        matrix.loc[first, second] = matrix.loc[second, first] = value
    rows = [[f, *[matrix.loc[f, g] for g in fields]] for f in fields]
    ordered = sorted([p for p in pairs if p[3] is not None], key=lambda p: (-abs(p[3]), p[0], p[1]))
    items = [text(f'Fields compared: {basis}.'),
             table('relationships-matrix', 'Associations between fields', [('Field', 'text'), *[(f, 'effect') for f in fields]], rows, evidence=evidence,
                   note="Categorical pairs: Cramér's V. Numeric pairs: Spearman ρ. Mixed pairs: correlation ratio η."),
             table('relationships-pairs', 'Field pairs by association', [('Field', 'text'), ('Field', 'text'), ('Measure', 'text'),
                   ('Value', 'effect'), ('95% CI low', 'effect'), ('95% CI high', 'effect'), ('n', 'count')], ordered, evidence=evidence,
                   note="Intervals: Fisher z with the Bonett–Wright standard error for Spearman ρ; inversion of the noncentral χ² (Cramér's V, "
                        'for the uncorrected V) and noncentral F (η). They describe sampling uncertainty only.')]
    fig = px.imshow(matrix.abs(), color_continuous_scale='Teal', zmin=0, zmax=1, text_auto='.2f', aspect='auto')
    items.append(figure('relationships-heatmap', 'Associations between fields', visuals.style(fig, 'Associations between fields (absolute values)'), evidence=evidence))
    for first, second, measure, value, *_ in ordered[:SETTINGS['relationship_pairs_shown']]:
        items += pair_detail(a, first, second, kinds, output)
    output['sections']['relationships'] = items
    return f'{len(pairs)} pairs'


def association(a, first, second, kinds, confidence=0.95):
    """(value, measure, n, queries, (low, high)) for one pair of fields."""
    kind = (kinds[first], kinds[second])
    if kind == ('numeric', 'numeric'):
        stats_, result = correlation(a, numeric_expression(first), numeric_expression(second), f'Correlation of {first} and {second}')
        return stats_['spearman'], 'Spearman ρ', stats_['n'], [result], auto_stats_ci.spearman_interval(stats_['spearman'], stats_['n'], confidence)
    if kind == ('categorical', 'categorical'):
        left, _, top_left = category_expression(a, first, 'true'); right, _, top_right = category_expression(a, second, 'true')
        result = a.run(f'SELECT {left} AS a,{right} AS b,count(*) AS n FROM selected GROUP BY 1,2', f'{first} × {second}')
        frame = pd.DataFrame(result['rows'], columns=['a', 'b', 'n']).pivot(index='a', columns='b', values='n').fillna(0)
        chi = auto_stats.chi_square(frame.to_numpy())
        n = int(frame.to_numpy().sum())
        interval = auto_stats_ci.cramers_v_interval(chi['statistic'], chi['df'], n, min(frame.shape), confidence) if chi else (None, None)
        return (chi['cramers_v'] if chi else None), "Cramér's V", n, [top_left, top_right, result], interval
    category, number = (first, second) if kind[0] == 'categorical' else (second, first)
    expression, _, top = category_expression(a, category, 'true'); x = numeric_expression(number)
    groups = a.run(f'SELECT {expression} AS g,count(v) AS n,avg(v) AS mean FROM (SELECT *,{x} AS v FROM selected) WHERE v IS NOT NULL GROUP BY 1',
                   f'{number} by {category}')
    total = a.run(f'SELECT count({x}),avg({x}),var_pop({x}) FROM selected', f'{number} overall')
    n, mean, variance = total['rows'][0]
    eta = auto_stats.correlation_ratio([(g_n, g_mean) for _, g_n, g_mean in groups['rows']], n, mean, variance)
    return eta, 'correlation ratio η', n, [top, groups, total], auto_stats_ci.eta_interval(eta, len(groups['rows']), n, confidence)


def pair_detail(a, first, second, kinds, output):
    title = f'{first} and {second}'
    kind = (kinds[first], kinds[second])
    if kind == ('numeric', 'numeric'):
        x, y = numeric_expression(first), numeric_expression(second)
        sample, total = scatter_sample(a, x, y, f'{title} points', output, title)
        points = frame_of(sample)
        label = title + (f' ({len(points):,} of {total:,} rows, seed {SETTINGS["sample_seed"]})' if total > len(points) else '')
        fig = go.Figure(go.Scattergl(x=points.x, y=points.y, mode='markers', marker={'color': visuals.TEAL, 'size': 5, 'opacity': .55}))
        key = item_id(f'{first}-{second}', 'pair-scatter'); a.results[key] = sample
        return [figure(key, label, visuals.style(fig, label, first, second), evidence=a.evidence('Deterministic sample of complete pairs', sample))]
    if kind == ('categorical', 'categorical'):
        left, other_left, top_left = category_expression(a, first, 'true'); right, other_right, top_right = category_expression(a, second, 'true')
        result = a.run(f'SELECT {left} AS a,{right} AS b,count(*) AS n FROM selected GROUP BY 1,2', f'{first} × {second}')
        frame = pd.DataFrame(result['rows'], columns=['a', 'b', 'n'])
        frame['a'] = frame.a.map(lambda v: category_label(v, other_left)); frame['b'] = frame.b.map(lambda v: category_label(v, other_right))
        pivot = frame.pivot(index='a', columns='b', values='n').fillna(0).astype(int)
        pivot = pivot.loc[pivot.sum(axis=1).sort_values(ascending=False, kind='stable').index, pivot.sum().sort_values(ascending=False, kind='stable').index]
        evidence = a.evidence('Two-dimensional counts', top_left, top_right, result)
        key = item_id(f'{first}-{second}', 'pair-heatmap'); a.results[key] = result
        return [table(item_id(f'{first}-{second}', 'pair-table'), f'{first} × {second}: rows', [(first, 'text'), *[(str(c), 'count') for c in pivot.columns]],
                      [[i, *r.tolist()] for i, r in pivot.iterrows()], evidence=evidence),
                figure(key, f'{first} × {second}: rows', heatmap(pivot, f'{first} × {second}: rows', second, first), evidence=evidence)]
    category, number = (first, second) if kind[0] == 'categorical' else (second, first)
    expression, other, top = category_expression(a, category, 'true')
    summary = a.run(group_summary_sql(expression, numeric_expression(number), 'true'), f'{number} by {category}')
    rows = [[category_label(r[0], other), *r[1:]] for r in sorted(summary['rows'], key=lambda r: category_order(r[0], r[1]))]
    evidence = a.evidence('Exact quantiles per category', top, summary)
    key = item_id(f'{category}-{number}', 'pair-box'); a.results[key] = summary
    return [table(item_id(f'{category}-{number}', 'pair-summary'), f'{number} by {category}', [(category, 'text'), *SUMMARY_COLUMNS], rows, evidence=evidence),
            figure(key, f'{number} by {category}', box(rows, f'{number} by {category}', category, number), evidence=evidence)]


# ---------------- text and geo fields ----------------
def text_field(a, spec, name, cfg):
    """Length, emptiness, frequent tokens and bigrams, detected language, and length or presence against the target."""
    value = f'trim(CAST({quote(name)} AS VARCHAR))'
    has_text = f"(NOT {absent_sql(name)} AND {value} <> '')"
    summary = a.run(f'''SELECT count(*) AS rows,count(*) FILTER (WHERE {has_text}) AS with_text,
        min(length({value})) FILTER (WHERE {has_text}) AS min_chars,quantile_cont(length({value}),.25) FILTER (WHERE {has_text}) AS q1_chars,
        median(length({value})) FILTER (WHERE {has_text}) AS median_chars,quantile_cont(length({value}),.75) FILTER (WHERE {has_text}) AS q3_chars,
        max(length({value})) FILTER (WHERE {has_text}) AS max_chars,
        median(len(regexp_split_to_array(trim({value}), '\\s+'))) FILTER (WHERE {has_text}) AS median_words FROM selected''', f'Text lengths of {name}')
    rows, with_text, *lengths, median_words = summary['rows'][0]
    tokens = f"regexp_split_to_array(lower({value}), '{auto_textgeo.TOKEN_PATTERN}')"
    keep = "length(tok) >= 2 AND NOT regexp_full_match(tok, '[0-9]+')"
    sample = a.run(f'''SELECT tok,count(*) AS n FROM (SELECT unnest({tokens}) AS tok FROM selected WHERE {has_text}) WHERE {keep}
        GROUP BY 1 ORDER BY n DESC,tok LIMIT 2000''', f'Frequent tokens of {name} (language detection)', limit=2000)
    language, share = auto_textgeo.detect_language({t: n for t, n in sample['rows']})
    stop = ','.join(literal(w) for w in sorted(auto_textgeo.stopwords_for(language)))
    top_tokens = a.run(f'''SELECT tok,count(*) AS n,count(DISTINCT rid) AS docs FROM (SELECT _row_id AS rid,unnest({tokens}) AS tok FROM selected WHERE {has_text})
        WHERE {keep} AND tok NOT IN ({stop}) GROUP BY 1 ORDER BY n DESC,tok LIMIT 20''', f'Top tokens of {name}')
    bigrams = a.run(f'''SELECT t[i] || ' ' || t[i + 1] AS bigram,count(*) AS n FROM (SELECT t,unnest(range(1, len(t))) AS i FROM
        (SELECT list_filter({tokens}, x -> x <> '') AS t FROM selected WHERE {has_text}))
        WHERE length(t[i]) >= 2 AND length(t[i + 1]) >= 2 AND t[i] NOT IN ({stop}) AND t[i + 1] NOT IN ({stop})
        AND NOT regexp_full_match(t[i], '[0-9]+') AND NOT regexp_full_match(t[i + 1], '[0-9]+') GROUP BY 1 ORDER BY n DESC,bigram LIMIT 15''',
        f'Top bigrams of {name}')
    evidence = a.evidence(f'Tokens: lower-cased runs of letters or digits; stop-words of the detected language ({language}) removed', summary, sample, top_tokens, bigrams)
    labels = ['Rows', 'Rows with text', 'Empty or missing', 'Characters: minimum', 'Characters: lower quartile', 'Characters: median',
              'Characters: upper quartile', 'Characters: maximum', 'Words: median', 'Detected language (stop-word share)']
    values = [f'{rows:,}', f'{with_text:,}', f'{rows - with_text:,} ({(rows - with_text) / max(rows, 1):.1%})',
              *[f'{v:,.0f}' if v is not None else '' for v in lengths], f'{median_words:,.0f}' if median_words is not None else '', f'{language} ({share:.1%})']
    items = [text(f'{name} is free text: it is described by length and frequent words; each value is not compared as a category.'),
             table(item_id(name, 'text-summary'), f'{name}: text summary', [('Measure', 'text'), ('Value', 'text')], [list(r) for r in zip(labels, values)],
                   evidence=evidence),
             table(item_id(name, 'text-tokens'), f'{name}: most frequent words', [('Word', 'text'), ('Occurrences', 'count'), ('Rows containing it', 'count'),
                   ('Share of rows with text', 'percent')], [[t, n, d, d / max(with_text, 1)] for t, n, d in top_tokens['rows']], evidence=evidence),
             table(item_id(name, 'text-bigrams'), f'{name}: most frequent word pairs', [('Word pair', 'text'), ('Occurrences', 'count')],
                   [list(r) for r in bigrams['rows']], evidence=evidence)]
    if not spec.column or spec.kind == 'multiclass' or not with_text: return items
    edges_q = a.run(f'SELECT quantile_cont(length({value}),[0.2,0.4,0.6,0.8]) FROM selected WHERE {has_text} AND {spec.ok}', f'Length quintiles of {name}')
    edges = sorted({int(round(e)) for e in (edges_q['rows'][0][0] or []) if e is not None})
    position = ' + '.join(f'CASE WHEN length({value}) > {e} THEN 1 ELSE 0 END' for e in edges) or '0'
    group = f'CASE WHEN NOT {has_text} THEN -1 ELSE {position} END'
    bounds = [0] + edges

    def label(g):
        if g == -1: return '(empty or missing)'
        low = bounds[g] + 1 if g else 1
        return f'{low:,}+ characters' if g == len(edges) else f'{low:,}–{edges[g]:,} characters'
    if spec.kind == 'binary':
        result = a.run(f'SELECT {group} AS g,count(*) AS n,sum({spec.positive_flag}) AS pos FROM selected WHERE {spec.ok} GROUP BY 1 ORDER BY 1',
                       f'{spec.column} by length of {name}')
        overall = sum(r[2] for r in result['rows']) / max(1, sum(r[1] for r in result['rows']))
        table_rows = [[label(g), n, p, *auto_univariate.rate_columns(p, n, overall, cfg.confidence)] for g, n, p in result['rows']]
        chi = auto_stats.chi_square([[p, n - p] for _, n, p in result['rows']])
        test = f"chi-square p {chi['p_value']:.2g}, Cramér's V {chi['cramers_v']:.3f}" if chi else 'no test (one group)'
        items.append(table(item_id(name, 'text-length'), f'{name}: {spec.column} rate by text length', [('Text length', 'text'), ('Rows', 'count'),
            ('Positives', 'count'), *RATE_COLUMNS], table_rows, evidence=a.evidence('Length quintiles among rows with text, plus empty or missing', edges_q, result),
            note=f'{rate_note(overall)} Test of independence: {test}.'))
    else:
        result = a.run(f'SELECT {group} AS g,count(y) AS n,avg(y) AS mean,stddev_samp(y) AS sd FROM (SELECT *,{spec.value} AS y FROM selected) '
                       f'WHERE y IS NOT NULL GROUP BY 1 ORDER BY 1', f'{spec.column} by length of {name}')
        corr, corr_q = correlation(a, f'length({value})', spec.value, f'Correlation of the length of {name} with {spec.column}')
        low, high = auto_stats_ci.spearman_interval(corr['spearman'], corr['n'], cfg.confidence)
        table_rows = [[label(g), n, m, *((m - 1.96 * sd / math.sqrt(n), m + 1.96 * sd / math.sqrt(n)) if sd and n > 1 else (None, None))]
                      for g, n, m, sd in result['rows']]
        items.append(table(item_id(name, 'text-length'), f'{name}: mean {spec.column} by text length', [('Text length', 'text'), ('Rows', 'count'),
            (f'Mean {spec.column}', 'number'), ('95% CI low', 'number'), ('95% CI high', 'number')], table_rows,
            evidence=a.evidence('Length quintiles among rows with text, plus empty or missing', edges_q, result, corr_q),
            note=f'Spearman ρ between text length and {spec.column}: {corr["spearman"]:.3f} [{low:.3f}–{high:.3f}], n {corr["n"]:,}.'
                 if corr['spearman'] is not None and low is not None else None))
    return items


def country_codes(a, spec, name, f):
    """A note and a validity table when a categorical field holds ISO 3166 country codes."""
    if f['distinct_clean'] > 400: return []
    result = a.run(f'SELECT trim(CAST({quote(name)} AS VARCHAR)) AS v,count(*) AS n FROM selected WHERE NOT {absent_sql(name)} GROUP BY 1',
                   f'Values of {name} (country-code check)')
    kind = auto_textgeo.country_code_kind(result['rows'])
    if not kind: return []
    codes = auto_textgeo.ALPHA2 if 'alpha-2' in kind else auto_textgeo.ALPHA3
    invalid = sorted([[v, n] for v, n in result['rows'] if v not in codes], key=lambda r: (-r[1], r[0]))
    return [text(f'{name} holds {kind} country codes; counts and target comparisons per country are in the tables above. '
                 f'{sum(n for _, n in invalid):,} rows have values that are not valid codes.'),
            table(item_id(name, 'geo-invalid'), f'{name}: values that are not {kind} codes', [('Value', 'text'), ('Rows', 'count')], invalid,
                  evidence=a.evidence(f'Every value compared with the {kind} list', result))]


def geo_pair(a, spec, lat, lon, cfg):
    """Range checks for a latitude/longitude pair and rows (and the target) per grid cell."""
    y, x = numeric_expression(lat), numeric_expression(lon)
    checks = a.run(f'''SELECT count(*) AS rows,count(*) FILTER (WHERE y IS NOT NULL AND x IS NOT NULL) AS both_present,
        count(*) FILTER (WHERE (y IS NULL) <> (x IS NULL)) AS one_missing,count(*) FILTER (WHERE y < -90 OR y > 90) AS lat_out,
        count(*) FILTER (WHERE x < -180 OR x > 180) AS lon_out,count(*) FILTER (WHERE y = 0 AND x = 0) AS null_island,
        min(y),max(y),min(x),max(x) FROM (SELECT {y} AS y,{x} AS x FROM selected)''', f'Coordinate checks for {lat}, {lon}')
    rows, both, one_missing, lat_out, lon_out, zero, *_ = checks['rows'][0]
    valid = f'{y} BETWEEN -90 AND 90 AND {x} BETWEEN -180 AND 180 AND NOT ({y} = 0 AND {x} = 0)'

    def occupied(size):
        return a.run(f'SELECT count(*) FROM (SELECT DISTINCT floor({y} / {size}),floor({x} / {size}) FROM selected WHERE {valid})',
                     f'Occupied {size}° cells')['rows'][0][0]
    size = auto_textgeo.cell_size(None, None, occupied)
    cell = f"printf('%.4g, %.4g', floor({y} / {size}) * {size}, floor({x} / {size}) * {size})"
    target = (f',sum({spec.positive_flag}) AS pos' if spec.kind == 'binary' else f',avg({spec.value}) AS mean' if spec.kind == 'numeric' else '')
    where = f'{valid} AND {spec.ok}' if spec.kind in ('binary', 'numeric') else valid
    cells = a.run(f'SELECT {cell} AS cell,count(*) AS n{target} FROM selected WHERE {where} GROUP BY 1 ORDER BY n DESC,cell LIMIT 30', f'Rows per {size}° cell')
    totals = a.run(f'SELECT count(*) AS n{target} FROM selected WHERE {where}', 'Rows with valid coordinates')
    evidence = a.evidence(f'Range checks; grid of {size}° cells (south-west corner shown), the 30 with most rows', checks, cells, totals)
    check_rows = [['Rows with both coordinates', both], ['One coordinate missing', one_missing], [f'{lat} outside −90 to 90', lat_out],
                  [f'{lon} outside −180 to 180', lon_out], ['Both exactly 0 (often a placeholder)', zero]]
    items = [text(f'{lat} and {lon} are read as latitude and longitude (name hints plus valid ranges).'),
             table(item_id(f'{lat}-{lon}', 'geo-checks'), f'{lat}, {lon}: coordinate checks', [('Check', 'text'), ('Rows', 'count')], check_rows, evidence=evidence)]
    if spec.kind == 'binary':
        overall = (totals['rows'][0][1] or 0) / max(1, totals['rows'][0][0])
        items.append(table(item_id(f'{lat}-{lon}', 'geo-cells'), f'Rows and {spec.column} rate per {size}° cell', [('Cell (lat, lon)', 'text'), ('Rows', 'count'),
            ('Positives', 'count'), *RATE_COLUMNS], [[c, n, p, *auto_univariate.rate_columns(p, n, overall, cfg.confidence)] for c, n, p in cells['rows']],
            evidence=evidence, note=rate_note(overall) + ' Cells with fewer than 30 rows give wide intervals.'))
    else:
        columns = [('Cell (lat, lon)', 'text'), ('Rows', 'count')] + ([(f'Mean {spec.column}', 'number')] if spec.kind == 'numeric' else [])
        items.append(table(item_id(f'{lat}-{lon}', 'geo-cells'), f'Rows per {size}° cell', columns, [list(r) for r in cells['rows']], evidence=evidence))
    return items


# ---------------- stage 11: no-target mode ----------------
def unsupervised_stage(a, facts, time_axis, output, state):
    """Clusters, unusual rows and distribution shapes on a seeded sample (no target chosen)."""
    cfg = state['cfg']; structure = state.get('structure')
    others = {m for c in (structure.clusters if structure else []) for m in c.members if m != c.representative}
    candidates = [n for n, f in facts.items() if f['type'] in ('numeric', 'categorical', 'binary') and n != time_axis]
    wide_categories = [n for n in candidates if facts[n]['type'] != 'numeric' and facts[n]['distinct_clean'] > SETTINGS['category_limit']]
    usable = [n for n in candidates if n not in others and n not in wide_categories]
    fields = usable[:cfg.cluster_fields]
    shape_fields = [n for n in candidates if facts[n]['type'] == 'numeric'][:100]
    loaded = list(dict.fromkeys(fields + shape_fields))
    parts = ['_row_id'] + [f'{numeric_expression(n)} AS {quote(n)}' if facts[n]['type'] == 'numeric' else
                           f'CASE WHEN {absent_sql(n)} THEN NULL ELSE trim(CAST({quote(n)} AS VARCHAR)) END AS {quote(n)}' for n in loaded]
    data, query = a.frame(f'SELECT {",".join(parts)} FROM selected ORDER BY hash(_row_id + {cfg.seed}),_row_id LIMIT {cfg.unsupervised_rows}',
                          'Seeded sample for clusters, unusual rows and shapes')
    total = output['rows']; sampled = len(data)
    evidence = {'version': output['analysis_version'], 'computation': f'Seeded sample of {sampled:,} of {total:,} rows (rows ordered by hash(_row_id + {cfg.seed}))',
                'queries': [{'sql': query['sql'], 'parameters': []}]}
    if sampled < total:
        output['sampling'].append(['clusters, unusual rows and shapes', sampled, total, cfg.seed, 'rows ordered by hash(_row_id + seed)'])
    numeric = [n for n in fields if facts[n]['type'] == 'numeric']; categorical = [n for n in fields if n not in numeric]
    notes = [f'Fields used for clusters and unusual rows: {", ".join(fields) or "none"}.']
    if others: notes.append('Redundant fields are represented once (their cluster representative): left out ' + ', '.join(sorted(others & set(candidates))) + '.')
    if wide_categories: notes.append(f'Categories with more than {SETTINGS["category_limit"]} levels are left out: {", ".join(wide_categories)}.')
    if len(usable) > len(fields): notes.append(f'Only the first {cfg.cluster_fields} usable fields in column order are used.')
    items = [text(' '.join(notes))]
    summary = {'fields': fields, 'sample_rows': sampled, 'total_rows': total}
    if len(fields) >= 2 and sampled >= 3 * cfg.cluster_k_max:
        fit_frame = data.iloc[:cfg.cluster_sample_rows].reset_index(drop=True)
        result, assign = auto_unsupervised.cluster(fit_frame, numeric, categorical, cfg)
        result.total_rows = total
        items.append(table('clusters-candidates', 'Number of clusters: silhouette and stability', [('Clusters (k)', 'count'), ('Mean silhouette', 'effect'),
            ('Bootstrap ARI', 'effect'), ('Chosen', 'text')], [[k, sil, ari, 'yes' if k == result.k else ''] for k, sil, ari in result.candidates],
            evidence=evidence, note=f'{result.method}, fitted on {len(fit_frame):,} sampled rows (seed {cfg.seed}). Stability = mean adjusted Rand index over '
            f'{cfg.cluster_bootstrap} bootstrap refits, computed when silhouette ≥ {cfg.cluster_silhouette_minimum}. A structure is reported only with '
            f'silhouette ≥ {cfg.cluster_silhouette_minimum} and ARI ≥ {cfg.cluster_ari_minimum}.'))
        if assign is not None:
            labels = assign(data[fields])
            result.sizes, result.profiles, _ = auto_unsupervised.profile(data[fields], labels, numeric, categorical)
            result.assigned_rows = sampled
            chosen = next(c for c in result.candidates if c[0] == result.k)
            items.append(table('clusters-sizes', f'{result.k} clusters: sizes', [('Cluster', 'text'), ('Rows', 'count'), ('Share', 'percent')],
                               [[f'Cluster {c}', n, share] for c, n, share in result.sizes], evidence=evidence,
                               note=f'Every sampled row is assigned to the nearest medoid or centre. Silhouette {chosen[1]:.2f}, bootstrap ARI {chosen[2]:.2f}.'))
            items.append(table('clusters-profiles', 'What distinguishes each cluster', [('Cluster', 'text'), ('Field', 'text'), ('Description', 'text'),
                ('Standardised difference', 'number')], [[f'Cluster {c}', f, d, v] for c, f, d, v in result.profiles], evidence=evidence,
                note='The three fields with the largest standardised differences from the other rows; numbers: difference in means over the overall sd; '
                     'categories: difference in the share of the most different level over √(p(1 − p)).'))
            fig = go.Figure(go.Bar(y=[f'Cluster {c}' for c, *_ in result.sizes], x=[n for _, n, _ in result.sizes], orientation='h', marker_color=visuals.TEAL))
            fig.update_yaxes(autorange='reversed')
            items.append(figure('clusters-chart', 'Rows per cluster', visuals.style(fig, 'Rows per cluster', 'Rows', None), evidence=evidence))
        else:
            result.note = (f'No stable cluster structure: no k from {cfg.cluster_k_min} to {cfg.cluster_k_max} reached silhouette ≥ '
                           f'{cfg.cluster_silhouette_minimum} with bootstrap ARI ≥ {cfg.cluster_ari_minimum}.')
            items.append(text(result.note))
        summary['clusters'] = result.to_dict()
    else:
        items.append(text('Clusters need at least two usable fields and enough rows; not computed.'))
    rows, flagged = auto_unsupervised.anomalies(data[fields], numeric, categorical, cfg, data['_row_id'].to_numpy()) if fields else ([], 0)
    items.append(table('unusual-rows', f'Unusual rows: the {cfg.anomaly_share:.0%} with the highest isolation scores', [('Row id', 'count'),
        ('Isolation score', 'number'), ('What is unusual', 'text')], rows[:cfg.anomalies_shown], evidence=evidence,
        note=f'{flagged:,} of {sampled:,} sampled rows flagged; the {min(flagged, cfg.anomalies_shown)} highest are listed. Isolation Forest '
             f'(200 trees, seed {cfg.seed}). Reasons: numbers at least 3 robust sd from the median, levels held by under 1% of rows. Reported only; no rows are removed.'))
    summary['anomalies'] = {'flagged': flagged, 'rows': rows[:cfg.anomalies_shown]}
    shapes = []
    for name in shape_fields:
        result = auto_unsupervised.shape(data[name].to_numpy(dtype=float), cfg)
        if result: shapes.append({'field': name, **result})
    items.append(table('distribution-shapes', 'Distribution shape of numeric fields', [('Field', 'text'), ('Values', 'count'), ('Skewness', 'number'),
        ('Excess kurtosis', 'number'), ('Beyond the outer fences', 'percent'), ('Dip', 'effect'), ('Dip test p', 'p'), ('Shape', 'text')],
        [[r['field'], r['n'], r['skewness'], r['kurtosis'], r['far_share'], r['dip'], r['dip_p'], ', '.join(r['labels'])] for r in shapes], evidence=evidence,
        note=f'Outer fences: Q1 − 3·IQR and Q3 + 3·IQR. Hartigan\'s dip test on at most {cfg.dip_sample_rows:,} sampled values, p from {cfg.dip_simulations} '
             'uniform samples of the same size; fields with fewer than 20 distinct values get no dip. Labels: |skewness| ≥ 1; excess kurtosis ≥ 3 or '
             '≥ 1% beyond the fences; dip p < 0.05.'))
    summary['shapes'] = shapes
    output['sections']['unsupervised'] = items
    output['unsupervised'] = deep_clean(summary)
    clusters = summary.get('clusters') or {}
    return (f'{clusters.get("k")} clusters' if clusters.get('stable') else 'no stable clusters') + f'; {flagged} unusual rows'


# ---------------- stage 12: time analysis ----------------
def time_stage(a, spec, facts, time_axis, output, state):
    """Coverage and granularity, an automatically chosen aggregation level, trend, seasonality and breaks of the
    row count and the target per period, and distribution drift (PSI) between the first and last part of the range."""
    cfg = state['cfg']; t = state['time_sql']
    cover = a.run(f'''SELECT count(*) AS rows,count(t) AS dated,min(t) AS first,max(t) AS last,count(DISTINCT t) AS distinct_times,
        (SELECT median(gap) FROM (SELECT (epoch(t) - lag(epoch(t)) OVER (ORDER BY t)) / 86400.0 AS gap
            FROM (SELECT DISTINCT {t} AS t FROM selected) WHERE t IS NOT NULL)) AS median_gap_days
        FROM (SELECT {t} AS t FROM selected)''', f'Coverage of {time_axis}')
    rows, dated, first, last, distinct_times, gap = cover['rows'][0]
    if not dated: raise ValueError(f'{time_axis} has no usable ISO dates or timestamps.')
    native = auto_time.native_grain(gap) or 'daily'
    finest = auto_time.GRAINS.index(auto_time.FINEST[native])
    grains = auto_time.GRAINS[finest:]
    spans = a.run('SELECT ' + ','.join(f"date_trunc('{g}',min(t)),date_trunc('{g}',max(t))" for g in grains)
                  + f' FROM (SELECT {t} AS t FROM selected) WHERE t IS NOT NULL', 'Period boundaries per level')
    bounds = spans['rows'][0]
    candidates = {g: (auto_time.spanned(bounds[2 * i], bounds[2 * i + 1], g), dated) for i, g in enumerate(grains)}
    grain, reason = auto_time.choose_grain(native, candidates, cfg.time_min_periods, cfg.time_min_rows_per_period)
    d = f"date_trunc('{grain}',t)"
    base = f'(SELECT *,{t} AS t FROM selected)'
    volume = a.run(f'SELECT {d} AS period,count(*) AS n FROM {base} WHERE t IS NOT NULL GROUP BY 1 ORDER BY 1', f'Rows per {grain} of {time_axis}')
    start = auto_time.period_start(bounds[2 * grains.index(grain)], grain)
    end = auto_time.period_start(bounds[2 * grains.index(grain) + 1], grain)
    periods = auto_time.all_periods(start, end, grain)
    labels = [auto_time.label(p, grain) for p in periods]
    counts = {auto_time.period_start(p, grain): n for p, n in volume['rows']}
    step = timedelta(days=gap or 1)
    complete = [True] * len(periods)
    if periods:
        complete[0] = auto_time.as_datetime(first) < auto_time.as_datetime(periods[0]) + step
        complete[-1] = complete[-1] and auto_time.as_datetime(last) + step >= auto_time.as_datetime(auto_time.next_period(periods[-1], grain))
    gaps = sum(1 for p in periods if not counts.get(p))
    queries = [cover, spans, volume]
    volume_values = [counts.get(p) or None for p in periods]
    series = [auto_time.analyse_series(f'Rows per {grain}', 'rows', labels, volume_values, complete, grain, cfg)]
    period_columns = [('Period', 'text'), ('Rows', 'count')]
    period_rows = [[label, counts.get(p, 0)] for label, p in zip(labels, periods)]
    target_values = None; errors = None
    if spec.kind == 'binary':
        result = a.run(f'SELECT {d} AS period,count(*) AS n,sum({spec.positive_flag}) AS pos FROM {base} WHERE t IS NOT NULL AND {spec.ok} GROUP BY 1 ORDER BY 1',
                       f'Positive rate per {grain} of {time_axis}')
        queries.append(result)
        by = {auto_time.period_start(p, grain): (n, pos) for p, n, pos in result['rows']}
        period_columns += [('Rows with a target', 'count'), ('Positives', 'count'), ('Positive rate', 'percent'), ('95% CI low', 'percent'), ('95% CI high', 'percent')]
        target_values, errors = [], []
        for row, p in zip(period_rows, periods):
            n, pos = by.get(p, (0, 0)); rate = pos / n if n else None
            low, high = auto_stats_ci.wilson_interval(pos, n, cfg.confidence) if n else (None, None)
            row += [n, pos, rate, low, high]
            target_values.append(rate if n >= cfg.time_rate_min_rows else None)
            errors.append((rate - low, high - rate) if rate is not None else (0, 0))
        target_name, unit = f'{spec.column} positive rate', 'rate'
    elif spec.kind == 'numeric':
        result = a.run(f'SELECT {d} AS period,count(y) AS n,avg(y) AS mean,stddev_samp(y) AS sd FROM (SELECT t,{spec.value} AS y FROM {base}) '
                       f'WHERE t IS NOT NULL AND y IS NOT NULL GROUP BY 1 ORDER BY 1', f'Mean {spec.column} per {grain} of {time_axis}')
        queries.append(result)
        by = {auto_time.period_start(p, grain): (n, m, sd) for p, n, m, sd in result['rows']}
        period_columns += [('Rows with a target', 'count'), (f'Mean {spec.column}', 'number'), ('95% CI low', 'number'), ('95% CI high', 'number')]
        target_values, errors = [], []
        for row, p in zip(period_rows, periods):
            n, mean, sd = by.get(p, (0, None, None))
            half = float(stats_t.ppf(0.5 + cfg.confidence / 2, n - 1) * sd / math.sqrt(n)) if n and n > 1 and sd is not None else None
            row += [n, mean, mean - half if half is not None else None, mean + half if half is not None else None]
            target_values.append(mean if n >= cfg.time_rate_min_rows else None)
            errors.append((half or 0, half or 0) if mean is not None else (0, 0))
        target_name, unit = f'Mean {spec.column}', 'mean'
    if target_values is not None:
        series.append(auto_time.analyse_series(target_name, unit, labels, target_values, complete, grain, cfg))
    for row, ok, p in zip(period_rows, complete, periods):
        row.append(', '.join(f for f in ['incomplete period' if not ok else '', 'no rows' if not counts.get(p) else ''] if f))
    period_columns.append(('Flags', 'text'))
    for s in series:
        lo = s.used[0] if s.used else 0
        for fit in s.seasonality:
            fit['peak_label'] = auto_time.phase_label(periods[lo + fit['peak']], fit['meaning'])
            fit['trough_label'] = auto_time.phase_label(periods[lo + fit['trough']], fit['meaning'])
    evidence = a.evidence(f'Aggregated per {grain} ({reason}); Mann–Kendall with Theil–Sen slope, STL seasonal strength with {cfg.seasonal_permutations} '
                          f'permutations (seed {cfg.seed}), binary segmentation for breaks', *queries)
    coverage_rows = [['Time axis', time_axis], ['Rows with a usable timestamp', f'{dated:,} of {rows:,}'], ['First', str(first)], ['Last', str(last)],
                     ['Distinct timestamps', f'{distinct_times:,}'], ['Rows sharing a timestamp with another row', f'{dated - distinct_times:,}'],
                     ['Median gap between distinct timestamps', f'{gap:,.3g} days' if gap is not None else 'one timestamp'], ['Granularity', native],
                     ['Aggregation level', f'{grain} ({reason})'], ['Periods spanned', f'{len(periods):,}'], ['Periods without rows', f'{gaps:,}'],
                     ['Incomplete edge periods (left out of tests)', ', '.join(l for l, ok in zip(labels, complete) if not ok) or 'none']]
    items = [table('time-coverage', f'{time_axis}: coverage and aggregation', [('Setting', 'text'), ('Value', 'text')], coverage_rows,
                   evidence=a.evidence('Distinct timestamps and their gaps; candidate levels hour, day, week, month, quarter, year', cover, spans)),
             table('time-periods', f'Rows{" and " + spec.column if target_values is not None else ""} per {grain}', period_columns, period_rows, evidence=evidence,
                   note='Intervals: Wilson for rates, t-based for means. Periods with fewer than '
                        f'{cfg.time_rate_min_rows} rows get no rate or mean in the tests.')]
    for s, values in ([(series[1], target_values)] if target_values is not None else []) + [(series[0], volume_values)]:
        items.append(figure(item_id(s.name, 'time-chart'), f'{s.name}', series_chart(s, values, errors if s is not series[0] else None, labels, unit=s.unit),
                            evidence=evidence))
        a.results[item_id(s.name, 'time-chart')] = queries[-1] if s is not series[0] else volume
    items.append(table('time-trend', 'Trend tests', [('Series', 'text'), ('Periods tested', 'count'), ("Kendall's τ", 'effect'), ('Mann–Kendall z', 'number'),
        ('p-value', 'p'), ('Autocorrelation factor', 'number'), ('Theil–Sen slope per period', 'number'), ('95% CI low', 'number'), ('95% CI high', 'number'),
        ('Change over the range', 'number'), ('Change relative to the mean level', 'percent'), ('Seasonality removed first', 'text')],
        [[s.name, s.trend['periods'], s.trend['tau'], s.trend['z'], s.trend['p'], s.trend['variance_factor'], s.trend['slope'], s.trend['low'], s.trend['high'],
          s.trend['change'], s.trend['relative_change'], 'yes' if s.trend['deseasonalised'] else 'no'] for s in series if s.trend],
        evidence=evidence, note='Mann–Kendall assumes independent periods; its variance is inflated for positive lag-1 autocorrelation (factor shown). '
        'Change over the range = Theil–Sen slope × (periods − 1).' + ''.join(' ' + s.note for s in series if s.note)))
    items.append(table('time-seasonality', 'Seasonality (STL)', [('Series', 'text'), ('Cycle', 'text'), ('Periods per cycle', 'count'),
        ('Seasonal strength', 'effect'), ('Permutation p', 'p'), ('Highest at', 'text'), ('Lowest at', 'text'), ('Peak-to-trough difference', 'number')],
        [[s.name, f['meaning'], f['period'], f['strength'], f['p'], f['peak_label'], f['trough_label'], f['amplitude']] for s in series for f in s.seasonality],
        evidence=evidence, note=f'Computed when at least two full cycles of complete periods exist and at most 10% of them are empty. Strength '
        f'= max(0, 1 − Var(remainder)/Var(seasonal + remainder)); reported as a finding at {cfg.seasonal_strength_minimum} or more with permutation '
        f'p ≤ {cfg.seasonal_p_maximum}, and then removed before the trend and break tests.'))
    items.append(table('time-breaks', 'Structural breaks', [('Series', 'text'), ('Break at', 'text'), ('Level just before', 'number'),
        ('Level just after', 'number'), ('Jump', 'number'), ('Jump in noise sd', 'number'), ('Mean before', 'number'), ('Mean after', 'number'),
        ('Slope before', 'number'), ('Slope after', 'number')],
        [[s.name, b['label'], b['level_before'], b['level_after'], b['jump'], b['jump_sd'], b['mean_before'], b['mean_after'], b['slope_before'], b['slope_after']]
         for s in series for b in s.breaks], evidence=evidence,
        note=f'Binary segmentation into straight-line segments of at least {cfg.break_min_periods} periods; a break is kept when it improves the fit by more '
             f'than a BIC-type penalty ({cfg.break_penalty} × 3 ln n, raised for autocorrelation) and the level jump is at least {cfg.break_min_jump_sd} noise sd. '
             'Breaks are less reliable in series shorter than about 40 periods.'))
    drift, drift_queries, drift_note = field_drift(a, spec, facts, time_axis, first, last, cfg)
    items.append(table('time-drift', f'Distribution drift: first vs last {cfg.psi_window_share:.0%} of the time range',
        [('Field', 'text'), ('PSI', 'effect'), ('Shift', 'text'), ('Rows in first window', 'count'), ('Rows in last window', 'count'),
         ('Largest change', 'text')], [[r['field'], r['psi'], r['label'], r['first_rows'], r['last_rows'], r['largest']] for r in drift],
        evidence=a.evidence('Bins from the first window (deciles for numbers, most frequent levels for categories, plus missing); '
                            'PSI = Σ (last − first) · ln(last / first) with shares floored at 0.0001', *drift_queries),
        note=drift_note or f'PSI below {cfg.psi_moderate} is conventionally read as stable, {cfg.psi_moderate}–{cfg.psi_major} as a moderate shift and '
             f'above {cfg.psi_major} as a major shift. A shift means older rows may describe the present less well.'))
    if spec.column:
        items.append(text(f'Cross-validation in the predictive-power, model, interaction and segment steps uses time order on {time_axis}: '
                          'each fold is scored by a model trained on earlier rows only.'))
    output['sections']['time'] = items
    output['time'] = deep_clean({'axis': time_axis, 'grain': grain, 'reason': reason, 'native': native, 'periods': len(periods), 'gaps': gaps,
                                 'series': [s.to_dict() for s in series], 'drift': drift})
    return f'{grain}; {len(periods)} periods; {sum(len(s.breaks) for s in series)} breaks; {sum(r["label"] == "major shift" for r in drift)} major drifts'


def series_chart(s, values, errors, labels, unit):
    color = visuals.TEAL
    trace = go.Scatter(x=labels, y=values, mode='lines+markers', name=s.name, line={'color': color})
    if errors:
        trace.error_y = {'type': 'data', 'symmetric': False, 'array': [e[1] for e in errors], 'arrayminus': [e[0] for e in errors]}
    fig = go.Figure(trace)
    if s.trend and s.used:
        lo = s.used[0]; hi = s.used[-1]
        xs = labels[lo:hi + 1]
        fig.add_trace(go.Scatter(x=xs, y=[s.trend['intercept'] + s.trend['slope'] * i for i in range(len(xs))], mode='lines', name='Theil–Sen trend',
                                 line={'dash': 'dash', 'color': visuals.AMBER}))
    for b in s.breaks:
        fig.add_shape(type='line', x0=b['label'], x1=b['label'], y0=0, y1=1, yref='paper', line={'dash': 'dot', 'color': visuals.AMBER})
    if unit == 'rate': fig.update_yaxes(tickformat='.0%')
    return visuals.style(fig, s.name, None, s.name)


def field_drift(a, spec, facts, time_axis, first, last, cfg):
    """PSI per field between the first and last share of the time range (bins from the first window)."""
    span = auto_time.as_datetime(last) - auto_time.as_datetime(first)
    if span.total_seconds() <= 0: return [], [], 'The time range is a single instant; drift is not computed.'
    early = auto_time.as_datetime(first) + span * cfg.psi_window_share
    late = auto_time.as_datetime(last) - span * cfg.psi_window_share
    window = f"CASE WHEN t <= TIMESTAMP '{early.isoformat(sep=' ')}' THEN 0 WHEN t >= TIMESTAMP '{late.isoformat(sep=' ')}' THEN 1 END"
    base = f'(SELECT *,{window} AS w FROM (SELECT *,{auto_time.timestamp_expression(time_axis)} AS t FROM selected))'
    sizes = a.run(f'SELECT count(*) FILTER (WHERE w=0),count(*) FILTER (WHERE w=1) FROM {base}', 'Rows in the first and last windows')
    first_rows, last_rows = sizes['rows'][0]
    if min(first_rows, last_rows) < cfg.psi_min_rows:
        return [], [sizes], f'Fewer than {cfg.psi_min_rows} rows in the first or last window ({first_rows:,} and {last_rows:,}); drift is not computed.'
    drift = []; queries = [sizes]
    for name, f in facts.items():
        if name == time_axis or f['type'] not in ('numeric', 'categorical', 'binary'): continue
        if f['type'] == 'numeric':
            x = numeric_expression(name)
            edges_q = a.run(f"SELECT quantile_cont({x},[{','.join(repr(i / cfg.psi_bins) for i in range(1, cfg.psi_bins))}]) FROM {base} WHERE w=0",
                            f'Bin edges of {name} in the first window')
            edges = sorted({float(e) for e in (edges_q['rows'][0][0] or []) if e is not None})
            position = ' + '.join(f'CASE WHEN {x} > {e!r} THEN 1 ELSE 0 END' for e in edges) or '0'
            bin_sql = f'CASE WHEN {x} IS NULL THEN -1 ELSE {position} END'
            names = auto_time.numeric_bin_labels(edges); setup = [edges_q]
            label_of = lambda b, names=names: '(missing)' if b == -1 else names[b]
        else:
            value = f'trim(CAST({quote(name)} AS VARCHAR))'
            levels_q = a.run(f'SELECT {value} AS v,count(*) AS n FROM {base} WHERE w IS NOT NULL AND NOT {absent_sql(name)} GROUP BY 1 '
                             f'ORDER BY n DESC,v LIMIT {cfg.psi_levels}', f'Levels of {name} in the two windows', limit=cfg.psi_levels)
            listed = ','.join(literal(v) for v, _ in levels_q['rows']) or 'NULL'
            bin_sql = f"CASE WHEN {absent_sql(name)} THEN '(missing)' WHEN {value} IN ({listed}) THEN {value} ELSE '(other)' END"
            setup = [levels_q]; label_of = str
        counts_q = a.run(f'SELECT w,{bin_sql} AS b,count(*) FROM {base} WHERE w IS NOT NULL GROUP BY 1,2', f'Counts of {name} in the two windows')
        queries += setup + [counts_q]
        keys = sorted({b for _, b, _ in counts_q['rows']}, key=str)
        e = [sum(n for w, b, n in counts_q['rows'] if w == 0 and b == k) for k in keys]
        l = [sum(n for w, b, n in counts_q['rows'] if w == 1 and b == k) for k in keys]
        value_psi = auto_time.psi(e, l)
        if value_psi is None: continue
        shares_e = [v / sum(e) for v in e]; shares_l = [v / sum(l) for v in l]
        k = max(range(len(keys)), key=lambda i: (abs(shares_l[i] - shares_e[i]), str(keys[i])))
        drift.append({'field': name + (' (target)' if name == spec.column else ''), 'column': name, 'psi': value_psi,
                      'label': auto_time.psi_label(value_psi, cfg.psi_moderate, cfg.psi_major), 'first_rows': first_rows, 'last_rows': last_rows,
                      'largest': f'{label_of(keys[k])}: {shares_e[k]:.1%} → {shares_l[k]:.1%}', 'is_target': name == spec.column})
    drift.sort(key=lambda r: (-r['psi'], r['field']))
    return drift, queries, ''


# ---------------- stage 9: evidence blocks and persistence ----------------
def save_blocks(a, output):
    saved = 0; base = configuration_for(a)
    for section in iter_items(output):
        if section['type'] != 'figure' or section['id'] not in a.results: continue
        name = f'Auto {output["run_id"][-6:]} · {output["table"]} · {section["title"]}'[:100]
        a.workspace.save_object('block', name, {'analysis': base, 'figure': section['figure'], 'evidence': a.results[section['id']],
                                                'automatic_analysis': output['run_id']})
        saved += 1
    output['saved_blocks'] = saved
    return f'{saved} charts saved as evidence'


def configuration_for(a):
    from .report import configuration
    return configuration(a.engine, Metric('Records'))


def iter_items(output):
    sections = output['sections']
    for key in ('quality', 'target_selection', 'corrections', 'structure', 'target', 'leakage', 'ranking', 'multivariate', 'segments', 'confounding'):
        yield from sections.get(key, [])
    for field in sections.get('fields', []):
        yield from field['items']
    for key in ('relationships', 'time'):
        yield from sections.get(key, [])


def display_value(value, fact):
    if fact['storage'] != 'VARCHAR':
        try:
            number = float(value)
            return f'{number:g}' if math.isfinite(number) else value
        except (TypeError, ValueError):
            return value
    return value


def runs_directory(workspace):
    return Path(workspace.directory) / RUN_FOLDER


def save_run(workspace, output):
    folder = runs_directory(workspace); folder.mkdir(exist_ok=True)
    path = folder / f'{output["run_id"]}.json'
    temporary = folder / f'.write-{uuid.uuid4().hex}'
    try:
        temporary.write_text(json.dumps(output, default=str), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def list_runs(workspace):
    folder = runs_directory(workspace)
    runs = []
    for path in sorted(folder.glob('*.json'), reverse=True) if folder.exists() else []:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            runs.append({'run_id': data['run_id'], 'table': data['table'], 'created_at': data['created_at'], 'target': data.get('target')})
        except (OSError, ValueError, KeyError):
            continue
    return runs


def load_run(workspace, run_id):
    if not re.fullmatch(r'[0-9]{8}-[0-9]{6}-[0-9a-f]{6}', run_id or ''): raise ValueError('Unknown analysis run.')
    path = runs_directory(workspace) / f'{run_id}.json'
    if not path.is_file(): raise ValueError('Unknown analysis run.')
    return json.loads(path.read_text(encoding='utf-8'))
