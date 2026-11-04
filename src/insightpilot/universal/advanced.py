"""Interpretable analyses with explicit applicability and observation-window gates."""
from collections import Counter
from dataclasses import asdict
from datetime import date, timedelta
import re

import numpy as np
import pandas as pd

from .engine import frame_of
from .profile import date_expression, numeric_expression


def time_analysis(engine, date_column, metric, period='month'):
    evidence=engine.trend(date_column,metric,period); data=frame_of(evidence)
    if len(data)<3: raise ValueError('At least three observed periods are required.')
    data['period']=pd.to_datetime(data.period)
    data['edge_period']=False; data.loc[[0,len(data)-1],'edge_period']=True
    # A calendar join prevents gaps from being mistaken for consecutive observations.
    frequency={'day':'D','week':'W-MON','month':'MS','quarter':'QS','year':'YS'}[period]
    index=pd.date_range(data.period.min(),data.period.max(),freq=frequency)
    data=data.set_index('period').reindex(index).rename_axis('period')
    data['observed']=data.records.notna(); data['value']=pd.to_numeric(data.value)
    data['rolling_mean_3']=data.value.rolling(3,min_periods=3).mean()
    data['rolling_sd_3']=data.value.rolling(3,min_periods=3).std()
    data['absolute_change']=data.value.diff()
    prior=data.value.shift(1)
    data['relative_change']=(data.value-prior)/prior.abs().replace(0,np.nan)
    lag={'day':365,'week':52,'month':12,'quarter':4,'year':1}[period]
    data['year_ago_change']=data.value-data.value.shift(lag)
    return {'data':data.reset_index(),'evidence':evidence,
        'limitations':'First and last observed periods may be incomplete; source completeness is not inferred. '
        'Missing periods remain missing, not zero. Day/week year-ago lags use 365 days/52 weeks, not exact calendar anniversaries.'}


def forecast(engine, date_column, metric, horizon=3):
    if not 1<=horizon<=12: raise ValueError('Forecast horizon must be 1–12 months.')
    if not metric.additive: raise ValueError('Forecasting currently supports additive sum/count monthly metrics only.')
    analysis=time_analysis(engine,date_column,metric)
    observed=analysis['data'].iloc[1:-1].copy()  # conservative edge-period exclusion
    if len(observed)<36 or observed.value.isna().any():
        raise ValueError('Forecasting needs at least 36 contiguous, nonmissing interior months; edge months are excluded conservatively.')
    values=observed.value.to_numpy(dtype=float)
    train,test=values[:-12],values[-12:]
    candidates={'last_value':np.repeat(train[-1],12),'seasonal_naive':train[-12:]}
    errors={key:float(np.mean(np.abs(test-pred))) for key,pred in candidates.items()}
    chosen=min(errors,key=errors.get)
    fitted=candidates[chosen]; residual=test-fitted
    radius=float(np.quantile(np.abs(residual),.95))
    prediction=np.repeat(values[-1],horizon) if chosen=='last_value' else values[-12:][:horizon]
    months=pd.date_range(observed.period.iloc[-1]+pd.offsets.MonthBegin(1),periods=horizon,freq='MS')
    return {'data':pd.DataFrame({'period':months,'forecast':prediction,'lower':prediction-radius,'upper':prediction+radius}),
            'model':chosen,'holdout_mae':errors,'holdout_months':12,'training_months':len(train),
            'evidence':analysis['evidence'],
            'limitations':'Baseline forecasts only, conditional on stable patterns and data quality. Interval width is the empirical '
            '95th percentile of 12 holdout absolute errors, not a calibrated 95% prediction guarantee. '
            'Structural changes and forecast-horizon error growth are not modeled. Edge months were excluded; the first forecast may cover an observed edge month.'}


def outliers(engine, column):
    summary=engine.numeric_summary(column); values=dict(zip(summary['columns'],summary['rows'][0]))
    if values['valid']<8: raise ValueError('Outlier review requires at least eight finite observations.')
    iqr=values['q3']-values['q1']
    if iqr==0: raise ValueError('The interquartile range is zero; this method cannot assign informative outlier severity.')
    lo,hi=values['q1']-1.5*iqr,values['q3']+1.5*iqr
    x=numeric_expression(column)
    evidence=engine.run(f'''SELECT _row_id,{x} AS value,
        greatest((?-({x}))/? ,(({x})-?)/?,0) AS severity_iqr
        FROM selected WHERE {x}<? OR {x}>? ORDER BY severity_iqr DESC,_row_id''',
        [lo,iqr,hi,iqr,lo,hi],limit=1000,method={'method':'Tukey 1.5 IQR fences','lower':lo,'upper':hi})
    return {'evidence':evidence,'summary':summary,'lower':lo,'upper':hi,
            'limitations':'Univariate screening, not proof of error or fraud. Skewed distributions can legitimately contain many flagged records. Results capped at 1,000; source row IDs enable inspection.'}


