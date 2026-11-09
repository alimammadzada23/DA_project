"""Standalone HTML and Excel reports for an automatic analysis run.

Formatting rules: counts with thousands separators, shares as percentages with one
decimal, p-values in scientific notation below 0.001, effect sizes with three
decimals. Text is escaped; spreadsheet cells are made formula-safe.
"""
from html import escape
import io
import json
import math
import re

import pandas as pd
import plotly.io as pio

from .auto_analysis import SETTINGS, iter_items
from .report import safe_spreadsheet_frame

SECTION_TITLES = [('quality', 'Data quality'), ('corrections', 'Corrections applied and issues reported'),
                  ('target', 'Target overview'), ('ranking', 'Ranking'), ('fields', 'Fields'),
                  ('relationships', 'Relationships between fields'), ('time', 'Time analysis')]


def format_value(value, kind):
    if value is None or (isinstance(value, float) and not math.isfinite(value)): return '—'
    if kind == 'text' or isinstance(value, str) or isinstance(value, bool): return str(value)
    if kind == 'count':
        return f'{value:,.0f}' if float(value).is_integer() else f'{value:,.4g}'
    if kind == 'percent': return f'{value * 100:.1f}%'
    if kind == 'p':
        if value == 0: return '< 1e-300'
        return f'{value:.2e}' if value < 0.001 else f'{value:.3f}'
    if kind == 'effect': return f'{value:.3f}'
    if float(value).is_integer() and abs(value) < 1e15: return f'{value:,.0f}'
    if value != 0 and abs(value) < 0.01: return f'{value:.3g}'
    return f'{value:,.4f}'.rstrip('0').rstrip('.')


def frame(item, formatted=True):
    columns = [c['name'] for c in item['columns']]
    # Repeated headers (such as two "Field" columns) need distinct labels in a table.
    seen = {}; labels = []
    for name in columns:
        seen[name] = seen.get(name, 0) + 1
        labels.append(name if seen[name] == 1 else f'{name} ({seen[name]})')
    rows = item['rows']
    if formatted:
        rows = [[format_value(v, c['kind']) for v, c in zip(row, item['columns'])] for row in rows]
    return pd.DataFrame(rows, columns=labels)


def report_tables(output):
    """Every table in report order, as (id, title, DataFrame of raw values)."""
    return [(item['id'], item['title'], frame(item, formatted=False)) for item in iter_items(output) if item['type'] == 'table']


STYLE = '''body{font:15px system-ui,-apple-system,sans-serif;color:#172c40;max-width:1180px;margin:32px auto;padding:0 20px;line-height:1.55}
h1{font-size:28px;margin-bottom:4px}h2{margin-top:44px;border-bottom:2px solid #cad4df;padding-bottom:6px}h3{margin-top:30px}h4{margin:22px 0 6px}
table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}td,th{padding:6px 8px;border-bottom:1px solid #dde4ec;text-align:left;vertical-align:top}
th{background:#edf3f8;position:sticky;top:0}td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.wrap{max-height:520px;overflow:auto;border:1px solid #dde4ec;border-radius:6px}.note{color:#4b5f74;font-size:13px}
.failed{background:#fdecee;border:1px solid #e9a3ab;padding:10px 14px;border-radius:6px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f5f8;padding:10px;font-size:12px}
.conclusions{min-height:240px;border:1px dashed #9fb0c2;border-radius:6px;padding:12px}nav a{margin-right:14px}'''


def html_table(item):
    data = frame(item)
    numeric = [c['kind'] not in ('text',) for c in item['columns']]
    head = ''.join(f'<th>{escape(str(c))}</th>' for c in data.columns)
    body = ''.join('<tr>' + ''.join(f'<td class="n">{escape(v)}</td>' if is_number else f'<td>{escape(v)}</td>'
                                    for v, is_number in zip(row, numeric)) + '</tr>' for row in data.itertuples(index=False))
    parts = [f'<h4 id="{escape(item["id"], quote=True)}">{escape(item["title"])}</h4>']
    parts.append(f'<div class="wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>' if len(data)
                 else '<p class="note">No rows.</p>')
    if item.get('note'): parts.append(f'<p class="note">{escape(item["note"])}</p>')
    return '\n'.join(parts)


def render_items(items, state):
    parts = []
    for item in items:
        if item['type'] == 'table': parts.append(html_table(item))
        elif item['type'] == 'text': parts.append(f'<p>{escape(item["text"])}</p>')
        else:
            figure = pio.from_json(item['figure'])
            include = not state['plotly']
            state['plotly'] = True
            parts.append(f'<h4 id="{escape(item["id"], quote=True)}">{escape(item["title"])}</h4>')
            parts.append(figure.to_html(full_html=False, include_plotlyjs=True if include else False, div_id='chart-' + item['id']))
    return '\n'.join(parts)


