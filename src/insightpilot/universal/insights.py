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
    if dimension and metric.formula!='count()':
        result=engine.groups(dimension,metric,top=100)
        eligible=[r for r in result['rows'] if r[1] is not None and r[2]>=5]
        if len(eligible)>=2:
            high,low=max(eligible,key=lambda r:r[1]),min(eligible,key=lambda r:r[1])
            if high[1]!=low[1]:
                findings.append({'finding':f'{metric.name} differs by {high[1]-low[1]:,.5g} {metric.units} between {high[0]} and {low[0]}.',
                    'magnitude':.9,'context':f'{high[1]:,.5g} versus {low[1]:,.5g}; {high[2]:,} and {low[2]:,} source rows. Up to 100 groups screened; groups need at least five rows.',
                    'evidence':result,'limitations':'Descriptive comparison, not a significance or causal claim. Additive totals also depend on group size. Rates use the metric’s explicit eligible denominator, not the displayed source-row count.',
                    'investigation':'Inspect supporting records and eligible inputs, then use a suitable group comparison in Advanced.',
                    'possible_action':'Check whether the observed difference matters for the stated objective before proposing an intervention.'})
    _,required=metric.compile(engine.info['columns'],engine.info['roles'])
    for column in required[:3]:
        if engine.info['roles'].get(column) not in ('measure','quantity','duration','price','revenue','expense'): continue
        summary=engine.numeric_summary(column); row=dict(zip(summary['columns'],summary['rows'][0]))
        if row['valid']>=8 and row['q1'] is not None and row['q3']>row['q1']:
            findings.append({'finding':f'{column}: the middle half of finite values lies between {row["q1"]:,.5g} and {row["q3"]:,.5g}.',
                'magnitude':.6,'context':f'Median {row["median"]:,.5g}; {row["valid"]:,} eligible of {total:,} selected rows. Units: '+engine.info.get('semantics',{}).get('units',{}).get(column,'unconfirmed'),
                'evidence':summary,'limitations':'An observed distribution summary; skew, selection, repeated entities and missingness can limit comparisons.',
                'investigation':'Inspect the distribution and compare compatible segments rather than relying on the mean alone.',
                'possible_action':'Use the observed range to frame a review question; no target or threshold is implied.'})
    if dimension and metric.formula=='count()':
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
    if date_column:
        trend=engine.trend(date_column,metric,engine.context.time_grain)
        periods=[r for r in trend['rows'] if r[1] is not None and r[2]>=5]
        if len(periods)>=3:
            peak=max(periods,key=lambda r:r[1]);low=min(periods,key=lambda r:r[1])
            if peak[1]!=low[1]:
                findings.append({'finding':f'{metric.name} ranges from {low[1]:,.5g} to {peak[1]:,.5g} {metric.units} across observed {engine.context.time_grain} periods.',
                    'magnitude':.7,'context':f'Lowest: {low[0]}; highest: {peak[0]}. Date meaning: '+engine.info.get('semantics',{}).get('date_purposes',{}).get(date_column,'unconfirmed'),
                    'evidence':trend,'limitations':'Extremes are selected after screening, not a statistical test. Edge periods, seasonality, missing inputs and unequal exposure can explain differences. At least five source rows per displayed eligible period.',
                    'investigation':'Compare complete, equally defined windows and inspect source coverage in Advanced before interpreting change.',
                    'possible_action':'Use the observed periods to prioritize a data review; no trend persistence or intervention effect is implied.'})
    if date_column and metric.additive:
        engine.column(date_column); dt=date_expression(date_column)
        bounds=engine.run(f'SELECT min({dt}) AS first_date,max({dt}) AS last_date FROM selected')['rows'][0]
        if bounds[0] and bounds[1] and (bounds[1]-bounds[0]).days>=56:
            end=bounds[1]-timedelta(days=1)
            earlier=(end-timedelta(days=55),end-timedelta(days=28))
            later=(end-timedelta(days=27),end)
            results=[]
            for start,finish in (earlier,later):
                scoped=Engine(engine.workspace,engine.table,[*engine.filters,Filter(date_column,'from',start),Filter(date_column,'through',finish)],version=engine.info['current'],context=engine.context)
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
