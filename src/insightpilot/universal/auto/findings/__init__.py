"""Findings digest: ranked, templated, factual statements built from the structured results.

Severity comes from fixed rules (effect size, rows affected, data risk), never from a model. Statements use
templates only: numbers with intervals, no interpretation, no recommendation and no causal wording.
"""
from dataclasses import asdict, dataclass, field
import re

from ...auto_stats import benjamini_hochberg

SEVERITY_ORDER = {'critical': 0, 'important': 1, 'informative': 2}
TYPE_LABELS = {
    'leakage_suspect': 'Possible leakage', 'no_signal': 'No joint signal', 'data_quality_issue': 'Data quality',
    'deterministic_missingness': 'Explained missing values', 'conditional_category': 'Conditional category',
    'derived_column': 'Derived column', 'redundancy_cluster': 'Redundant fields', 'strong_association': 'Association',
    'adjusted_effect_change': 'Change after adjustment', 'confounding_or_simpson': 'Confounding check', 'interaction': 'Interaction',
    'high_impact_segment': 'Segment', 'nonlinear_shape': 'Non-linear shape', 'null_result': 'No association',
    'trend': 'Trend', 'seasonality': 'Seasonality', 'break': 'Structural break', 'drift': 'Drift',
    'cluster_structure': 'Clusters', 'unusual_rows': 'Unusual rows', 'distribution_shape': 'Distribution shape'}


@dataclass
class Finding:
    type: str
    severity: str
    statement: str
    evidence: str                       # report item id with the supporting table or chart
    fields: list = field(default_factory=list)
    numbers: list = field(default_factory=list)
    caveats: list = field(default_factory=list)
    score: float = 0.0
    id: str = ''

    def to_dict(self):
        return asdict(self) | {'label': TYPE_LABELS[self.type]}


def pct(value): return f'{value * 100:.1f}%'
def num(value): return f'{value:,.0f}' if float(value).is_integer() and abs(value) >= 1 else f'{value:,.3g}'
def ci(low, high, percent=True):
    return f'[{pct(low)[:-1]}–{pct(high)}]' if percent else f'[{num(low)}–{num(high)}]'
def signed_pp(value): return f'{value:+.1f} pp'


def items_by_id(output):
    from ...auto_analysis import iter_items
    return {item['id']: item for item in iter_items(output) if item.get('id')}


def rows(item):
    names = [c['name'] for c in item['columns']]
    return [dict(zip(names, row)) for row in item['rows']]


def numbers_in(text):
    return [float(v.replace(',', '')) for v in re.findall(r'[-+]?\d[\d,]*\.?\d*', text)]


