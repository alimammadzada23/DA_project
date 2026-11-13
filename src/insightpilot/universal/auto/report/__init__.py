"""Report building blocks: compact inline SVG charts and the machine-readable exports.

Charts are drawn as static SVG from the same Plotly figures the app shows, in the Okabe–Ito
colour-blind-safe palette, so the HTML report needs no chart library and stays small.
"""
import base64
import csv
from html import escape
import io
import json
import math

import numpy as np
import plotly.io as pio

PALETTE = ['#0072B2', '#E69F00', '#009E73', '#CC79A7', '#56B4E9', '#D55E00', '#F0E442', '#000000']
INK, MUTED, GRID, REFERENCE = '#1f2d3d', '#5f6f82', '#e3e8ee', '#D55E00'
WIDTH = 720
MAX_POINTS = 1500


def decode(seq):
    """Plotly stores numeric arrays as base64 typed arrays ({dtype, bdata, shape}); turn them back into lists."""
    if isinstance(seq, dict) and 'bdata' in seq:
        array = np.frombuffer(base64.b64decode(seq['bdata']), dtype=np.dtype(seq['dtype']))
        if seq.get('shape'): array = array.reshape([int(n) for n in str(seq['shape']).split(',')])
        return array.tolist()
    if isinstance(seq, np.ndarray): return seq.tolist()
    return seq


def _values(seq):
    output = []
    for v in (decode(seq) if seq is not None else []):
        try:
            f = float(v)
            output.append(f if math.isfinite(f) else None)
        except (TypeError, ValueError):
            output.append(None)
    return output


def _labels(seq):
    return [str(v) for v in (decode(seq) if seq is not None else [])]


def nice_ticks(low, high, count=5):
    if high == low: high = low + 1
    raw = (high - low) / count; magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    start = math.floor(low / step) * step; end = math.ceil(high / step) * step
    return [round(start + step * i, 12) for i in range(int(round((end - start) / step)) + 1)]


def _fmt(value, percent):
    if percent: return f'{value * 100:.0f}%'
    if abs(value) >= 1000: return f'{value:,.0f}'
    return f'{value:.3g}'


def _text(x, y, content, size=11, anchor='start', color=INK, weight='normal'):
    return f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" fill="{color}" font-weight="{weight}">{escape(str(content))}</text>'


def _frame(height, title, body, caption=''):
    head = _text(8, 18, title, 13, weight='600') if title else ''
    return (f'<figure class="chart"><svg viewBox="0 0 {WIDTH} {height}" width="100%" role="img" aria-label="{escape(title or "chart", quote=True)}" '
            f'xmlns="http://www.w3.org/2000/svg" font-family="system-ui,sans-serif">{head}{body}</svg>'
            + (f'<figcaption>{escape(caption)}</figcaption>' if caption else '') + '</figure>')


def _references(layout, axis):
    lines = []
    for shape in layout.shapes or []:
        if axis == 'x' and shape.x0 is not None and shape.x0 == shape.x1: lines.append(float(shape.x0))
        if axis == 'y' and shape.y0 is not None and shape.y0 == shape.y1: lines.append(float(shape.y0))
    return lines


