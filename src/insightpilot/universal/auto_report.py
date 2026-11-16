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

from .auto.report import chart_svg, evidence as evidence_json, findings_csv, results as results_json
from .auto_analysis import SETTINGS, iter_items
from .report import safe_spreadsheet_frame

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


STYLE = '''body{font:15px system-ui,-apple-system,sans-serif;color:#172c40;background:#ffffff;max-width:1180px;margin:32px auto;padding:0 16px;line-height:1.55}
h1{font-size:28px;margin-bottom:4px}h2{margin-top:44px;border-bottom:2px solid #cad4df;padding-bottom:6px}h3{margin-top:30px}h4{margin:22px 0 6px}
table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}td,th{padding:6px 8px;border-bottom:1px solid #dde4ec;text-align:left;vertical-align:top}
th{background:#edf3f8;position:sticky;top:0}td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.wrap{max-height:520px;overflow:auto;border:1px solid #dde4ec;border-radius:6px}.note{color:#4b5f74;font-size:13px}
.failed{background:#fdecee;border:1px solid #e9a3ab;padding:10px 14px;border-radius:6px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f5f8;padding:10px;font-size:12px}
.conclusions{min-height:240px;border:1px dashed #9fb0c2;border-radius:6px;padding:12px}nav{display:flex;flex-wrap:wrap;gap:4px 14px;margin:12px 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:12px 0}
.tile{border:1px solid #dde4ec;border-radius:8px;padding:12px 14px;background:#f7f9fb}.tile .v{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums}
.tile .l{color:#4b5f74;font-size:13px}.finding{border-left:4px solid #9fb0c2;padding:6px 12px;margin:10px 0;background:#f7f9fb}
.finding.critical{border-color:#D55E00}.finding.important{border-color:#0072B2}.finding p{margin:4px 0}.fid{font-weight:700;margin-right:8px}
.badge{font-size:12px;color:#4b5f74;text-transform:uppercase;letter-spacing:.03em}figure.chart{margin:10px 0 18px;max-width:760px}
figcaption{color:#4b5f74;font-size:13px}details{margin:6px 0}summary{cursor:pointer}details.field>summary{font-size:17px;font-weight:600;margin-top:10px}
.ai{border:1px solid #cad4df;border-radius:6px;padding:10px 14px;background:#fbfbf4}'''

SCRIPT = '''<script>function openTo(h){if(!h||h.length<2)return;var e=document.getElementById(decodeURIComponent(h.slice(1)));if(!e)return;
for(var d=e;d;d=d.parentElement){if(d.tagName==='DETAILS')d.open=true}e.scrollIntoView()}
document.addEventListener('click',function(ev){var a=ev.target.closest('a[href^="#"]');if(a){ev.preventDefault();history.replaceState(null,'',a.getAttribute('href'));openTo(a.getAttribute('href'))}});
window.addEventListener('hashchange',function(){openTo(location.hash)});if(location.hash)openTo(location.hash);</script>'''


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
            parts.append(f'<h4 id="{escape(item["id"], quote=True)}">{escape(item["title"])}</h4>')
            svg = chart_svg(item['figure'], '')
            state['charts'] += 1
            if svg is None:
                state['unsupported'] += 1
                parts.append('<p class="note">This chart type is shown in the app only; its numbers are in the neighbouring tables.</p>')
            else:
                parts.append(svg)
    return '\n'.join(parts)