def build(output, cfg):
    """All findings for a run, sorted by severity then score, with stable IDs F01, F02, …"""
    items = items_by_id(output); found = []
    target, positive, kind = output.get('target'), output.get('positive'), output.get('target_type')

    def add(type_, severity, statement, evidence, fields=(), caveats=(), score=0.0):
        found.append(Finding(type_, severity, statement, evidence, list(fields), numbers_in(statement), list(caveats), float(score)))

    for suspect in output.get('leakage', []):
        add('leakage_suspect', 'critical', f'{suspect["field"]} is a leakage suspect: ' + '; '.join(suspect['reasons']) + '.', 'leakage-suspects',
            [suspect['field']], ['Excluded from the multivariate model and segments.'], 1.0)
    multivariate = output.get('multivariate') or {}
    if multivariate.get('no_signal') and multivariate.get('metrics'):
        head = multivariate['metrics'][0]
        add('no_signal', 'important', f'The model does not beat the baseline meaningfully: {head[0]} {head[1]:.3f} vs {head[3]:.3f} '
            f'(gain {multivariate["signal"]:.3f}, below {cfg.signal_minimum}).', 'multivariate-metrics', [], ['Importance and adjusted effects are not reported.'], 0.9)
    rows_total = output.get('rows') or 1
    overview = {r['Measure']: r['Value'] for r in rows(items['quality-overview'])} if 'quality-overview' in items else {}
    duplicates = overview.get('Exact duplicate rows (beyond the first copy)') or 0
    if duplicates:
        add('data_quality_issue', 'important' if duplicates / rows_total >= 0.01 else 'informative',
            f'{duplicates:,} rows ({pct(duplicates / rows_total)}) are exact duplicates of another row; they were kept.', 'quality-duplicates',
            [], ['Duplicates count twice in every rate unless removed.'], duplicates / rows_total)
    if 'quality-columns' in items:
        for r in rows(items['quality-columns']):
            share = r.get('Missing share') or 0
            if share >= cfg.finding_missing_share:
                add('data_quality_issue', 'important', f'{r["Column"]} has no usable value in {pct(share)} of rows.', 'quality-missing', [r['Column']], [], share)
    if 'corrections-applied' in items:
        for r in rows(items['corrections-applied']):
            if r.get('Conversion failures'):
                add('data_quality_issue', 'informative', f'{r["Column"]}: {r["Conversion failures"]:,} values could not be {r["Change"].replace("converted to ", "read as ")} '
                    'and were set to missing (kept in the error ledger).', 'corrections-applied', [r['Column']], [], r['Conversion failures'] / rows_total)
    for e in output.get('missing_explanations', []):
        add('deterministic_missingness', 'important', e['statement'], 'missing-explanations', [e['column'], e['condition_column']], [], e['coverage'])
    structure = output.get('structure') or {}
    for c in structure.get('conditional', []):
        add('conditional_category', 'informative', f'{c["column"]} has the level "{c["level"]}" exactly when {c["parent"]} = {c["parent_value"]} '
            f'({c["rows"]:,} rows); its comparison with the target uses the remaining rows.', 'structure-conditional', [c['column'], c['parent']], [], 0.5)
    for d in structure.get('derived', []):
        add('derived_column', 'informative', f'{d["column"]} ≈ {d["factor"]:.4g} × ({d["formula"]}), R² {d["r2_full"]:.3f} on {d["rows"]:,} rows; '
            'it is not ranked separately.', 'structure-derived', [d['column'], *d['sources']], [], d['r2_full'])
    for c in structure.get('clusters', []):
        add('redundancy_cluster', 'informative', f'{", ".join(c["members"])} carry overlapping information (association ≥ {cfg.redundancy_threshold}); '
            f'{c["representative"]} represents them.', 'structure-clusters', c['members'], [], 0.4)
    suspects = {s['field'] for s in output.get('leakage', [])}
    ranking = output.get('ranking', [])
    # The rank test misses non-monotone shapes; the permutation test on mutual information does not. Either, after
    # Benjamini–Hochberg adjustment across fields, counts as evidence of an association.
    permuted = [e.get('nmi_p') for e in ranking]
    known = [i for i, v in enumerate(permuted) if v is not None]
    nmi_q = dict(zip(known, benjamini_hochberg([permuted[i] for i in known]))) if known else {}
    interacting = {f for i in output.get('interactions', []) if i['reported'] for f in (i['first'], i['second'])}
    for index, e in enumerate(ranking):
        if e['field'] in suspects: continue
        power = e.get('power'); q = e.get('adjusted_p'); q_nmi = nmi_q.get(index)
        if power is None: continue
        rank_ok = q is not None and q < 0.05; nmi_ok = q_nmi is not None and q_nmi < 0.05
        if (rank_ok or nmi_ok) and power >= (cfg.finding_power_auc if e.get('power_metric') == 'ΔAUC' else cfg.finding_power_r2):
            statement, evidence = association_statement(items, e, target, positive, kind)
            caveats = ['Association, not cause.'] + ([f'Measured on rows where {e["subset"]}.'] if e.get('subset') not in (None, 'all rows') else [])
            if not rank_ok: caveats.append(f'The rank test does not detect it (adjusted p {q:.2f}); the relationship is not monotone '
                                           f'(permutation test adjusted p {q_nmi:.2g}).' if q is not None else 'Detected by the permutation test only.')
            add('strong_association', 'important', statement, evidence, [e['field']], caveats, power)
        elif not rank_ok and not nmi_ok and abs(power) < cfg.null_power:
            caveats = [f'It takes part in a reported interaction: its association with {target} depends on another field.'] if e['field'] in interacting else []
            add('null_result', 'informative', f'{e["field"]} shows no association with {target}: {e.get("power_metric")} {power:+.3f}, '
                f'adjusted p {q:.2f}.' if q is not None else f'{e["field"]} shows no association with {target}.', 'ranking', [e['field']], caveats, 1 - abs(power))
    for v in multivariate.get('verdicts', []):
        name, uni, adj, df, kept, p_adj, s_uni, s_adj, verdict, note = v
        if verdict in ('holds', 'no univariate signal') or uni is None: continue
        if verdict.startswith(('left out', 'represented', 'not in')): continue
        severity = 'important' if verdict in ('reverses', 'disappears') else 'informative'
        add('adjusted_effect_change', severity, f'{name}: after adjusting for the other fields, {pct(kept or 0)} of its effect remains '
            f'(likelihood-ratio χ² {uni:,.1f} alone vs {adj:,.1f} adjusted); verdict: {verdict}.', 'multivariate-verdicts', [name],
            ['Holding the other listed fields constant.'] + ([note[0].upper() + note[1:] + '.'] if note else []), 1 - (kept or 0))
    for c in output.get('confounding', []):
        if not c['flag']: continue
        measure = 'odds ratio' if c['measure'] == 'odds ratio' else 'difference'
        add('confounding_or_simpson', 'important' if c['flag'] == 'reverses' else 'informative',
            f'{c["comparison"]}: crude {measure} {num(c["crude"])} vs {num(c["stratified"])} {ci(c["low"], c["high"], False)} within levels of '
            f'{c["stratifier"]}; the effect ' + ('reverses direction.' if c['flag'] == 'reverses' else 'drops by more than half.'),
            'confounding-checks', [c['field'], c['stratifier']], ['Stratified comparison; associations, not causes.'], 1.0 if c['flag'] == 'reverses' else 0.5)
    for i in output.get('interactions', []):
        if not i['reported']: continue
        add('interaction', 'important', f'{i["first"]} and {i["second"]} are associated with {target} jointly beyond their separate effects: '
            f'cross-validated gain {i["gain"]:.3f} (adjusted p {i["adjusted_p"]:.2g}).', 'interactions', [i['first'], i['second']], [], i['gain'])
    overall = output.get('segment_overall')
    for s in output.get('segments', []):
        rate = pct(s['rate']) if kind == 'binary' else num(s['rate'])
        base = (f' vs {pct(overall)} overall' if kind == 'binary' else f' vs {num(overall)} overall') if overall is not None else ''
        interval = ci(s['low'], s['high'], kind == 'binary') if s.get('low') is not None else ''
        share = f', {pct(s["share_of_target"])} of all positives' if kind == 'binary' and s.get('share_of_target') is not None else ''
        add('high_impact_segment', 'important', f'Rows with {s["rule"]} have a {"positive rate" if kind == "binary" else "mean " + target} of {rate} '
            f'{interval}{base} (lift {s["lift"]:.2f}); {s["rows"]:,} rows ({pct(s["share_of_rows"])}){share}. Held-out lift {s["held_out_lift"]:.2f}.',
            'segments-higher' if s['direction'] == 'higher' else 'segments-lower', list(dict.fromkeys(c[0] if isinstance(c[0], str) else c[0][0] for c in s['conditions'])),
            ['Rule found on 80% of the model sample and checked on the other 20%.'], s.get('impact', 0) / rows_total)
    for n in output.get('nonlinear', []):
        add('nonlinear_shape', 'informative', f'{n["field"]} has a non-linear relationship with {target}: a monotone curve explains '
            f'{pct(n["monotone_share"])} of the variation between bins ({n["detail"]}).', f'{slug(n["field"])}-bins' if kind == 'binary' else f'{slug(n["field"])}-medians',
            [n['field']], [], 1 - n['monotone_share'])
    time_findings(output, cfg, add)
    unsupervised_findings(output, add)
    found = interleave(found)
    for index, finding in enumerate(found, 1): finding.id = f'F{index:02d}'
    return found


