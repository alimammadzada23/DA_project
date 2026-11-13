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
    'high_impact_segment': 'Segment', 'nonlinear_shape': 'Non-linear shape', 'null_result': 'No association'}


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
    found = interleave(found)
    for index, finding in enumerate(found, 1): finding.id = f'F{index:02d}'
    return found


# Within each severity, types take turns in this order, so the first screen covers what predicts the target,
# which segments matter and what to be careful about, instead of many findings of one type.
TYPE_PRIORITY = ['leakage_suspect', 'no_signal', 'strong_association', 'high_impact_segment', 'confounding_or_simpson', 'interaction',
                 'deterministic_missingness', 'data_quality_issue', 'adjusted_effect_change', 'nonlinear_shape', 'null_result',
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