def header_rows(output):
    return [('Dataset', output['dataset']), ('Table', output['table']), ('Upload SHA-256', output['source_sha256']),
            ('Rows', f'{output["rows"]:,}'), ('Columns', f'{output["column_count"]:,}'),
            ('Original version', output['original_version']), ('Analysed version', output['analysis_version']),
            ('Analysis date (UTC)', output['created_at']), ('Target', output.get('target') or 'none'),
            ('Target type', output.get('target_type') or '—'), ('Positive class', output.get('positive') or '—'),
            ('Mode', 'target analysis' if output.get('target') else 'descriptive (no target)'),
            ('Time axis', output.get('time_axis') or 'none'), ('Configuration hash', output.get('config_hash', '—')),
            ('Seed', output.get('seed', '—')),
            ('Software', ', '.join(f'{k} {v}' for k, v in (output.get('versions') or {}).items()) or '—')]


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
        ['Ranking', 'Cross-validated single-feature predictive power: each field is binned (levels, or equal-count bins for numbers, plus a missing bin), '
                    'encoded with its smoothed target rate or mean from the other folds, and scored out of fold (ΔAUC = AUC − 0.5, macro one-vs-rest '
                    'for multiclass; R² for numeric targets). Normalised mutual information minus its mean under exact permutations is the second measure'],
        ['Group rates', 'Wilson score 95% intervals; difference vs the overall rate in percentage points; lift = group rate / overall rate'],
        ['Group medians', 'Percentile bootstrap 95% intervals (seeded)'],
        ['Conditional categories', 'A level of one column and a value of another cover the same rows; target comparisons use the applicable rows'],
        ['Derived columns', 'C ≈ k·A or k·(A op B), k fitted through the origin, confirmed by R² on all rows; listed under their sources in the ranking'],
        ['Redundancy clusters', "Average-linkage clusters of fields whose association (bias-corrected Cramér's V, Spearman ρ, correlation ratio η) exceeds the threshold"],
        ['Explained missing values', 'A condition is reported when it meets the coverage, lift and precision thresholds'],
        ['Leakage screen', 'Single-field power above the AUC/R² threshold, an exact dependency with the target, or a post-outcome date-name hint'],
        ['Multivariate model', 'HistGradientBoosting with native missing-value and categorical handling, cross-validated (stratified, or time-ordered '
                               'with a time axis) against baselines; permutation importance on held-out folds, grouped for redundancy clusters'],
        ['Adjusted model', 'Logistic (odds ratios) or linear (HC3) regression on cluster representatives, standardised numbers, pooled rare levels and '
                           'VIF pruning; associations holding the other listed fields constant'],
        ['Accumulated local effects', 'First-order ALE (Apley & Zhu 2020) on equal-count intervals, centred to mean zero'],
        ['Confounding screen', 'Cochran–Mantel–Haenszel common odds ratio with Breslow–Day homogeneity (binary targets) or a weighted within-stratum '
                               'mean difference (numeric targets), compared with the crude effect'],
        ['Interactions', 'Likelihood-ratio test of each pair of top fields (product terms, or joint levels for two categories) with '
                         'Benjamini–Hochberg adjustment across pairs; reported only when the cross-validated gain also exceeds the threshold and twice its standard error'],
        ['Segments', 'A shallow decision tree grown on 80% of the model sample, a split kept only when its test gives p < 0.001; each leaf validated '
                     'on the other 20% (lift threshold and interval excluding the overall value); surviving rules re-counted on all rows'],
        ['Findings digest', 'Fixed rules turn results into findings. Critical: leakage suspects. Important: a model no better than its baseline, '
                            'single-field power of at least 0.05 ΔAUC or 0.02 R² with an adjusted p below 0.05 (rank test or permutation test), validated '
                            'segments, direction reversals, interactions, explained missing values and data-quality issues affecting many rows. '
                            'Informative: the rest. A null result needs both adjusted p-values ≥ 0.05 and |power| below 0.01'],
        ['Charts', 'Static SVG in the Okabe–Ito colour-blind-safe palette; rate bars show 95% Wilson intervals and a dashed overall-rate line; '
                   'bars are ordered by value except ordered bins and time'],
        ['Non-linear shapes', 'Flagged when the best monotone (isotonic) fit explains less of the between-bin variation than the threshold and the bins differ (p < 0.01)'],
    ]
    thresholds = [[key.replace('_', ' '), value] for key, value in s.items()]
    thresholds += [[key.replace('_', ' '), value] for key, value in (output.get('config') or {}).items() if key != 'config_version']
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


# Report order: header, at a glance, warnings, top findings, associated fields, segments, clusters (no target), structure, field details,
# relationships, time, data quality, method, (optional local-AI summary), analyst conclusions, evidence appendix.
REPORT_SECTIONS = [
    ('drivers', 'Fields associated with the target', ['target_selection', 'target', 'leakage', 'ranking', 'multivariate'],
     'No target was selected, so fields are not compared with an outcome.'),
    ('segments', 'Segments', ['segments'], 'No validated segments: segments need a target and enough rows.'),
    ('patterns', 'Clusters, unusual rows and shapes', ['unsupervised'], 'Computed only when no target is chosen.'),
    ('structure', 'Structure', ['structure'], 'No structure results.'),
    ('fields', 'Field details', ['fields'], 'No fields were analysed.'),
    ('relationships', 'Relationships, confounding and interactions', ['confounding', 'relationships'], 'No relationship results.'),
    ('time', 'Time', ['time'], 'No time axis was selected.'),
    ('quality', 'Data quality and corrections', ['quality', 'corrections'], 'No data quality results.'),
]
EVIDENCE_INLINE_BYTES = 350_000
CONCLUSIONS_NOTE = ('This section is reserved for the analyst and is intentionally empty. The findings digest lists what the data shows, '
                    'each with its numbers, interval and a link to the evidence; conclusions and decisions are left to the analyst.')


