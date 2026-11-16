"""Data-quality checks of the analyst engine: deterministic explanations of missing values."""
from dataclasses import asdict, dataclass

from ....datasets import quote
from ...store import literal


@dataclass
class MissingExplanation:
    column: str
    missing_rows: int
    condition_column: str
    condition_value: str          # '(missing)' when the condition is that the other column is missing too
    condition_rows: int           # rows meeting the condition in the whole table
    both_rows: int                # missing rows that meet the condition
    coverage: float               # P(condition | missing)
    lift: float                   # P(condition | missing) / P(condition)
    precision: float              # P(missing | condition)
    equivalent: bool              # missing ⇔ condition (both ≥ 99%)

    def statement(self):
        condition = f'{self.condition_column} is missing' if self.condition_value == '(missing)' else f'{self.condition_column} = {self.condition_value}'
        text = (f'{self.both_rows:,} of {self.missing_rows:,} rows with {self.column} missing have {condition} '
                f'({self.coverage:.1%} of missing rows; lift {self.lift:,.1f})')
        if self.equivalent:
            text += f', and these are all {self.condition_rows:,} rows with {condition}'
        else:
            text += f'; {self.precision:.1%} of the {self.condition_rows:,} rows with {condition} have {self.column} missing'
        return text + '.'

    def to_dict(self):
        return asdict(self) | {'statement': self.statement()}


def explain_missing(a, facts, cfg, missing_sql, display_value):
    """Conditions C = v (or C missing) that explain the missing values of a column.

    Reported only when coverage P(C=v | missing) ≥ cfg.missing_explanation_coverage, lift
    P(C=v | missing) / P(C=v) ≥ cfg.missing_explanation_lift and precision
    P(missing | C=v) ≥ cfg.missing_explanation_precision. The precision rule removes
    coincidences: a common value that merely also appears in the missing rows.
    """
    rows = None; output = []; queries = []
    with_missing = sorted([(f['missing'], n) for n, f in facts.items() if f['missing']], key=lambda t: (-t[0], t[1]))
    for missing, column in with_missing[:cfg.missing_explanation_columns]:
        others = [o for o, f in facts.items() if o != column and f['type'] not in ('empty', 'constant', 'identifier', 'text')]
        if not others: continue
        value = lambda o: f'trim(CAST({quote(o)} AS VARCHAR))'
        modes = a.run('SELECT count(*) AS rows_,' + ','.join(f'mode({value(o)}) AS "m{i}",count({quote(o)}) AS "n{i}"' for i, o in enumerate(others))
                      + f' FROM selected WHERE {missing_sql(column)}', f'Most common values where {column} is missing')
        queries.append(modes)
        found = dict(zip(modes['columns'], modes['rows'][0])); missing = found['rows_']
        conditions = []
        for i, other in enumerate(others):
            co_missing = missing - found[f'n{i}']
            if co_missing / missing >= cfg.missing_explanation_coverage:
                conditions.append((other, '(missing)', f'{quote(other)} IS NULL'))
            elif found[f'm{i}'] is not None:
                conditions.append((other, found[f'm{i}'], f'{value(other)}={literal(found[f"m{i}"])}'))
        if not conditions: continue
        counts = a.run('SELECT count(*) AS total,' + ','.join(
            f'count(*) FILTER (WHERE {sql}) AS "c{i}",count(*) FILTER (WHERE {sql} AND {missing_sql(column)}) AS "b{i}"'
            for i, (_, _, sql) in enumerate(conditions)) + ' FROM selected', f'Rows meeting each candidate condition for {column}')
        queries.append(counts)
        found = dict(zip(counts['columns'], counts['rows'][0])); rows = found['total']
        for i, (other, shown, _) in enumerate(conditions):
            condition_rows, both = found[f'c{i}'], found[f'b{i}']
            if not condition_rows: continue
            coverage = both / missing; share = condition_rows / rows; precision = both / condition_rows
            lift = coverage / share
            if coverage >= cfg.missing_explanation_coverage and lift >= cfg.missing_explanation_lift and precision >= cfg.missing_explanation_precision:
                output.append(MissingExplanation(column, missing, other, shown if shown == '(missing)' else display_value(shown, facts[other]),
                                                 condition_rows, both, coverage, lift, precision, coverage >= 0.99 and precision >= 0.99))
    output.sort(key=lambda e: (e.column, -e.precision, -e.coverage, e.condition_column))
    return output, queries
