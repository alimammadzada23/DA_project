"""Full-scope additive concentration with an explicit denominator and bounded output."""
import math
from .engine import frame_of


def pareto(engine, dimension, metric, *, threshold=.8):
    if not metric.additive:
        raise ValueError('Share and Pareto analysis require an additive sum/count metric. Averages, ratios and distinct counts are not additive.')
    if isinstance(threshold,bool) or not isinstance(threshold,(int,float)) or not math.isfinite(threshold) or not 0 < threshold <= 1:
        raise ValueError('The concentration threshold must be greater than zero and at most one.')
    column=engine.column(dimension)
    expression,_=metric.compile(engine.info['columns'],engine.info['roles'])
    # Window totals cover EVERY group, before any client-side display bound.
    evidence=engine.run(f'''SELECT category,value,records,
        sum(value) OVER () AS total,
        sum(value) OVER (ORDER BY value DESC NULLS LAST,category NULLS LAST ROWS UNBOUNDED PRECEDING) AS cumulative_value
        FROM (SELECT {column} AS category,{expression} AS value,count(*) AS records FROM selected GROUP BY 1)
        ORDER BY value DESC NULLS LAST,category NULLS LAST''',limit=10000,
        method={'method':'Additive concentration','dimension':dimension,'metric':metric.formula,
                'threshold':threshold,'denominator':'Sum across every selected category, including NULL category. NULL aggregates are unavailable, not zero.'})
    if evidence['truncated']:raise ValueError('More than 10,000 groups. Narrow the selection before concentration analysis; no partial denominator is used.')
    data=frame_of(evidence)
    if data.empty:raise ValueError('No selected groups are available.')
    values=data.value.tolist()
    if any(v is None or not math.isfinite(float(v)) or v<0 for v in values):
        raise ValueError('Concentration requires finite, nonnegative category totals. Negative or unavailable totals cannot define a share of this metric.')
    total=float(data.total.iloc[0])
    if not math.isfinite(total) or total<=0:raise ValueError('The selected total must be finite and greater than zero to define shares.')
    data['share']=data.value/total
    data['cumulative_share']=data.cumulative_value/total
    needed=next(i+1 for i,v in enumerate(data.cumulative_share) if v>=threshold-1e-12)
    # Retain derived values in evidence so saved reports contain exactly the displayed calculation.
    evidence=evidence|{'columns':list(data.columns),'rows':list(data.itertuples(index=False,name=None))}
    return {'data':data,'evidence':evidence,'total':total,'groups_to_threshold':needed,'threshold':threshold,
            'limitations':'Descriptive concentration of nonnegative category totals, not a causal explanation. '
            'Missing grouping values remain a separate category. Missing metric inputs follow the chosen metric definition. '
            'Shares describe the current filters and retained source version.'}