def header_rows(output):
    return [('Dataset', output['dataset']), ('Table', output['table']), ('Upload SHA-256', output['source_sha256']),
            ('Rows', f'{output["rows"]:,}'), ('Columns', f'{output["column_count"]:,}'),
            ('Original version', output['original_version']), ('Analysed version', output['analysis_version']),
            ('Analysis date (UTC)', output['created_at']), ('Target', output.get('target') or 'none'),
            ('Target type', output.get('target_type') or '—'), ('Positive class', output.get('positive') or '—'),
            ('Time axis', output.get('time_axis') or 'none')]


def method_items(output):
    s = SETTINGS
    tests = [
        ['Categorical field, binary or multiclass target', "Pearson chi-square test of independence (no continuity correction); effect size Cramér's V"],
        ['Categorical field, numeric target', 'Kruskal–Wallis H with tie correction; effect size epsilon-squared = H / (n − 1)'],
        ['Numeric field, binary target', 'Mann–Whitney U, two-sided normal approximation with tie and continuity correction; effect size rank-biserial r = 2U/(n₁n₂) − 1'],
        ['Numeric field, multiclass target', 'Kruskal–Wallis H with tie correction; effect size epsilon-squared'],
        ['Numeric field, numeric target', 'Spearman ρ (Pearson correlation of average ranks), p from t with n − 2 df; Pearson r for reference'],
        ['Date field', 'Counts and target summaries per period; no test'],
        ['Field pairs', "Cramér's V (categorical), Spearman ρ (numeric), correlation ratio η (mixed)"],
        ['Multiple comparisons', 'Benjamini–Hochberg adjusted p-values across all ranked fields'],
        ['Missing values', 'Missing, blank and NA-like values form their own "(missing)" category for categorical fields and are excluded from numeric calculations'],
    ]
    thresholds = [[key.replace('_', ' '), value] for key, value in s.items()]
    items = [{'type': 'table', 'id': 'method-tests', 'title': 'Methods', 'columns': [{'name': 'Situation', 'kind': 'text'}, {'name': 'Method', 'kind': 'text'}],
              'rows': tests, 'note': 'Statistical associations are not causes. Effect sizes are reported as numbers without labels.'},
             {'type': 'table', 'id': 'method-thresholds', 'title': 'Thresholds and limits', 'columns': [{'name': 'Setting', 'kind': 'text'},
              {'name': 'Value', 'kind': 'number'}], 'rows': thresholds, 'note': f'NA-like markers: N/A, NA, NULL, ?, - (any case), empty and whitespace-only values.'},
             {'type': 'table', 'id': 'method-skipped', 'title': 'Skipped fields', 'columns': [{'name': 'Field', 'kind': 'text'}, {'name': 'Reason', 'kind': 'text'}],
              'rows': output['skipped'], 'note': None},
             {'type': 'table', 'id': 'method-failures', 'title': 'Fields whose analysis failed', 'columns': [{'name': 'Field', 'kind': 'text'},
              {'name': 'Error', 'kind': 'text'}], 'rows': output['field_failures'], 'note': None},
             {'type': 'table', 'id': 'method-sampling', 'title': 'Sampling', 'columns': [{'name': 'Chart', 'kind': 'text'}, {'name': 'Rows shown', 'kind': 'count'},
              {'name': 'Rows available', 'kind': 'count'}, {'name': 'Seed', 'kind': 'count'}, {'name': 'Method', 'kind': 'text'}], 'rows': output['sampling'],
              'note': 'Only scatter plots are sampled. Every statistic uses all rows.'},
             {'type': 'table', 'id': 'method-small', 'title': f'Groups with fewer than {s["small_group_rows"]} rows', 'columns': [{'name': 'Field', 'kind': 'text'},
              {'name': 'Group', 'kind': 'text'}, {'name': 'Rows', 'kind': 'count'}], 'rows': output['small_sample_warnings'], 'note': None}]
    return items


