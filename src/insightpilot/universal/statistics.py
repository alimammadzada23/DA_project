"""Defensible full-data descriptive inference; no causal interpretation."""
import math

import numpy as np
from scipy import stats

from .profile import numeric_expression


def mean_interval(engine, column, confidence=.95):
    if not .8<=confidence<=.99: raise ValueError('Confidence must be between 80% and 99%.')
    result=engine.numeric_summary(column); values=dict(zip(result['columns'],result['rows'][0]))
    n,mean,sd=values['valid'],values['mean'],values['sd']
    if n<3 or sd is None: raise ValueError('At least three finite observations are required for a mean interval.')
    half=float(stats.t.ppf((1+confidence)/2,n-1))*sd/math.sqrt(n)
    return {'n':n,'mean':mean,'lower':mean-half,'upper':mean+half,'confidence':confidence,
            'evidence':result,'assumptions':'Independent observations, representative sampling and a reasonably well-behaved sampling distribution. '
            'Repeated entities, strong skew at small n and selection bias can invalidate this interval. It does not correct nonrepresentative data.'}


def welch_test(engine, numeric, group, first, second):
    if first==second: raise ValueError('Choose two distinct groups.')
    col=engine.column(group); engine.column(numeric); x=numeric_expression(numeric)
    evidence=engine.run(f'''SELECT category,count(numeric_value) n,avg(numeric_value) mean,stddev_samp(numeric_value) sd
        FROM (SELECT {col} AS category,{x} AS numeric_value FROM selected) WHERE category IS NOT DISTINCT FROM ? OR category IS NOT DISTINCT FROM ?
        GROUP BY 1''',[first,second])
    groups={r[0]:r[1:] for r in evidence['rows']}
    if first not in groups or second not in groups or any(groups[g][0]<3 for g in (first,second)):
        raise ValueError('Both selected groups need at least three finite observations.')
    n1,m1,s1=groups[first]; n2,m2,s2=groups[second]
    variance=s1*s1/n1+s2*s2/n2
    if variance<=0: raise ValueError('Zero within-group variance: a t-test is not informative.')
    dof=variance**2/((s1*s1/n1)**2/(n1-1)+(s2*s2/n2)**2/(n2-1))
    se=math.sqrt(variance); delta=m1-m2; critical=float(stats.t.ppf(.975,dof))
    pooled=math.sqrt(((n1-1)*s1*s1+(n2-1)*s2*s2)/(n1+n2-2))
    return {'first':first,'second':second,'n_first':n1,'n_second':n2,'mean_first':m1,'mean_second':m2,
            'mean_difference':delta,'difference_ci95':[delta-critical*se,delta+critical*se],
            'p_value':float(2*stats.t.sf(abs(delta/se),dof)), 'degrees_freedom':dof,
            'hedges_g':delta/pooled*(1-3/(4*(n1+n2)-9)) if pooled else None,'evidence':evidence,
            'assumptions':'Welch two-sided independent-group mean comparison. Review independence, repeated entities, outliers, sampling bias and skew. '
            'A small p-value is not proof of practical importance or causality. This is one planned comparison; adjust p-values when screening many comparisons.'}


def holm_adjust(p_values):
    if any(not math.isfinite(p) or not 0<=p<=1 for p in p_values): raise ValueError('P-values must be in [0,1].')
    order=np.argsort(p_values); output=[0.]*len(p_values); previous=0.
    for rank,index in enumerate(order):
        previous=max(previous,min(1.,(len(order)-rank)*p_values[index])); output[int(index)]=previous
    return output


def regression(engine, x_column, y_column):
    engine.column(x_column); engine.column(y_column)
    x,y=numeric_expression(x_column),numeric_expression(y_column)
    result=engine.run(f'''SELECT count(*) n,corr(y,x) pearson_r,regr_slope(y,x) slope,
        regr_intercept(y,x) intercept,regr_r2(y,x) r_squared,regr_sxx(y,x) sxx,
        regr_syy(y,x) syy FROM (SELECT {x} x,{y} y FROM selected) WHERE x IS NOT NULL AND y IS NOT NULL''')
    values=dict(zip(result['columns'],result['rows'][0])); n=values['n']
    if n<4 or not values['sxx'] or not values['syy']:
        raise ValueError('Regression needs at least four paired finite observations and variation in both variables.')
    r2=values['r_squared']; residual=max(0.,values['syy']*(1-r2))
    se=math.sqrt(residual/(n-2)/values['sxx']); half=float(stats.t.ppf(.975,n-2))*se
    values.update(slope_ci95=[values['slope']-half,values['slope']+half],evidence=result,
        assumptions='One-predictor OLS and Pearson correlation on complete pairs. Review linearity, independent errors, homoscedasticity and outliers. '
        'Intervals assume the model is appropriate. Association does not establish causation; omitted variables and repeated entities matter.')
    return values


def categorical_association(engine, first, second):
    left,right=engine.column(first),engine.column(second)
    if first==second: raise ValueError('Choose two different categorical fields.')
    for col in (first,second):
        values=engine.categories(col)
        if values['truncated'] or len(values['rows'])>20:
            raise ValueError('Filter to at most 20 values per field before categorical association testing.')
    evidence=engine.run(f'''SELECT {left} AS first_value,{right} AS second_value,count(*) AS records FROM selected
        WHERE {left} IS NOT NULL AND {right} IS NOT NULL GROUP BY 1,2''')
    from .engine import frame_of
    frame=frame_of(evidence)
    matrix=frame.pivot(index='first_value',columns='second_value',values='records').fillna(0)
    if min(matrix.shape)<2: raise ValueError('At least two represented values per field are required.')
    values=matrix.to_numpy(dtype=float); n=int(values.sum())
    chi2,p,dof,expected=stats.chi2_contingency(values,correction=False)
    method='Pearson chi-square (no continuity correction)'; odds=None
    if (expected<5).any():
        if values.shape!=(2,2): raise ValueError('Expected cell counts below five: the chi-square approximation is unsuitable for this table.')
        odds,p=stats.fisher_exact(values,alternative='two-sided')
        odds=float(odds) if math.isfinite(odds) else None
        method='Fisher exact two-sided (sparse 2×2 table)'
    return {'method':method,'n_complete':n,'excluded_missing':engine.count()-n,'p_value':float(p),
        'cramers_v':math.sqrt(float(chi2)/(n*(min(values.shape)-1))),
        'odds_ratio_if_fisher':odds,'minimum_expected_count':float(expected.min()),'evidence':evidence,
        'assumptions':'Independent categorical observations. Cramér’s V is an uncorrected descriptive effect size; '
        'sparse cells trigger Fisher exact for 2×2 tables, otherwise the method is unavailable. '
        'Missing values are excluded, not an extra outcome. Dependence, sampling bias and confounding affect inference. No causal claim.'}