def tiles(output):
    """At most five headline numbers, all taken from computed results."""
    found = [(f'{output["rows"]:,} × {output["column_count"]:,}', 'rows × columns analysed')]
    overall = output.get('segment_overall')
    if output.get('target') and overall is not None:
        if output.get('target_type') == 'binary':
            found.append((f'{overall * 100:.1f}%', f'{output["target"]} = {output.get("positive")}'))
        elif output.get('target_type') == 'numeric':
            found.append((format_value(overall, 'number'), f'mean {output["target"]}'))
    metrics = (output.get('multivariate') or {}).get('metrics') or []
    if metrics:
        name, value, sd, baseline = metrics[0][:4]
        found.append((f'{value:.3f} ± {sd:.3f}', f'cross-validated {name} (baseline {format_value(baseline, "number")})'))
    ranking = [r for r in output.get('ranking') or [] if r.get('power') is not None]
    if ranking:
        top = ranking[0]
        found.append((top['field'], f'highest single-field {top.get("power_metric", "power")} ({top["power"]:.3f})'))
    counts = output.get('finding_counts') or {}
    if counts:
        found.append((f'{sum(counts.values()):,}', ' · '.join(f'{n} {s}' for s, n in counts.items() if n) or 'findings'))
    return found[:5]


def finding_html(finding):
    caveats = ' '.join(finding.get('caveats') or [])
    link = f'<a href="#{escape(finding["evidence"], quote=True)}">evidence</a>' if finding.get('evidence') else ''
    return (f'<div class="finding {escape(finding["severity"])}" id="finding-{escape(finding["id"])}"><span class="fid">{escape(finding["id"])}</span>'
            f'<span class="badge">{escape(finding["severity"])} · {escape(finding["label"])}</span><p>{escape(finding["statement"])}</p>'
            f'<p class="note">{escape(caveats)}{" · " if caveats and link else ""}{link}</p></div>')


def findings_html(output, top):
    findings = output.get('findings') or []
    if not findings: return '<p class="note">No findings met the reporting thresholds.</p>'
    parts = [''.join(finding_html(f) for f in findings[:top])]
    if len(findings) > top:
        parts.append(f'<details><summary>All {len(findings)} findings</summary>'
                     + ''.join(finding_html(f) for f in findings[top:]) + '</details>')
    return '\n'.join(parts)


def ai_html(section):
    if not section: return ''
    parts = [f'<h2 id="section-ai">Local AI summary (generated)</h2><div class="ai"><p class="note">Written by the local model '
             f'{escape(str(section.get("model") or ""))} from the findings digest only. Each sentence cites the findings it uses; sentences '
             'whose numbers were not in the cited findings, or that used causal wording, were removed. It is not part of the analysis.</p><ul>']
    parts += [f'<li>{escape(s["text"])}</li>' for s in section.get('sentences', [])]
    parts.append('</ul>')
    if section.get('rejected'): parts.append(f'<p class="note">{len(section["rejected"])} sentence(s) failed the checks and were removed.</p>')
    return ''.join(parts) + '</div>'


def evidence_html(output):
    parts = []; budget = EVIDENCE_INLINE_BYTES; moved = 0
    for item in iter_items(output):
        if item['type'] == 'text': continue
        evidence = item['evidence']; queries = ''
        for q in evidence.get('queries', []):
            block = f'<pre>{escape(q["sql"])}</pre>' + (f'<p class="note">Parameters: {escape(json.dumps(q["parameters"]))}</p>' if q['parameters'] else '')
            if len(block) <= budget:
                queries += block; budget -= len(block)
            else:
                moved += 1
        if evidence.get('queries') and not queries: queries = '<p class="note">Query text in evidence.json.</p>'
        parts.append(f'<details><summary>{escape(item["title"])} · version {escape(evidence["version"])}</summary>'
                     f'<p>{escape(evidence["computation"])}</p>{queries}</details>')
    note = ('<p class="note">The query or computation behind every table and chart, and the table version it ran on. '
            + (f'{moved:,} queries did not fit in the report size budget; every query is in the evidence.json download.' if moved
               else 'Every query is also in the evidence.json download.') + '</p>')
    return note + '\n'.join(parts)


