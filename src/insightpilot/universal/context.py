"""Reviewed, portable analytical intent shared by every product page."""
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AnalysisContext:
    date_basis: str | None = None
    time_grain: str = 'month'
    comparison: str | None = None
    objective: str = 'Describe observed records'

    def validate(self, info):
        if self.time_grain not in ('day','week','month','quarter','year'):
            raise ValueError('Unsupported calendar grain.')
        semantics=info.get('semantics', {})
        if self.date_basis is not None:
            if not semantics.get('reviewed') or info['roles'].get(self.date_basis)!='date':
                raise ValueError('Review the selected date field before using time analysis.')
            if not semantics.get('date_purposes', {}).get(self.date_basis):
                raise ValueError('The date basis needs a reviewed purpose.')
        if self.comparison and self.comparison not in info['columns']:
            raise ValueError('Unavailable comparison field.')
        return self

    def dictionary(self): return asdict(self)


def coverage(info, report):
    semantics=info.get('semantics', {})
    approved=info['roles']
    dates=[c['column'] for c in report['columns'] if c['recognized_dates']]
    return {'reviewed':bool(semantics.get('reviewed')), 'date_candidates':dates,
        'date_ambiguity':len(dates)>1 and not semantics.get('date_basis'),
        'approved_measures':[c for c,r in approved.items() if r in ('measure','quantity','duration','price','revenue','expense')],
        'approved_dimensions':[c for c,r in approved.items() if r in ('category','status','geography','company','product')],
        'notice':'Multiple timestamps represent different events. Choose and describe a date basis; none is selected from column order.' if len(dates)>1 else
                 'Dates, units, identifiers and row grain require review before business interpretation.'}
