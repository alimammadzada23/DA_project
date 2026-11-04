"""A small ranked set of evidence-backed descriptive findings, without causal claims."""
from dataclasses import dataclass
from datetime import timedelta

from .engine import Engine, Filter
from .metrics import Metric
from .profile import date_expression


@dataclass(frozen=True)
class DiscoveryConfig:
    minimum_concentration: float = .4
    minimum_missing_share: float = .1
    minimum_relative_change: float = .15
    minimum_period_rows: int = 20
    maximum_findings: int = 5


def discover(engine,dimension=None,metric=None,date_column=None,config=DiscoveryConfig()):
    if not 0<=config.minimum_concentration<=1 or not 0<=config.minimum_missing_share<=1 or not 1<=config.maximum_findings<=20:
        raise ValueError('Invalid insight-discovery thresholds.')
    counts=','.join(f"count(*) FILTER (WHERE {engine.column(c)} IS NULL OR trim(CAST({engine.column(c)} AS VARCHAR))='') AS m{i}"
                    for i,c in enumerate(engine.info['columns']))
    coverage=engine.run('SELECT count(*) AS total,'+counts+' FROM selected')
    total,*missing_values=coverage['rows'][0]
    findings=[]
    if not total: return findings
    metric=metric or Metric('Records')
    if dimension:
        evidence=engine.groups(dimension,Metric('Records'),top=1)
        if evidence['rows']:
            category,value,records=evidence['rows'][0]
            share=records/total
            if share>=config.minimum_concentration:
                findings.append({'finding':f'{category if category is not None else "Missing category"} represents {share:.1%} of selected rows.',
                    'magnitude':share,'context':f'{records:,} of {total:,} selected rows.','evidence':evidence,
                    'limitations':'Concentration describes this selection; it does not establish risk, quality or customer exposure.',
                    'investigation':'Inspect the segment records and compare a compatible complete period.',
                    'possible_action':'Review whether this concentration warrants operational monitoring; effectiveness is not demonstrated.'})
    for column,missing in zip(engine.info['columns'],missing_values):
        if missing/total>=config.minimum_missing_share:
            findings.append({'finding':f'{column}: {missing/total:.1%} of selected rows are missing.',
                'magnitude':missing/total,'context':f'{missing:,} of {total:,} selected rows.','evidence':coverage,
                'limitations':'Missingness can reflect legitimate inapplicability or collection problems; its cause is unknown.',
                'investigation':'Compare missing-value coverage across time and segments before interpreting this field.',
                'possible_action':'Confirm collection rules and disclose unavailable inputs in downstream metrics.'})
    if date_column and metric.additive:
        engine.column(date_column); dt=date_expression(date_column)
        bounds=engine.run(f'SELECT min({dt}) AS first_date,max({dt}) AS last_date FROM selected')['rows'][0]
        if bounds[0] and bounds[1] and (bounds[1]-bounds[0]).days>=56:
            end=bounds[1]-timedelta(days=1)
            earlier=(end-timedelta(days=55),end-timedelta(days=28))
            later=(end-timedelta(days=27),end)
            results=[]
            for start,finish in (earlier,later):
                scoped=Engine(engine.workspace,engine.table,[*engine.filters,Filter(date_column,'from',start),Filter(date_column,'through',finish)],version=engine.info['current'])
                scoped.info['roles']=engine.info['roles']
                results.append(scoped.metric(metric))
            before,after=[r['rows'][0][0] for r in results]
            n_before,n_after=[r['rows'][0][1] for r in results]
            if before is not None and after is not None and before>0 and min(n_before,n_after)>=config.minimum_period_rows:
                change=(after-before)/before
                if abs(change)>=config.minimum_relative_change:
                    findings.append({'finding':f'{metric.name} changed by {change:+.1%} across two equal 28-day windows.',
                        'magnitude':abs(change),'context':f'{before:,.4g} → {after:,.4g} {metric.units}; {earlier[0]}–{earlier[1]} vs {later[0]}–{later[1]}.',
                        'evidence':{'earlier':results[0],'later':results[1]},
                        'limitations':'Descriptive screening without a significance claim. Equal windows do not establish source completeness or adjust for seasonality, exposure or composition. The last observed day is excluded.',
                        'investigation':'Check reporting coverage and decompose the change across compatible segments in Investigation Lab.',
                        'possible_action':'Review the evidence with the data owner before changing operations; no causal effect or effective intervention is established.'})
    return sorted(findings,key=lambda f:f['magnitude'],reverse=True)[:config.maximum_findings]