# Within each severity, types take turns in this order, so the first screen covers what predicts the target,
# which segments matter and what to be careful about, instead of many findings of one type.
TYPE_PRIORITY = ['leakage_suspect', 'no_signal', 'strong_association', 'high_impact_segment', 'confounding_or_simpson', 'interaction',
                 'cluster_structure', 'drift', 'trend', 'break', 'seasonality', 'unusual_rows', 'distribution_shape', 'deterministic_missingness', 'data_quality_issue', 'adjusted_effect_change', 'nonlinear_shape', 'null_result',
                 'derived_column', 'redundancy_cluster', 'conditional_category']


def interleave(found):
    ordered = []
    for severity in sorted({f.severity for f in found}, key=SEVERITY_ORDER.get):
        queues = {t: sorted([f for f in found if f.severity == severity and f.type == t], key=lambda f: (-f.score, f.statement)) for t in TYPE_PRIORITY}
        while any(queues.values()):
            for t in TYPE_PRIORITY:
                if queues[t]: ordered.append(queues[t].pop(0))
    return ordered


def slug(name):
    return re.sub(r'[^A-Za-z0-9]+', '-', name).strip('-').lower()[:40]


def association_statement(items, e, target, positive, kind):
    field_ = e['field']; key = slug(field_)
    suffix = '-applicable' if e.get('subset') not in (None, 'all rows') else ''
    rates = items.get(f'{key}-rates{suffix}')
    if kind == 'binary' and rates:
        groups = [r for r in rows(rates) if not str(r['Category']).startswith('(') and r['Rows'] >= 30]
        if groups:
            top = max(groups, key=lambda r: (r['Difference vs overall (pp)'], r['Category']))
            overall = top['Positive rate'] / top['Lift'] if top['Lift'] else None
            return (f'Rows with {field_} = {top["Category"]} have a positive rate of {pct(top["Positive rate"])} '
                    f'{ci(top["95% CI low"], top["95% CI high"])} vs {pct(overall)} overall ({signed_pp(top["Difference vs overall (pp)"])}, '
                    f'lift {top["Lift"]:.2f}); {top["Rows"]:,} rows, {pct(top["Share of all positives"])} of all positives.', f'{key}-rates{suffix}')
    bins = items.get(f'{key}-bins{suffix}')
    if kind == 'binary' and bins:
        groups = [r for r in rows(bins) if r['Rows'] >= 30]
        if groups:
            top = max(groups, key=lambda r: (r['Difference vs overall (pp)'], -r['Bin']))
            overall = top['Positive rate'] / top['Lift'] if top['Lift'] else None
            return (f'Rows with {field_} from {num(top["Lowest value"])} to {num(top["Highest value"])} have a positive rate of '
                    f'{pct(top["Positive rate"])} {ci(top["95% CI low"], top["95% CI high"])} vs {pct(overall)} overall '
                    f'({signed_pp(top["Difference vs overall (pp)"])}, lift {top["Lift"]:.2f}); {top["Rows"]:,} rows.', f'{key}-bins{suffix}')
    test = items.get(f'{key}-test{suffix}')
    if test and rows(test):
        r = rows(test)[0]
        return (f'{field_} is associated with {target}: {e.get("power_metric")} {e["power"]:.3f}, {r["Test"]} effect size {r["Effect size"]:.3f} '
                f'(adjusted p {e["adjusted_p"]:.2g}, n {r["n"]:,}).', f'{key}-test{suffix}')
    return f'{field_} is associated with {target}: {e.get("power_metric")} {e["power"]:.3f}.', 'ranking'