def html_report(output, ai_section=None):
    """One self-contained HTML file: inline SVG charts, no scripts from the network."""
    state = {'charts': 0, 'unsupported': 0}
    title = f'Analysis report: {output["dataset"]}'
    sections = output['sections']; number = 0
    top = (output.get('config') or {}).get('top_findings', 10)

    def heading(key, label):
        nonlocal number
        number += 1
        return f'<h2 id="section-{key}">{number}. {escape(label)}</h2>'

    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
             f'<title>{escape(title)}</title><style>{STYLE}</style></head><body>', f'<h1>{escape(title)}</h1>']
    parts.append('<table>' + ''.join(f'<tr><th>{escape(k)}</th><td>{escape(str(v))}</td></tr>' for k, v in header_rows(output)) + '</table>')
    nav = [('glance', 'At a glance'), ('warnings', 'Warnings'), ('findings', 'Top findings')] + [(k, l) for k, l, *_ in REPORT_SECTIONS] \
        + [('method', 'Method and limitations')] + ([('ai', 'Local AI summary')] if ai_section else []) \
        + [('conclusions', 'Analyst conclusions'), ('evidence', 'Evidence appendix')]
    parts.append('<nav>' + ''.join(f'<a href="#section-{k}">{escape(l)}</a>' for k, l in nav) + '</nav>')

    parts.append(heading('glance', 'At a glance'))
    parts.append('<div class="tiles">' + ''.join(f'<div class="tile"><div class="v">{escape(v)}</div><div class="l">{escape(l)}</div></div>' for v, l in tiles(output)) + '</div>')

    parts.append(heading('warnings', 'Warnings'))
    failed = [s for s in output['stages'] if s['status'] == 'failed']
    warnings = list(output.get('warnings') or []) + [f'{s["title"]} failed: {s.get("error", "")}' for s in failed]
    parts.append('<div class="failed"><ul>' + ''.join(f'<li>{escape(w)}</li>' for w in warnings) + '</ul></div>' if warnings else '<p class="note">No warnings.</p>')

    parts.append(heading('findings', f'Top {min(top, len(output.get("findings") or []))} findings' if output.get('findings') else 'Findings'))
    parts.append('<p class="note">Ordered by severity; within a severity, finding types take turns so different kinds of result appear first. '
                 'Severity follows fixed rules (see Method). Associations are not causes.</p>')
    parts.append(findings_html(output, top))

    for key, label, sources, empty in REPORT_SECTIONS:
        parts.append(heading(key, label))
        present = [s for s in sources if sections.get(s)]
        if not present:
            parts.append(f'<p class="note">{escape(empty)}</p>'); continue
        if key == 'fields':
            parts.append('<p class="note">Every analysed field, in ranking order. Click a field to open its tables and charts.</p>')
            for field in sections['fields']:
                parts.append(f'<details class="field" id="field-{escape(field["field"], quote=True)}"><summary>{escape(field["field"])} '
                             f'<span class="note">({escape(field["type"])})</span></summary>')
                parts.append(render_items(field['items'], state)); parts.append('</details>')
        else:
            for source in present: parts.append(render_items(sections[source], state))

    parts.append(heading('method', 'Method and limitations'))
    parts.append('<h4>Stages</h4><table><tr><th>Stage</th><th>Status</th><th>Detail</th><th>Seconds</th></tr>' + ''.join(
        f'<tr><td>{escape(s["title"])}</td><td>{escape(s["status"])}</td><td>{escape(s.get("detail") or s.get("error") or "")}</td>'
        f'<td class="n">{s["seconds"]:.2f}</td></tr>' for s in output['stages']) + '</table>')
    parts.append(render_items(method_items(output), state))
    if ai_section: parts.append(ai_html(ai_section))
    number += 1
    parts.append(f'<h2 id="section-conclusions">{number}. Analyst conclusions</h2><p class="note">{escape(CONCLUSIONS_NOTE)}</p><div class="conclusions"></div>')
    parts.append(heading('evidence', 'Evidence appendix'))
    parts.append(evidence_html(output))
    parts.append(SCRIPT + '</body></html>')
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
        digest = pd.DataFrame([[f['id'], f['severity'], f['label'], f['statement'], f.get('evidence') or '', ' '.join(f.get('caveats') or [])]
                               for f in output.get('findings') or []], columns=['ID', 'Severity', 'Type', 'Statement', 'Evidence', 'Caveats'])
        safe_spreadsheet_frame(digest).to_excel(writer, index=False, sheet_name=sheet_name('Findings', used))
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


def downloads(output):
    """Machine-readable companions to the HTML report: {filename suffix: (bytes, mime type)}."""
    return {'results.json': (results_json(output).encode(), 'application/json'),
            'findings.csv': (findings_csv(output).encode('utf-8-sig'), 'text/csv'),
            'evidence.json': (evidence_json(output).encode(), 'application/json')}