def time_anomalies(engine,date_column,metric):
    analysis=time_analysis(engine,date_column,metric)
    data=analysis['data'].iloc[1:-1].copy()
    if len(data)<8 or data.value.isna().any(): raise ValueError('At least eight contiguous interior periods are required for time anomaly screening.')
    median=float(data.value.median()); mad=float((data.value-median).abs().median())
    if not mad: raise ValueError('Median absolute deviation is zero; robust anomaly severity is undefined.')
    data['robust_z']=.67448975*(data.value-median)/mad
    data['flagged']=data.robust_z.abs()>3.5
    return {'data':data,'evidence':analysis['evidence'],
        'limitations':'Median/MAD screening (|robust z| > 3.5); no seasonality or multiple-testing calibration. '
        'Review trend, sample size and known business cycles before interpreting flags. Edge periods excluded.'}


def rfm(engine, customer, transaction, date_column, revenue, as_of):
    entity=engine.column(customer); tx=engine.column(transaction); engine.column(date_column)
    if engine.info['roles'].get(revenue)!='revenue': raise ValueError('Approve the amount as revenue before monetary segmentation.')
    dt=date_expression(date_column); amount=numeric_expression(revenue); cutoff=date.fromisoformat(str(as_of))
    evidence=engine.run(f'''SELECT {entity} AS entity,date_diff('day',max({dt}),?::DATE) AS recency_days,
        count(DISTINCT {tx}) AS transaction_frequency,sum({amount}) AS monetary_value,
        count(*) AS source_rows,count({amount}) AS valid_amounts FROM selected
        WHERE {entity} IS NOT NULL AND {tx} IS NOT NULL AND {dt}<=?::DATE GROUP BY 1 ORDER BY monetary_value DESC NULLS LAST''',
        [cutoff,cutoff],limit=10000,method={'method':'RFM','as_of':cutoff,'revenue_column':revenue,'transaction_id':transaction})
    if evidence['truncated']: raise ValueError('More than 10,000 entities; filter the cohort for this segmentation view.')
    return {'data':frame_of(evidence),'evidence':evidence,'limitations':
        'Transaction frequency uses distinct transaction identifiers; monetary value sums source rows. '
        'Confirm line-item grain to avoid repeated order-total double counting. Negative amounts are retained as net revenue. '
        'Incomplete customer history affects recency/frequency; no lifetime value or causal churn score is inferred.'}


def cohorts(engine, entity, date_column, complete_through):
    who=engine.column(entity); engine.column(date_column); dt=date_expression(date_column)
    cutoff=date.fromisoformat(str(complete_through))
    if cutoff.day!=1: raise ValueError('Specify the first day after the last complete month, exclusive.')
    evidence=engine.run(f'''SELECT cohort::DATE AS cohort,observed_month::DATE AS month,
        date_diff('month',cohort,observed_month) AS month_offset,count(DISTINCT entity) AS active_entities
        FROM (SELECT entity,observed_month,min(observed_month) OVER (PARTITION BY entity) AS cohort FROM
        (SELECT DISTINCT {who} AS entity,date_trunc('month',{dt}) AS observed_month FROM selected
        WHERE {who} IS NOT NULL AND {dt}<?::DATE AND {dt} IS NOT NULL))
        GROUP BY 1,2,3 ORDER BY 1,2''',[cutoff],limit=10000,
        method={'method':'Observed first-activity monthly cohorts','complete_through_exclusive':cutoff})
    if evidence['truncated']: raise ValueError('Too many cohort cells; filter the observation window.')
    data=frame_of(evidence)
    if data.empty: raise ValueError('No eligible entity/date observations.')
    sizes=data[data.month_offset==0].set_index('cohort').active_entities.to_dict()
    # Populate only observed windows; future/censored cells remain absent, not zero.
    cells=[]
    for cohort,size in sizes.items():
        start=pd.Timestamp(cohort); last=pd.Timestamp(cutoff)-pd.offsets.MonthBegin(1)
        months=(last.year-start.year)*12+last.month-start.month
        for offset in range(months+1):
            found=data[(data.cohort==cohort)&(data.month_offset==offset)]
            count=int(found.active_entities.iloc[0]) if len(found) else 0
            cells.append((cohort,offset,count,int(size),count/size))
    table=pd.DataFrame(cells,columns=['cohort','month_offset','active_entities','cohort_size','retention'])
    table['cohort']=pd.to_datetime(table.cohort)
    return {'data':table,
            'evidence':evidence,'limitations':'Cohorts use first observed activity, not verified acquisition. Left-truncated histories can misclassify acquisition. '
            'Only user-confirmed complete months are included; absence within those windows is zero, censored future cells are omitted.'}