def horizontal_bars(fig, title, caption):
    trace = fig.data[0]
    labels = _labels(trace.y); values = _values(trace.x)
    plus = _values(trace.error_x.array) if trace.error_x and trace.error_x.array is not None else [0] * len(values)
    minus = _values(trace.error_x.arrayminus) if trace.error_x and trace.error_x.arrayminus is not None else plus
    texts = _labels(trace.text) if trace.text is not None else [''] * len(values)
    rows = list(zip(labels, values, plus, minus, texts))[:40]
    if fig.layout.yaxis.autorange != 'reversed': rows = rows[::-1]
    percent = (fig.layout.xaxis.tickformat or '').endswith('%')
    references = _references(fig.layout, 'x')
    top = max([v + (p or 0) for _, v, p, _, _ in rows if v is not None] + references + [0])
    low = min([v - (m or 0) for _, v, _, m, _ in rows if v is not None] + [0])
    ticks = nice_ticks(low, top * 1.1)
    left = min(240, 12 + 6.4 * max((len(l) for l, *_ in rows), default=4)); right = WIDTH - 110
    scale = lambda v: left + (v - ticks[0]) / (ticks[-1] - ticks[0]) * (right - left)
    height = 46 + 24 * len(rows) + 28; body = []
    for t in ticks:
        x = scale(t); body.append(f'<line x1="{x:.1f}" y1="30" x2="{x:.1f}" y2="{height - 26}" stroke="{GRID}"/>')
        body.append(_text(x, height - 10, _fmt(t, percent), 10, 'middle', MUTED))
    for index, (label, value, p, m, text) in enumerate(rows):
        y = 36 + 24 * index
        body.append(_text(left - 6, y + 12, label[:34], 11, 'end'))
        if value is None: continue
        x0, x1 = scale(min(0, value)), scale(max(0, value))
        body.append(f'<rect x="{x0:.1f}" y="{y + 2}" width="{max(x1 - x0, 1):.1f}" height="16" fill="{PALETTE[0]}"/>')
        if p or m:
            a_, b_ = scale(value - (m or 0)), scale(value + (p or 0))
            body.append(f'<line x1="{a_:.1f}" y1="{y + 10}" x2="{b_:.1f}" y2="{y + 10}" stroke="{INK}" stroke-width="1.2"/>')
            body += [f'<line x1="{v:.1f}" y1="{y + 5}" x2="{v:.1f}" y2="{y + 15}" stroke="{INK}" stroke-width="1.2"/>' for v in (a_, b_)]
        body.append(_text(max(x1, scale(value + (p or 0))) + 5, y + 14, text or _fmt(value, percent), 10, 'start', MUTED))
    for r in references:
        x = scale(r); body.append(f'<line x1="{x:.1f}" y1="30" x2="{x:.1f}" y2="{height - 26}" stroke="{REFERENCE}" stroke-dasharray="4 3" stroke-width="1.5"/>')
        body.append(_text(x + 4, 30, f'overall {r * 100:.1f}%' if percent else _fmt(r, False), 10, 'start', REFERENCE))
    return _frame(height, title, ''.join(body), caption)


def vertical_bars(fig, title, caption):
    traces = [t for t in fig.data if t.type == 'bar']
    numeric = traces[0].width is not None
    percent = (fig.layout.yaxis.tickformat or '').endswith('%')
    stacked = fig.layout.barmode == 'stack' and len(traces) > 1
    left, right, top_y, bottom = 60, WIDTH - 16, 36, 300 - 54
    if numeric:
        t = traces[0]; xs = _values(t.x); ys = _values(t.y)
        widths = _values(t.width) if not isinstance(t.width, (int, float)) else [float(t.width)] * len(xs)
        offsets = _values(t.offset) if t.offset is not None and not isinstance(t.offset, (int, float)) else [0.0] * len(xs)
        spans = [(x + o, x + o + w) for x, o, w in zip(xs, offsets, widths) if x is not None and w is not None]
        lo, hi = min(s[0] for s in spans), max(s[1] for s in spans)
        top = max(v for v in ys if v is not None); ticks_y = nice_ticks(0, top * 1.08)
        sx = lambda v: left + (v - lo) / ((hi - lo) or 1) * (right - left)
        sy = lambda v: bottom - (v - ticks_y[0]) / (ticks_y[-1] - ticks_y[0]) * (bottom - top_y)
        body = axes_y(ticks_y, sy, left, right, percent)
        for (a_, b_), v in zip(spans, ys):
            if v is None: continue
            body.append(f'<rect x="{sx(a_):.1f}" y="{sy(v):.1f}" width="{max(sx(b_) - sx(a_) - 1, 1):.1f}" height="{bottom - sy(v):.1f}" fill="{PALETTE[0]}"/>')
        for t_ in nice_ticks(lo, hi, 6):
            if lo <= t_ <= hi: body.append(_text(sx(t_), bottom + 16, _fmt(t_, False), 10, 'middle', MUTED))
        return _frame(300, title, ''.join(body), caption)
    categories = _labels(traces[0].x)[:40]
    series = [[v or 0 for v in _values(t.y)[:40]] for t in traces]
    totals = [sum(col) for col in zip(*series)] if stacked else [max(col) for col in zip(*series)]
    ticks_y = nice_ticks(min(0, min(min(s) for s in series)), max(totals + [0]) * 1.08)
    band = (right - left) / max(len(categories), 1)
    sy = lambda v: bottom - (v - ticks_y[0]) / (ticks_y[-1] - ticks_y[0]) * (bottom - top_y)
    body = axes_y(ticks_y, sy, left, right, percent)
    for i, category in enumerate(categories):
        base = 0.0; x = left + band * i
        for k, values in enumerate(series):
            v = values[i]; width = band * 0.7 / (1 if stacked else len(series)); offset = band * 0.15 + (0 if stacked else width * k)
            y0, y1 = (base, base + v) if stacked else (0, v)
            body.append(f'<rect x="{x + offset:.1f}" y="{sy(max(y0, y1)):.1f}" width="{width:.1f}" height="{abs(sy(y0) - sy(y1)):.1f}" fill="{PALETTE[k % len(PALETTE)]}"/>')
            if stacked: base += v
        body.append(_text(x + band / 2, bottom + 14, category[:14], 10, 'middle', MUTED))
    body += legend([t.name for t in traces] if len(traces) > 1 else [])
    return _frame(300, title, ''.join(body), caption)