def top(findings, count):
    return findings[:count]


def in_unit(value, unit):
    """Rates in percentage points, everything else as a number."""
    return f'{value * 100:.2f} pp' if unit == 'rate' else num(value)


def time_findings(output, cfg, add):
    """Trend, seasonality, break and drift findings from the time analysis."""
    time = output.get('time') or {}
    target = output.get('target')
    for s in time.get('series', []):
        is_target = s['unit'] != 'rows'; severity = 'important' if is_target else 'informative'
        grain = s.get('grain') or time.get('grain', 'period'); fields = [target] if is_target and target else []
        t = s.get('trend')
        if t and t['p'] < 0.05 and (t['low'] > 0 or t['high'] < 0) and t.get('relative_change') is not None \
                and abs(t['relative_change']) >= cfg.trend_min_change:
            change = f'{t["change"] * 100:+.1f} pp' if s['unit'] == 'rate' else f'{t["change"]:+,.3g}'
            add('trend', severity, f'{s["name"]} {"rises" if t["slope"] > 0 else "falls"} by {in_unit(abs(t["slope"]), s["unit"])} per {grain} '
                f'(Theil–Sen, 95% CI {in_unit(t["low"], s["unit"])} to {in_unit(t["high"], s["unit"])}): {change} over {t["periods"]} {grain}s from '
                f'{t["first"]} to {t["last"]}, {t["relative_change"] * 100:+.0f}% of the mean level; Mann–Kendall p {t["p"]:.2g}.', 'time-trend', fields,
                ['Describes the analysed range only.'] + (['Tested after removing the seasonal pattern.'] if t.get('deseasonalised') else [])
                + ([f'The series also has a structural break ({", ".join(b["label"] for b in s["breaks"])}); part of the change is that shift.']
                   if s.get('breaks') else []),
                abs(t['relative_change']))
        for f in s.get('seasonality', []):
            if f['p'] > cfg.seasonal_p_maximum or f['strength'] < cfg.seasonal_strength_minimum: continue
            add('seasonality', severity, f'{s["name"]} varies with the {f["meaning"]}: seasonal strength {f["strength"]:.2f} (STL, permutation p {f["p"]:.2f}); '
                f'highest around {f["peak_label"]}, lowest around {f["trough_label"]}, a peak-to-trough difference of {in_unit(f["amplitude"], s["unit"])}.',
                'time-seasonality', fields, [f'Estimated from {len(s["used"])} complete {grain}s.'], f['strength'])
        for b in s.get('breaks', []):
            add('break', severity, f'{s["name"]} shifts at {b["label"]}: {in_unit(b["level_before"], s["unit"])} just before vs '
                f'{in_unit(b["level_after"], s["unit"])} just after (jump {b["jump_sd"]:+.1f} noise sd); mean {in_unit(b["mean_before"], s["unit"])} before vs '
                f'{in_unit(b["mean_after"], s["unit"])} after.', 'time-breaks', fields, ['The break date is accurate to about one period.'],
                min(1.0, abs(b['jump_sd']) / 10))
    for d in time.get('drift', []):
        if d['label'] != 'major shift': continue
        add('drift', 'important', f'The distribution of {d["column"]} differs between the first and last {cfg.psi_window_share:.0%} of the time range: '
            f'PSI {d["psi"]:.2f} (above {cfg.psi_major}); largest change {d["largest"]}.', 'time-drift', [d['column']],
            ['Older rows may describe the present less well.'] + (['This is the target.'] if d.get('is_target') else []), min(1.0, d['psi']))