def funnel(engine, entity, event, date_column, steps, window_days, observation_end):
    who,what=engine.column(entity),engine.column(event); date_col=engine.column(date_column)
    if len(steps)<2 or len(steps)>8 or len(set(steps))!=len(steps): raise ValueError('Choose 2–8 distinct ordered event steps.')
    if not 1<=window_days<=365: raise ValueError('Funnel window must be 1–365 days.')
    end=pd.Timestamp(observation_end,tz='UTC')
    evidence=engine.run(f'''SELECT _row_id,{who} AS entity,{what} AS "event",CAST({date_col} AS VARCHAR) AS "timestamp"
        FROM selected WHERE {who} IS NOT NULL AND {what} IS NOT NULL ORDER BY _row_id''',limit=50000)
    if evidence['truncated']: raise ValueError('Funnel sequence analysis is bounded to 50,000 selected events; filter the dataset.')
    data=frame_of(evidence)
    data['instant']=pd.to_datetime(data.timestamp,format='ISO8601',utc=True,errors='coerce')
    if data.instant.isna().any(): raise ValueError('Funnel timestamps need valid ISO timestamps; invalid sequences are not silently discarded.')
    data=data[data.instant<end].sort_values(['instant','_row_id'])
    counts=[0]*len(steps); durations=[[] for _ in steps]; censored=0
    for _,records in data.groupby('entity',sort=False):
        starts=records[records.event==steps[0]]
        if starts.empty: continue
        start=starts.instant.iloc[0]
        if start+pd.Timedelta(days=window_days)>end:
            censored+=1; continue
        counts[0]+=1; previous=start
        for index,step in enumerate(steps[1:],1):
            eligible=records[(records.event==step)&(records.instant>previous)&(records.instant<=start+pd.Timedelta(days=window_days))]
            if eligible.empty: break
            previous=eligible.instant.iloc[0]; counts[index]+=1
            durations[index].append((previous-start).total_seconds()/3600)
    return {'data':pd.DataFrame({'step':steps,'entities':counts,
        'conversion_from_start':[n/counts[0] if counts[0] else None for n in counts],
        'median_hours_from_start':[float(np.median(v)) if v else None for v in durations]}),
        'censored_starts':censored,'evidence':evidence,
        'limitations':'One earliest-start funnel per observed entity; later steps must be strictly later, within the fixed window. '
        'Starts without a full observation window are excluded and disclosed. Equal-time events have no verified order and do not progress. '
        'Confirm source completeness and entity identity; no acquisition or causal conversion effect is implied.'}


def text_terms(engine, column):
    col=engine.column(column)
    evidence=engine.run(f'SELECT {col} AS text FROM selected WHERE {col} IS NOT NULL ORDER BY _row_id LIMIT 5000',limit=5000,
        method='First 5,000 selected nonmissing texts in source order; bounded vocabulary exploration, not a representative sample.')
    terms=Counter(); phrases=Counter()
    stop={'the','and','for','with','this','that','was','were','are','from','have','has','not','but','you','your','our','their'}
    for row in evidence['rows']:
        words=[w for w in re.findall(r"[a-zA-Z][a-zA-Z'-]{2,}",str(row[0])[:10000].lower()) if w not in stop]
        terms.update(words); phrases.update(' '.join(pair) for pair in zip(words,words[1:]))
    return {'terms':terms.most_common(30),'phrases':phrases.most_common(30),'evidence':evidence,
        'limitations':'English token counts and adjacent phrases only; each text capped at 10,000 characters. '
        'Not topic modeling, verified classification, sentiment or document prevalence. Counts reflect the disclosed bounded subset.'}
