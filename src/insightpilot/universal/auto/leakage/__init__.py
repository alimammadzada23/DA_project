"""Leakage screen: fields that predict the target too well, mirror it exactly, or look recorded after the outcome."""
from dataclasses import asdict, dataclass, field
import re

from ....datasets import quote

# Weak, disclosed hints that a date may be recorded after the outcome. They never decide anything alone
# except flagging the field for review, as the specification requires.
POST_OUTCOME_HINTS = ('close', 'closed', 'cancel', 'cancelled', 'canceled', 'end', 'ended', 'resolved', 'resolution', 'termination',
                      'terminated', 'exit', 'churn', 'completed', 'completion', 'outcome', 'result')


@dataclass
class LeakageSuspect:
    field: str
    reasons: list = field(default_factory=list)
    power_metric: str = ''
    power: float | None = None

    def to_dict(self):
        return asdict(self)


def name_hints(name):
    words = re.sub(r'[^a-z0-9]+', ' ', re.sub(r'([a-z])([A-Z])', r'\1 \2', name).lower()).split()
    return [hint for hint in POST_OUTCOME_HINTS if hint in words]


def screen(a, spec, powers, facts, cfg, absent_sql, text_value, time_axis=None):
    """Flag suspects by (1) single-field power, (2) exact dependency with the target, (3) date-name hints and
    (4) with a time axis, dates that fall after it on almost every row (values known only later)."""
    suspects = {}; queries = []

    def flag(name, reason):
        suspects.setdefault(name, LeakageSuspect(name)).reasons.append(reason)
    threshold = cfg.leakage_auc - 0.5 if spec.kind != 'numeric' else cfg.leakage_r2
    for name, power in powers.items():
        if power.get('value') is not None and power['value'] >= threshold:
            shown = f'AUC {power["value"] + 0.5:.3f}' if power['metric'] == 'ΔAUC' else f'R² {power["value"]:.3f}'
            flag(name, f'{shown} from this field alone (threshold {"AUC " + str(cfg.leakage_auc) if spec.kind != "numeric" else "R² " + str(cfg.leakage_r2)})')
    target = spec.label if spec.kind != 'numeric' else f'CAST({spec.value} AS VARCHAR)'
    for name, f in facts.items():
        if name == spec.column or f['type'] in ('identifier', 'text', 'empty', 'constant'): continue
        value = text_value(name)
        query = a.run(f'''SELECT
            (SELECT count(*) FILTER (WHERE d=1) FROM (SELECT count(DISTINCT t) AS d,count(*) AS n FROM (SELECT {value} AS v,{target} AS t FROM selected
                WHERE {spec.ok} AND NOT {absent_sql(name)}) GROUP BY v) WHERE n>=2) AS field_groups_consistent,
            (SELECT count(*) FROM (SELECT count(*) AS n FROM (SELECT {value} AS v FROM selected WHERE {spec.ok} AND NOT {absent_sql(name)}) GROUP BY v) WHERE n>=2) AS field_groups,
            (SELECT count(*) FILTER (WHERE d=1) FROM (SELECT count(DISTINCT v) AS d FROM (SELECT {value} AS v,{target} AS t FROM selected
                WHERE {spec.ok} AND NOT {absent_sql(name)}) GROUP BY t)) AS target_groups_consistent,
            (SELECT count(DISTINCT {target}) FROM selected WHERE {spec.ok} AND NOT {absent_sql(name)}) AS target_groups''',
            f'Exact dependency between {name} and the target')
        queries.append(query)
        consistent, groups, by_target, classes = query['rows'][0]
        rows_share = f['clean'] / max(1, f['present'] + f['missing'])   # share of rows with a usable value
        if groups >= 20 and consistent / groups >= cfg.dependency_overlap and f['distinct_clean'] <= 0.5 * max(f['clean'], 1) and rows_share >= 0.5:
            flag(name, f'determines the target in {consistent / groups:.1%} of its {groups:,} repeated values')
        if classes and spec.kind != 'numeric' and by_target == classes and f['distinct_clean'] <= classes:
            flag(name, 'takes a single value within each target class')
        if f['type'] == 'date' and name_hints(name):
            flag(name, 'date whose name suggests it is recorded after the outcome (' + ', '.join(repr(h) for h in name_hints(name)) + '; a name hint only)')
    if time_axis:
        from ...profile import date_expression
        axis = date_expression(time_axis)
        for name, f in facts.items():
            if name in (spec.column, time_axis) or f['type'] != 'date': continue
            query = a.run(f'''SELECT count(*) FILTER (WHERE d > t) AS later,count(*) AS n FROM (SELECT {date_expression(name)} AS d,{axis} AS t
                FROM selected) WHERE d IS NOT NULL AND t IS NOT NULL''', f'Order of {name} and the time axis {time_axis}')
            queries.append(query)
            later, n = query['rows'][0]
            if n >= 20 and later / n >= cfg.temporal_leakage_share:
                flag(name, f'later than the time axis {time_axis} on {later / n:.1%} of {n:,} rows with both dates (known only after it)')
    for name, suspect in suspects.items():
        power = powers.get(name) or {}
        suspect.power_metric, suspect.power = power.get('metric', ''), power.get('value')
    return [suspects[n] for n in sorted(suspects, key=lambda n: list(facts).index(n) if n in facts else 0)], queries
