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

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from ..datasets import quote
from . import auto_stats, visuals
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
STAGES = [('profile', 'Data profile'), ('target_selection', 'Target selection'), ('corrections', 'Safe data corrections'),
          ('target_overview', 'Target overview'), ('fields', 'Fields versus the target'), ('ranking', 'Ranking'),
          ('relationships', 'Relationships between fields'), ('time', 'Time analysis'), ('evidence', 'Saved evidence')]
RUN_FOLDER = 'automatic_analysis'


def _clean(value):
    """JSON-safe values; floats keep 12 significant digits so repeated runs compare equal."""
    if isinstance(value, float):
        return None if not math.isfinite(value) else float(f'{value:.12g}')
    if isinstance(value, (datetime, date)): return value.isoformat()
    if hasattr(value, 'item') and not isinstance(value, (list, tuple, dict, str)):
        return _clean(value.item())
    return value


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
        lowered = re.sub(r'[^a-z0-9]+', ' ', re.sub(r'([a-z])([A-Z])', r'\1 \2', name)).lower()
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
def run(workspace, table_name, *, target=None, target_type=None, positive=None, time_axis=None, progress=None,
        prepared=None, save_evidence=True, apply_corrections_step=True):
    progress = progress or (lambda *args: None)
    info = workspace.info(table_name)
    if prepared is None or prepared.get('version') != info['current']:
        prepared = None
    run_id = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6]
    output = {'format_version': FORMAT_VERSION, 'run_id': run_id, 'created_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
              'table': table_name, 'dataset': info['source'].get('filename', table_name), 'source_sha256': info['source']['sha256'],
              'rows': info['rows'], 'column_count': len(info['columns']), 'original_version': info['current'], 'analysis_version': info['current'],
              'target': target, 'target_type': target_type if target else None, 'positive': positive if target_type == 'binary' else None,
              'time_axis': time_axis, 'settings': SETTINGS, 'stages': [], 'sections': {}, 'skipped': [], 'field_failures': [],
              'sampling': [], 'small_sample_warnings': []}
    state = {'prepared': prepared}

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
        items.extend(missing_patterns(state['corrected'], facts_after))
        output['sections']['corrections'] = items
        return f'{len(rows)} corrections' + (f'; version {version["table"]}' if version else '; no version needed')

    stage('corrections', corrections_stage)
    worker = state.get('corrected') or analysis
    facts = state.get('facts') or state['prepared']['facts']
    spec = TargetSpec(target, target_type, positive)

    stage('target_overview', lambda: target_overview(worker, spec, output), skip_reason=None if target else 'no target chosen')
    stage('fields', lambda: fields_stage(worker, spec, facts, time_axis, output, state))
    stage('ranking', lambda: ranking_stage(output, state), skip_reason=None if target else 'no target chosen')
    stage('relationships', lambda: relationships_stage(worker, spec, facts, time_axis, output, state))
    stage('time', lambda: time_stage(worker, spec, time_axis, output), skip_reason=None if time_axis else 'no time axis chosen')
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
    def __init__(self, column, kind, positive):
        self.column, self.kind, self.positive = column, kind, positive

    @property
    def ok(self):
        if self.kind == 'numeric': return f'{numeric_expression(self.column)} IS NOT NULL'
        return f'NOT {absent_sql(self.column)}'

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
        if f['type'] not in ANALYSED_TYPES:
            output['skipped'].append([name, SKIP_REASONS[f['type']].format(mean_length=f['mean_length'] or 0)]); continue
        try:
            items, effect = analyse_field(a, spec, name, f, output)
        except Exception as exc:
            output['field_failures'].append([name, f'{type(exc).__name__}: {exc}']); continue
        sections.append({'field': name, 'type': f['type'], 'items': items})
        if effect: state['effects'].append(effect)
    output['sections']['fields'] = sections
    return f'{len(sections)} fields analysed, {len(output["skipped"])} skipped, {len(output["field_failures"])} failed'


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
    all_positive = sum(r[2] for r in rows) or 1
    table_rows = [[category_label(c, other), t, p, t - p, p / t, p / all_positive, flag_small(t)] for c, t, p in rows]
    chi = auto_stats.chi_square([[p, t - p] for _, t, p in rows])
    evidence = a.evidence('Counts per category; chi-square on the category × target table (SciPy)', top, result)
    items = [table(item_id(name, 'rates'), f'{name}: rows and positive rate ({spec.column} = {spec.positive})', [('Category', 'text'),
        ('Rows', 'count'), ('Positives', 'count'), ('Negatives', 'count'), ('Positive rate', 'percent'), ('Share of all positives', 'percent'),
        ('Flags', 'text')], table_rows, evidence=evidence, note=note_other(other)),
        table(item_id(name, 'test'), f'{name}: test of independence', TEST_COLUMNS, chi_rows(chi), evidence=evidence,
              note="Effect size: Cramér's V.")]
    labels = [r[0] for r in table_rows]
    negative = 'not ' + str(spec.positive)
    pivot = pd.DataFrame({str(spec.positive): [r[2] for r in table_rows], negative: [r[3] for r in table_rows]}, index=labels)
    items.append(figure(item_id(name, 'heatmap'), f'{name} × {spec.column}: rows', heatmap(pivot, f'{name} × {spec.column}: rows', spec.column, name), evidence=evidence))
    ordered = sorted(table_rows, key=lambda r: (-r[4], r[0]))
    bars = go.Figure(go.Bar(y=[r[0] for r in ordered], x=[r[4] for r in ordered], orientation='h', marker_color=visuals.TEAL,
                            text=[f'{r[4]:.1%} of {r[1]:,}' for r in ordered], textposition='outside', cliponaxis=False))
    bars.update_xaxes(tickformat='.0%'); bars.update_yaxes(autorange='reversed')
    items.append(figure(item_id(name, 'rate'), f'{name}: positive rate', visuals.style(bars, f'{name}: share of rows with {spec.column} = {spec.positive}',
                        'Positive rate', name), evidence=evidence))
    a.results[item_id(name, 'heatmap')] = a.results[item_id(name, 'rate')] = result
    small_groups(output, name, table_rows, 1)
    return items, effect_row(name, 'categorical', "Chi-square", chi, 'cramers_v')


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
    rows = [[category_label(r[0], other), *r[1:], flag_small(r[2])] for r in rows]
    test, ranks = kruskal(a, spec.value, expression, spec.ok, f'Ranks of {spec.column} by {name}')
    evidence = a.evidence('Exact quantiles per category; Kruskal–Wallis from average ranks', top, summary, ranks)
    test_rows = [['Kruskal–Wallis H', test['statistic'], test['df'], test['p_value'], test['epsilon_squared'], test['n'], '']] if test else \
        [['Kruskal–Wallis H', None, None, None, None, None, 'undefined: fewer than two groups or all values tied']]
    items = [table(item_id(name, 'summary'), f'{spec.column} by {name}', [('Category', 'text'), *SUMMARY_COLUMNS, ('Flags', 'text')], rows,
                   evidence=evidence, note=note_other(other)),
             table(item_id(name, 'test'), f'{name}: test of differences in {spec.column}', TEST_COLUMNS, test_rows, evidence=evidence,
                   note='Effect size: epsilon-squared.'),
             figure(item_id(name, 'box'), f'{spec.column} by {name}', box(rows, f'{spec.column} by {name}', name, spec.column), evidence=evidence)]
    a.results[item_id(name, 'box')] = summary
    small_groups(output, name, rows, 2)
    return items, effect_row(name, 'categorical', 'Kruskal–Wallis', test, 'epsilon_squared')


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
        (SELECT x,y,ntile({SETTINGS["quantile_bins"]}) OVER (ORDER BY x,_row_id) AS bin FROM
        (SELECT {x} AS x,{spec.positive_flag} AS y,_row_id FROM selected WHERE {spec.ok}) WHERE x IS NOT NULL) GROUP BY 1 ORDER BY 1''',
        f'Positive rate by quantile bins of {name}')
    bin_rows = [[b, low, high, n, p, p / n] for b, low, high, n, p in bins['rows']]
    evidence = a.evidence('Exact quantiles per class; Mann–Whitney U from average ranks; equal-count bins by value then _row_id', summary, ranks, bins)
    test_rows = [['Mann–Whitney U (normal approximation, tie and continuity corrected)', test['statistic'], None, test['p_value'],
                  test['rank_biserial'], test['n'], '']] if test else [['Mann–Whitney U', None, None, None, None, None, 'undefined']]
    items = [table(item_id(name, 'summary'), f'{name} by {spec.column}', [('Class', 'text'), *SUMMARY_COLUMNS], rows, evidence=evidence),
             table(item_id(name, 'test'), f'{name}: test of differences between classes', TEST_COLUMNS, test_rows, evidence=evidence,
                   note=f'Effect size: rank-biserial correlation r = 2U/(n₁n₂) − 1, where group 1 is {spec.column} = {spec.positive}; '
                        'positive r means larger values are more common in that group.'),
             table(item_id(name, 'bins'), f'{name}: positive rate by {SETTINGS["quantile_bins"]} equal-count bins', [('Bin', 'count'),
                   ('Lowest value', 'number'), ('Highest value', 'number'), ('Rows', 'count'), ('Positives', 'count'), ('Positive rate', 'percent')],
                   bin_rows, evidence=evidence, note='Rows with tied values can fall into adjacent bins; ties are ordered by _row_id.'),
             figure(item_id(name, 'box'), f'{name} by {spec.column}', box(rows, f'{name} by {spec.column}', spec.column, name), evidence=evidence)]
    line = go.Figure(go.Scatter(x=[f'{r[0]}: {visuals.compact(r[1])}–{visuals.compact(r[2])}' for r in bin_rows], y=[r[5] for r in bin_rows],
                                mode='lines+markers', line={'color': visuals.TEAL}, text=[f'{r[5]:.1%} of {r[3]:,}' for r in bin_rows]))
    line.update_yaxes(tickformat='.0%')
    items.append(figure(item_id(name, 'bins-chart'), f'{name}: positive rate by bin', visuals.style(line, f'{name}: positive rate by equal-count bin',
                        name, 'Positive rate'), evidence=evidence))
    a.results[item_id(name, 'box')] = summary; a.results[item_id(name, 'bins-chart')] = bins
    return items, effect_row(name, 'numeric', 'Mann–Whitney U', test, 'rank_biserial')


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
    medians = a.run(f'''SELECT bin,min(x) AS lower,max(x) AS upper,count(*) AS n,median(y) AS median FROM
        (SELECT x,y,ntile({SETTINGS["median_bins"]}) OVER (ORDER BY x,_row_id) AS bin FROM
        (SELECT {x} AS x,{y} AS y,_row_id FROM selected) WHERE x IS NOT NULL AND y IS NOT NULL) GROUP BY 1 ORDER BY 1''',
        f'Median {spec.column} by bins of {name}')
    sample, total = scatter_sample(a, x, y, f'{name} and {spec.column} points', output, name)
    evidence = a.evidence('Spearman = Pearson correlation of average ranks; p from t with n − 2 df', result, medians, sample)
    items = [table(item_id(name, 'test'), f'{name} and {spec.column}: correlation', TEST_COLUMNS, [
        ['Spearman rank correlation', stats_['spearman'], None, stats_['p_value'], stats_['spearman'], stats_['n'], ''],
        ['Pearson correlation (reference)', stats_['pearson'], None, stats_['pearson_p'], stats_['pearson'], stats_['n'], '']], evidence=evidence),
        table(item_id(name, 'medians'), f'{name}: median {spec.column} by {SETTINGS["median_bins"]} equal-count bins', [('Bin', 'count'),
              ('Lowest value', 'number'), ('Highest value', 'number'), ('Rows', 'count'), (f'Median {spec.column}', 'number')], medians['rows'],
              evidence=evidence)]
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


# ---------------- stage 6 ----------------
MEASURE_COLUMNS = [('cramers_v', "Cramér's V"), ('rank_biserial', '|rank-biserial r|'), ('epsilon_squared', 'epsilon-squared'), ('spearman', '|Spearman ρ|')]


def ranking_stage(output, state):
    effects = [e for e in state.get('effects', []) if e['effect'] is not None]
    adjusted = auto_stats.benjamini_hochberg([e['p_value'] for e in effects])
    for effect, q in zip(effects, adjusted): effect['adjusted_p'] = q
    effects.sort(key=lambda e: (-abs(e['effect']), e['field']))
    state['ranked'] = [e['field'] for e in effects]
    rows = []
    for rank, e in enumerate(effects, 1):
        measures = [abs(e['effect']) if e['measure'] == key else None for key, _ in MEASURE_COLUMNS]
        rows.append([rank, e['field'], e['type'], e['test'], e['n'], *measures, e['p_value'], e['adjusted_p']])
    columns = [('Rank', 'count'), ('Field', 'text'), ('Field type', 'text'), ('Test', 'text'), ('n', 'count'),
               *[(label, 'effect') for _, label in MEASURE_COLUMNS], ('p-value', 'p'), ('Benjamini–Hochberg adjusted p', 'p')]
    undefined = [[e['field'], e['test']] for e in state.get('effects', []) if e['effect'] is None]
    output['sections']['ranking'] = [table('ranking', 'Fields ranked by effect size: statistical associations, not causes', columns, rows,
        evidence={'version': output['analysis_version'], 'computation': 'Effect sizes and p-values from each field section; '
                  'Benjamini–Hochberg adjustment across all ranked fields', 'queries': []},
        note='Effect sizes of different measures are not directly comparable; each row is ordered by its own measure.')]
    if undefined:
        output['sections']['ranking'].append(table('ranking-undefined', 'Fields with an undefined test', [('Field', 'text'), ('Test', 'text')],
            undefined, evidence={'version': output['analysis_version'], 'computation': 'Test undefined for the data', 'queries': []}))
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
            value, measure, n, results = association(a, first, second, kinds)
            # Rounded before sorting, so last-bit differences from parallel sums cannot reorder near-ties.
            pairs.append([first, second, measure, _clean(value), n]); queries += results
    evidence = a.evidence("Cramér's V for categorical pairs, Spearman ρ for numeric pairs, correlation ratio η for mixed pairs", *queries)
    matrix = pd.DataFrame(1.0, index=fields, columns=fields)
    for first, second, _, value, _ in pairs:
        matrix.loc[first, second] = matrix.loc[second, first] = value
    rows = [[f, *[matrix.loc[f, g] for g in fields]] for f in fields]
    ordered = sorted([p for p in pairs if p[3] is not None], key=lambda p: (-abs(p[3]), p[0], p[1]))
    items = [text(f'Fields compared: {basis}.'),
             table('relationships-matrix', 'Associations between fields', [('Field', 'text'), *[(f, 'effect') for f in fields]], rows, evidence=evidence,
                   note="Categorical pairs: Cramér's V. Numeric pairs: Spearman ρ. Mixed pairs: correlation ratio η."),
             table('relationships-pairs', 'Field pairs by association', [('Field', 'text'), ('Field', 'text'), ('Measure', 'text'),
                   ('Value', 'effect'), ('n', 'count')], ordered, evidence=evidence)]
    fig = px.imshow(matrix.abs(), color_continuous_scale='Teal', zmin=0, zmax=1, text_auto='.2f', aspect='auto')
    items.append(figure('relationships-heatmap', 'Associations between fields', visuals.style(fig, 'Associations between fields (absolute values)'), evidence=evidence))
    for first, second, measure, value, n in ordered[:SETTINGS['relationship_pairs_shown']]:
        items += pair_detail(a, first, second, kinds, output)
    output['sections']['relationships'] = items
    return f'{len(pairs)} pairs'


def association(a, first, second, kinds):
    kind = (kinds[first], kinds[second])
    if kind == ('numeric', 'numeric'):
        stats_, result = correlation(a, numeric_expression(first), numeric_expression(second), f'Correlation of {first} and {second}')
        return stats_['spearman'], 'Spearman ρ', stats_['n'], [result]
    if kind == ('categorical', 'categorical'):
        left, _, top_left = category_expression(a, first, 'true'); right, _, top_right = category_expression(a, second, 'true')
        result = a.run(f'SELECT {left} AS a,{right} AS b,count(*) AS n FROM selected GROUP BY 1,2', f'{first} × {second}')
        frame = pd.DataFrame(result['rows'], columns=['a', 'b', 'n']).pivot(index='a', columns='b', values='n').fillna(0)
        chi = auto_stats.chi_square(frame.to_numpy())
        return (chi['cramers_v'] if chi else None), "Cramér's V", int(frame.to_numpy().sum()), [top_left, top_right, result]
    category, number = (first, second) if kind[0] == 'categorical' else (second, first)
    expression, _, top = category_expression(a, category, 'true'); x = numeric_expression(number)
    groups = a.run(f'SELECT {expression} AS g,count(v) AS n,avg(v) AS mean FROM (SELECT *,{x} AS v FROM selected) WHERE v IS NOT NULL GROUP BY 1',
                   f'{number} by {category}')
    total = a.run(f'SELECT count({x}),avg({x}),var_pop({x}) FROM selected', f'{number} overall')
    n, mean, variance = total['rows'][0]
    eta = auto_stats.correlation_ratio([(g_n, g_mean) for _, g_n, g_mean in groups['rows']], n, mean, variance)
    return eta, 'correlation ratio η', n, [top, groups, total]


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


# ---------------- stage 8 ----------------
def time_stage(a, spec, time_axis, output):
    items = date_versus_target(a, TargetSpec(None, None, None), time_axis, title_prefix=f'Rows by {time_axis}', suffix='-time')
    if spec.column:
        items += date_versus_target(a, spec, time_axis, title_prefix=f'{spec.column} by {time_axis}', suffix='-time-target')
    output['sections']['time'] = items
    return ''


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
    for key in ('quality', 'target_selection', 'corrections', 'target', 'ranking'):
        yield from sections.get(key, [])
    for field in sections.get('fields', []):
        yield from field['items']
    for key in ('relationships', 'time'):
        yield from sections.get(key, [])


def missing_patterns(a, facts):
    """For columns with missing values: other columns that have one shared value (or are also missing) in every such row."""
    candidates = sorted([(f['missing'], n) for n, f in facts.items() if f['missing']], key=lambda t: (-t[0], t[1]))
    rows = []; queries = []
    for count, name in candidates[:SETTINGS['missing_pattern_columns']]:
        others = [o for o, f in facts.items() if o != name and f['type'] not in ('empty', 'constant', 'identifier', 'text')]
        if not others: continue
        aggregates = ','.join(f'count(DISTINCT CAST({quote(o)} AS VARCHAR)) AS "d{i}",min(CAST({quote(o)} AS VARCHAR)) AS "v{i}",count({quote(o)}) AS "n{i}"'
                              for i, o in enumerate(others))
        result = a.run(f'SELECT count(*) AS rows_,{aggregates} FROM selected WHERE {missing_sql(name)}', f'Values in rows where {name} is missing')
        queries.append(result)
        values = dict(zip(result['columns'], result['rows'][0])); found = 0
        for i, other in enumerate(others):
            if found >= SETTINGS['missing_patterns_per_column']: break
            if values[f'n{i}'] == 0:
                total = a.run(f'SELECT count(*) FROM selected WHERE {quote(other)} IS NULL', f'Rows where {other} is missing')
                queries.append(total); found += 1
                rows.append([name, count, other, '(missing)', total['rows'][0][0]])
            elif values[f'd{i}'] == 1 and values[f'n{i}'] == count:
                shared = values[f'v{i}']
                total = a.run(f'SELECT count(*) FROM selected WHERE CAST({quote(other)} AS VARCHAR)={literal(shared)}', f'Rows where {other} = {shared}')
                queries.append(total); found += 1
                rows.append([name, count, other, display_value(shared, facts[other]), total['rows'][0][0]])
    # Patterns whose value occurs only in the missing rows come first, then by how common the value is.
    rows.sort(key=lambda r: (r[0], r[4] - r[1], r[2]))
    return [table('missing-patterns', 'Observed patterns in missing values', [('Column with missing values', 'text'), ('Missing rows', 'count'),
        ('Other column', 'text'), ('Value in every missing row', 'text'), ('Rows with that value in total', 'count')], rows,
        evidence=a.evidence('Distinct values of every other column within the missing rows', *queries),
        note=f'Checked for up to {SETTINGS["missing_pattern_columns"]} columns, listing at most {SETTINGS["missing_patterns_per_column"]} '
             'other columns each. Reported only; no values are filled.')]


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