def html_report(output):
    state = {'plotly': False}
    title = f'Analysis report: {output["dataset"]}'
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
             f'<title>{escape(title)}</title><style>{STYLE}</style></head><body>', f'<h1>{escape(title)}</h1>']
    parts.append('<table>' + ''.join(f'<tr><th>{escape(k)}</th><td>{escape(str(v))}</td></tr>' for k, v in header_rows(output)) + '</table>')
    failed = [s for s in output['stages'] if s['status'] == 'failed']
    if failed:
        parts.append('<div class="failed"><strong>Failed stages</strong><ul>' + ''.join(
            f'<li>{escape(s["title"])}: {escape(s.get("error", ""))}</li>' for s in failed) + '</ul></div>')
    parts.append('<h4>Stages</h4><table><tr><th>Stage</th><th>Status</th><th>Detail</th><th>Seconds</th></tr>' + ''.join(
        f'<tr><td>{escape(s["title"])}</td><td>{escape(s["status"])}</td><td>{escape(s.get("detail") or s.get("error") or "")}</td>'
        f'<td class="n">{s["seconds"]:.2f}</td></tr>' for s in output['stages']) + '</table>')
    sections = output['sections']; number = 1
    parts.append(render_items(sections.get('target_selection', []), state))
    parts.append('<nav>' + ''.join(f'<a href="#section-{key}">{escape(label)}</a>' for key, label in SECTION_TITLES if sections.get(key))
                 + '<a href="#section-method">Method and limitations</a><a href="#section-evidence">Evidence</a><a href="#section-conclusions">Analyst conclusions</a></nav>')
    for key, label in SECTION_TITLES:
        if key not in sections: continue
        number += 1
        parts.append(f'<h2 id="section-{key}">{number}. {escape(label)}</h2>')
        if key == 'fields':
            for field in sections['fields']:
                parts.append(f'<h3 id="field-{escape(field["field"], quote=True)}">{escape(field["field"])} <span class="note">({escape(field["type"])})</span></h3>')
                parts.append(render_items(field['items'], state))
        else:
            parts.append(render_items(sections[key], state))
    number += 1
    parts.append(f'<h2 id="section-method">{number}. Method and limitations</h2>')
    parts.append(render_items(method_items(output), state))
    number += 1
    parts.append(f'<h2 id="section-evidence">{number}. Evidence appendix</h2><p class="note">The query or computation behind every table and chart, '
                 'and the table version it ran on.</p>')
    for item in iter_items(output):
        if item['type'] == 'text': continue
        evidence = item['evidence']
        queries = ''.join(f'<pre>{escape(q["sql"])}</pre>' + (f'<p class="note">Parameters: {escape(json.dumps(q["parameters"]))}</p>' if q['parameters'] else '')
                          for q in evidence.get('queries', []))
        parts.append(f'<details><summary>{escape(item["title"])} · version {escape(evidence["version"])}</summary>'
                     f'<p>{escape(evidence["computation"])}</p>{queries}</details>')
    number += 1
    parts.append(f'<h2 id="section-conclusions">{number}. Analyst conclusions</h2><div class="conclusions"></div></body></html>')
    return '\n'.join(parts)


def sheet_name(title, used):
    base = re.sub(r'[\[\]:*?/\\]', ' ', title).strip()[:28] or 'Table'
    name = base; index = 2
    while name.lower() in used:
        name = f'{base[:25]} {index}'; index += 1
    used.add(name.lower())
    return name


def excel_report(output):
    buffer = io.BytesIO(); used = set(); index_rows = []
    tables = [item for item in iter_items(output) if item['type'] == 'table'] + method_items(output)
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        overview = pd.DataFrame(header_rows(output) + [(f'Stage: {s["title"]}', s['status'] + (f' — {s["error"]}' if s.get('error') else ''))
                                                        for s in output['stages']], columns=['Item', 'Value'])
        safe_spreadsheet_frame(overview).to_excel(writer, index=False, sheet_name=sheet_name('Overview', used))
        for item in tables:
            name = sheet_name(item['title'], used)
            data = safe_spreadsheet_frame(frame(item, formatted=False))
            data.to_excel(writer, index=False, sheet_name=name)
            sheet = writer.sheets[name]
            for position, column in enumerate(item['columns'], 1):
                fmt = {'percent': '0.0%', 'p': '0.00E+00', 'effect': '0.000', 'count': '#,##0'}.get(column['kind'])
                if fmt:
                    for row in range(2, len(data) + 2): sheet.cell(row=row, column=position).number_format = fmt
            index_rows.append([name, item['title'], (item.get('evidence') or {}).get('version', ''), (item.get('evidence') or {}).get('computation', '')])
        evidence = []
        for item in iter_items(output):
            if item['type'] == 'text': continue
            for query in item['evidence'].get('queries', []) or [{'sql': '', 'parameters': []}]:
                evidence.append([item['title'], item['evidence']['version'], item['evidence']['computation'], query['sql'][:32000]])
        safe_spreadsheet_frame(pd.DataFrame(index_rows, columns=['Sheet', 'Table', 'Version', 'Computation'])).to_excel(
            writer, index=False, sheet_name=sheet_name('Contents', used))
        safe_spreadsheet_frame(pd.DataFrame(evidence, columns=['Table or chart', 'Version', 'Computation', 'Query'])).to_excel(
            writer, index=False, sheet_name=sheet_name('Evidence', used))
        pd.DataFrame(columns=['Analyst conclusions']).to_excel(writer, index=False, sheet_name=sheet_name('Analyst conclusions', used))
    return buffer.getvalue()