def axes_y(ticks, sy, left, right, percent):
    body = []
    for t in ticks:
        y = sy(t); body.append(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="{GRID}"/>')
        body.append(_text(left - 6, y + 4, _fmt(t, percent), 10, 'end', MUTED))
    return body


def legend(names):
    body = []
    for i, name in enumerate(names[:8]):
        x = 70 + 110 * i
        body.append(f'<rect x="{x}" y="276" width="10" height="10" fill="{PALETTE[i % len(PALETTE)]}"/>')
        body.append(_text(x + 14, 285, str(name)[:14], 10, 'start', MUTED))
    return body


def lines(fig, title, caption):
    traces = [t for t in fig.data if t.type in ('scatter', 'scattergl')]
    trace = traces[0]
    percent = (fig.layout.yaxis.tickformat or '').endswith('%')
    xs_raw = list(decode(trace.x)) if trace.x is not None else []; ys = _values(trace.y)
    categorical = any(not isinstance(v, (int, float)) for v in xs_raw)
    markers_only = (trace.mode or '') == 'markers'
    note = ''
    if markers_only and len(ys) > MAX_POINTS:
        xs_raw, ys = xs_raw[:MAX_POINTS], ys[:MAX_POINTS]
        note = f' (first {MAX_POINTS:,} of the sampled points)'
    xs = list(range(len(xs_raw))) if categorical else _values(xs_raw)
    plus = _values(trace.error_y.array) if trace.error_y and trace.error_y.array is not None else [0] * len(ys)
    minus = _values(trace.error_y.arrayminus) if trace.error_y and trace.error_y.arrayminus is not None else plus
    points = [(x, y, p or 0, m or 0) for x, y, p, m in zip(xs, ys, plus, minus) if x is not None and y is not None]
    if not points: return None
    references = _references(fig.layout, 'y')
    low = min([y - m for _, y, _, m in points] + references); high = max([y + p for _, y, p, _ in points] + references)
    ticks_y = nice_ticks(low, high); left, right, top_y, bottom = 60, WIDTH - 16, 36, 300 - 54
    x_lo, x_hi = min(p[0] for p in points), max(p[0] for p in points)
    sx = lambda v: left + (v - x_lo) / ((x_hi - x_lo) or 1) * (right - left)
    sy = lambda v: bottom - (v - ticks_y[0]) / (ticks_y[-1] - ticks_y[0]) * (bottom - top_y)
    body = axes_y(ticks_y, sy, left, right, percent)
    if markers_only:
        # One shared style and whole-pixel positions keep 1,500 points to a few dozen kilobytes.
        body.append(f'<g fill="{PALETTE[0]}" fill-opacity="0.45">' + ''.join(f'<circle cx="{sx(x):.0f}" cy="{sy(y):.0f}" r="1.8"/>' for x, y, _, _ in points) + '</g>')
    else:
        body.append('<polyline fill="none" stroke="' + PALETTE[0] + '" stroke-width="2" points="' + ' '.join(f'{sx(x):.1f},{sy(y):.1f}' for x, y, _, _ in points) + '"/>')
        for x, y, p, m in points:
            body.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="3" fill="{PALETTE[0]}"/>')
            if p or m: body.append(f'<line x1="{sx(x):.1f}" y1="{sy(y - m):.1f}" x2="{sx(x):.1f}" y2="{sy(y + p):.1f}" stroke="{INK}"/>')
    for r in references:
        body.append(f'<line x1="{left}" y1="{sy(r):.1f}" x2="{right}" y2="{sy(r):.1f}" stroke="{REFERENCE}" stroke-dasharray="4 3" stroke-width="1.5"/>')
    if categorical:
        step = max(1, len(xs_raw) // 8)
        for i in range(0, len(xs_raw), step): body.append(_text(sx(i), bottom + 16, str(xs_raw[i])[:16], 10, 'middle', MUTED))
    else:
        for t in nice_ticks(x_lo, x_hi, 6):
            if x_lo <= t <= x_hi: body.append(_text(sx(t), bottom + 16, _fmt(t, False), 10, 'middle', MUTED))
    return _frame(300, title + note, ''.join(body), caption)


def heatmap(fig, title, caption):
    trace = fig.data[0]
    z = [[None if v is None or (isinstance(v, float) and not math.isfinite(v)) else float(v) for v in row] for row in decode(trace.z)][:30]
    xs = _labels(trace.x)[:20]; ys = _labels(trace.y)[:30]
    z = [row[:len(xs)] for row in z]
    top = max([v for row in z for v in row if v is not None] + [1])
    left = min(220, 12 + 6.4 * max((len(y) for y in ys), default=4)); cell_w = (WIDTH - left - 16) / max(len(xs), 1); cell_h = 22
    height = 40 + cell_h * len(ys) + 30; body = []
    for i, label in enumerate(ys):
        body.append(_text(left - 6, 40 + cell_h * i + 15, label[:32], 10, 'end'))
        for j, value in enumerate(z[i] if i < len(z) else []):
            share = (value or 0) / top
            colour = f'rgb({int(255 - share * (255 - 0))},{int(255 - share * (255 - 114))},{int(255 - share * (255 - 178))})'
            x = left + cell_w * j; y = 40 + cell_h * i
            body.append(f'<rect x="{x:.1f}" y="{y}" width="{cell_w - 1:.1f}" height="{cell_h - 1}" fill="{colour}"/>')
            if value is not None and len(xs) * len(ys) <= 150:
                body.append(_text(x + cell_w / 2, y + 15, f'{value:,.0f}' if float(value).is_integer() else f'{value:.2f}', 10, 'middle',
                                  '#ffffff' if share > 0.55 else INK))
    for j, label in enumerate(xs): body.append(_text(left + cell_w * j + cell_w / 2, height - 10, label[:14], 10, 'middle', MUTED))
    return _frame(height, title, ''.join(body), caption)


def boxes(fig, title, caption):
    trace = fig.data[0]
    labels = _labels(trace.x)[:30]; q1 = _values(trace.q1); median = _values(trace.median); q3 = _values(trace.q3)
    low = _values(trace.lowerfence); high = _values(trace.upperfence)
    values = [v for seq in (low, high) for v in seq if v is not None]
    if not values: return None
    ticks_y = nice_ticks(min(values), max(values)); left, right, top_y, bottom = 60, WIDTH - 16, 36, 300 - 54
    band = (right - left) / max(len(labels), 1)
    sy = lambda v: bottom - (v - ticks_y[0]) / (ticks_y[-1] - ticks_y[0]) * (bottom - top_y)
    body = axes_y(ticks_y, sy, left, right, False)
    for i, label in enumerate(labels):
        if None in (q1[i], median[i], q3[i], low[i], high[i]): continue
        cx = left + band * (i + 0.5); w = min(40, band * 0.5)
        body.append(f'<line x1="{cx:.1f}" y1="{sy(low[i]):.1f}" x2="{cx:.1f}" y2="{sy(high[i]):.1f}" stroke="{INK}"/>')
        body.append(f'<rect x="{cx - w / 2:.1f}" y="{sy(q3[i]):.1f}" width="{w:.1f}" height="{max(sy(q1[i]) - sy(q3[i]), 1):.1f}" fill="{PALETTE[4]}" stroke="{INK}"/>')
        body.append(f'<line x1="{cx - w / 2:.1f}" y1="{sy(median[i]):.1f}" x2="{cx + w / 2:.1f}" y2="{sy(median[i]):.1f}" stroke="{INK}" stroke-width="2"/>')
        body.append(_text(cx, bottom + 16, label[:14], 10, 'middle', MUTED))
    return _frame(300, title, ''.join(body), caption)


def describe(fig):
    """One factual line: the largest value shown and the reference line, if any."""
    trace = fig.data[0]; kind = trace.type
    if kind == 'bar':
        horizontal = (trace.orientation or 'v') == 'h'
        labels = _labels(trace.y if horizontal else trace.x); values = _values(trace.x if horizontal else trace.y)
        axis = fig.layout.xaxis if horizontal else fig.layout.yaxis
        percent = (axis.tickformat or '').endswith('%')
        pairs = [(v, l) for l, v in zip(labels, values) if v is not None]
        if not pairs: return ''
        value, label = max(pairs)
        line = f'Largest: {label} ({_fmt(value, percent) if not percent else f"{value * 100:.1f}%"}).'
        references = _references(fig.layout, 'x' if horizontal else 'y')
        if references and percent: line += f' Dashed line: overall {references[0] * 100:.1f}%. Error bars: 95% intervals.' if trace.error_x and trace.error_x.array is not None else f' Dashed line: overall {references[0] * 100:.1f}%.'
        elif references: line += f' Dashed line: {_fmt(references[0], False)}.'
        return line
    if kind == 'heatmap':
        z = decode(trace.z); xs = _labels(trace.x); ys = _labels(trace.y)
        cells = [(v, ys[i], xs[j]) for i, row in enumerate(z) for j, v in enumerate(row)
                 if v is not None and isinstance(v, (int, float)) and math.isfinite(v) and i < len(ys) and j < len(xs)]
        if not cells: return ''
        value, y, x = max(cells)
        return f'Largest cell: {y} × {x} ({value:,.0f}).' if float(value).is_integer() else f'Largest cell: {y} × {x} ({value:.3g}).'
    if kind == 'box':
        medians = [(m, l) for m, l in zip(_values(trace.median), _labels(trace.x)) if m is not None]
        if not medians: return ''
        return f'Boxes show quartiles and whiskers the fences; medians range from {_fmt(min(medians)[0], False)} ({min(medians)[1]}) to {_fmt(max(medians)[0], False)} ({max(medians)[1]}).'
    if kind in ('scatter', 'scattergl'):
        values = [v for v in _values(trace.y) if v is not None]
        if not values: return ''
        percent = (fig.layout.yaxis.tickformat or '').endswith('%')
        line = f'Values range from {_fmt(min(values), percent)} to {_fmt(max(values), percent)}.'
        references = _references(fig.layout, 'y')
        if references: line += f' Dashed line: overall {references[0] * 100:.1f}%.' if percent else f' Dashed line: {_fmt(references[0], False)}.'
        return line
    return ''


def chart_svg(figure_json, title, caption=None):
    """Static SVG for a stored Plotly figure, or None when its chart type is not supported."""
    try:
        fig = pio.from_json(figure_json)
    except (ValueError, TypeError):
        return None
    types = {t.type for t in fig.data}
    if not fig.data: return None
    try:
        if caption is None: caption = describe(fig)
        if types == {'bar'} and all((t.orientation or 'v') == 'h' for t in fig.data): return horizontal_bars(fig, title, caption)
        if types == {'bar'}: return vertical_bars(fig, title, caption)
        if types <= {'scatter', 'scattergl'}: return lines(fig, title, caption)
        if types == {'heatmap'}: return heatmap(fig, title, caption)
        if types == {'box'}: return boxes(fig, title, caption)
    except (TypeError, ValueError, ZeroDivisionError, IndexError):
        return None
    return None


# ---------------- machine-readable exports ----------------
VOLATILE = ('run_id', 'created_at', 'saved_blocks')


def stable(output):
    """Version table names are random; results use stable labels so identical inputs give identical files."""
    text = json.dumps(output, default=str, sort_keys=True)
    for name, label in ((output.get('analysis_version'), 'version:analysed'), (output.get('original_version'), 'version:original')):
        if name: text = text.replace(name, label)
    return json.loads(text)


def results(output):
    """Everything structured: settings, stage statuses, findings and every table (no charts, queries or timings)."""
    from ...auto_analysis import iter_items
    data = stable(output)
    tables = [{'id': i['id'], 'title': i['title'], 'columns': i['columns'], 'rows': i['rows'], 'note': i.get('note')}
              for i in iter_items(data) if i['type'] == 'table']
    keep = {k: v for k, v in data.items() if k not in VOLATILE and k not in ('sections', 'stages', 'config')}
    keep['stages'] = [{'key': s['key'], 'title': s['title'], 'status': s['status'], 'error': s.get('error')} for s in data['stages']]
    keep['config'] = data.get('config'); keep['tables'] = tables
    return json.dumps(keep, sort_keys=True, indent=1, ensure_ascii=False) + '\n'


def findings_csv(output):
    buffer = io.StringIO(); writer = csv.writer(buffer)
    writer.writerow(['id', 'severity', 'type', 'label', 'statement', 'evidence', 'fields', 'caveats'])
    for f in stable(output).get('findings', []):
        writer.writerow([f['id'], f['severity'], f['type'], f['label'], safe_cell(f['statement']), f['evidence'], '; '.join(f['fields']),
                         safe_cell(' '.join(f['caveats']))])
    return buffer.getvalue()


def safe_cell(value):
    return "'" + value if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')) else value


def evidence(output):
    """The query or computation behind every table and chart, with the table version it ran on."""
    from ...auto_analysis import iter_items
    return json.dumps([{'id': i['id'], 'title': i['title'], **i['evidence']} for i in iter_items(output) if i['type'] != 'text'],
                      default=str, indent=1, ensure_ascii=False) + '\n'