def unsupervised_findings(output, add):
    """Cluster structure (or its absence), unusual rows and multimodal fields from the no-target mode."""
    u = output.get('unsupervised') or {}
    c = u.get('clusters')
    if c and c.get('stable'):
        chosen = next(row for row in c['candidates'] if row[0] == c['k'])
        sizes = ', '.join(f'{n:,}' for _, n, _ in c['sizes'])
        top = max(c['profiles'], key=lambda r: abs(r[3])) if c['profiles'] else None
        add('cluster_structure', 'important', f'The sampled rows form {c["k"]} clusters ({c["method"]}; silhouette {chosen[1]:.2f}, bootstrap ARI '
            f'{chosen[2]:.2f}) of {sizes} rows' + (f'; the largest difference: cluster {top[0]}, {top[2]}.' if top else '.'), 'clusters-sizes',
            c['fields'], [f'Clusters describe similarity in the {len(c["fields"])} fields used; they are not labels found in the data.'], chosen[1])
    elif c:
        best = max((row[1] for row in c['candidates']), default=None)
        add('cluster_structure', 'informative', 'No stable cluster structure: ' + (f'the best mean silhouette is {best:.2f}' if best is not None else 'no k could be fitted')
            + f' for k from {min(r[0] for r in c["candidates"]) if c["candidates"] else 2} to {max(r[0] for r in c["candidates"]) if c["candidates"] else 8}.',
            'clusters-candidates', c['fields'], [], 0.3)
    a = u.get('anomalies') or {}
    if a.get('flagged') and a.get('rows'):
        first = a['rows'][0]
        add('unusual_rows', 'informative', f'{a["flagged"]:,} of {u["sample_rows"]:,} sampled rows have the highest isolation scores; the most unusual, row '
            f'{first[0]}, has {first[2]}.', 'unusual-rows', [], ['Reported only; no rows were removed.'], 0.2)
    for s in u.get('shapes', []):
        if 'more than one mode' in s['labels']:
            add('distribution_shape', 'informative', f'{s["field"]} has more than one mode: dip {s["dip"]:.3f}, dip test p {s["dip_p"]:.3f} on {s["dip_rows"]:,} values.',
                'distribution-shapes', [s['field']], ['Averages of this field describe a mixture of groups.'], s['dip'])
