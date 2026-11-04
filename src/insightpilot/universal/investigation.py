"""Exact descriptive decomposition of proportion changes into rate and mix terms."""
from dataclasses import asdict
from datetime import date
import math

import pandas as pd

from .engine import frame_of
from .profile import date_expression


def proportion_change(engine,dimension,date_column,numerator,denominator,earlier,later):
    if not numerator.additive or not denominator.additive:
        raise ValueError('Provide separately additive numerator and denominator metrics, not an already divided rate.')
    group=engine.column(dimension); engine.column(date_column)
    a,b,c,d=[date.fromisoformat(str(value)) for value in [*earlier,*later]]
    if not a<=b<c<=d or b-a!=d-c: raise ValueError('Choose equally long, nonoverlapping inclusive windows, earlier first.')
    num,_=numerator.compile(engine.info['columns'],engine.info['roles'])
    den,_=denominator.compile(engine.info['columns'],engine.info['roles'])
    dt=date_expression(date_column)
    evidence=engine.run(f'''SELECT {group} AS category,0 AS period_index,{num} AS numerator,{den} AS denominator
        FROM selected WHERE {dt} BETWEEN ?::DATE AND ?::DATE GROUP BY 1
        UNION ALL SELECT {group},1,{num},{den} FROM selected
        WHERE {dt} BETWEEN ?::DATE AND ?::DATE GROUP BY 1''',[a,b,c,d],limit=10000,
        method={'method':'Symmetric Kitagawa rate/mix identity; descriptive, not causal',
                'numerator':asdict(numerator),'denominator':asdict(denominator),
                'earlier':[a,b],'later':[c,d]})
    if evidence['truncated']: raise ValueError('Too many segments; filter before rate/mix decomposition.')
    segments={}
    for category,period,n,denom in evidence['rows']:
        if n is None or denom is None or not math.isfinite(n) or not math.isfinite(denom) or denom<=0 or not 0<=n<=denom:
            raise ValueError('Each segment/window needs a known positive denominator and a numerator between zero and that denominator. No rates are imputed.')
        segments.setdefault(category,{})[period]=(float(n),float(denom))
    if not segments or any(set(v)!={0,1} for v in segments.values()):
        raise ValueError('New or absent segments have unobserved rates. Choose compatible segments/windows; no counterfactual rates are invented.')
    total_before=sum(v[0][1] for v in segments.values()); total_after=sum(v[1][1] for v in segments.values())
    rows=[]
    for category,values in segments.items():
        n1,d1=values[0]; n2,d2=values[1]
        r1,r2=n1/d1,n2/d2; w1,w2=d1/total_before,d2/total_after
        mix=(w2-w1)*(r1+r2)/2; rate=(r2-r1)*(w1+w2)/2
        rows.append((category,r1,r2,w1,w2,mix,rate,mix+rate))
    rate_before=sum(v[0][0] for v in segments.values())/total_before
    rate_after=sum(v[1][0] for v in segments.values())/total_after
    data=pd.DataFrame(rows,columns=['category','rate_earlier','rate_later','weight_earlier','weight_later',
                                   'mix_contribution','rate_contribution','total_contribution'])
    residual=float(data.total_contribution.sum())-(rate_after-rate_before)
    if abs(residual)>1e-10: raise ValueError('Rate decomposition failed its exact reconciliation check.')
    return {'data':data,'rate_earlier':rate_before,'rate_later':rate_after,'rate_change':rate_after-rate_before,
        'denominator_earlier':total_before,'denominator_later':total_after,
        'mix_contribution':float(data.mix_contribution.sum()),'rate_contribution':float(data.rate_contribution.sum()),
        'reconciliation_residual':residual,'evidence':evidence,
        'limitations':'Observed rates use explicit denominators; rates/weights are fractions, contributions are fraction-point changes. '
        'The symmetric identity divides the interaction equally between mix and rate terms. It is accounting, not a causal explanation. '
        'New/absent segments and unknown rates are rejected rather than imputed. Review denominator definitions, exposure, sampling and reporting coverage.'}
