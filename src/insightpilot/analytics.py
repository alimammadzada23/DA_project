"""Auditable predefined investigations; spans never imply case completion."""
QUESTIONS = {
 'item_volume': ('How many purchase-order items are recorded?', 'SELECT count(*) AS items FROM cases'),
 'event_volume': ('How many events are recorded?', 'SELECT count(*) AS events FROM events'),
 'document_volume': ('How many purchase documents are recorded?', 'SELECT count(DISTINCT purchase_document) AS documents FROM cases'),
 'activity_volume': ('How many distinct activities are recorded?', 'SELECT count(DISTINCT activity) AS activities FROM events'),
 'user_volume': ('How many resources are recorded?', 'SELECT count(DISTINCT resource) AS resources FROM events'),
 'median_span': ('What is the median observed case span?', 'SELECT median(observed_span_days) AS median_days FROM case_metrics'),
 'p90_span': ('What is the 90th percentile observed case span?', 'SELECT quantile_cont(observed_span_days,0.9) AS p90_days FROM case_metrics'),
 'repeat_cases': ('How many cases contain repeated activities?', 'SELECT count(*) AS cases FROM case_metrics WHERE repeated_event_count>0'),
 'repeat_events': ('How many repeated activity occurrences are recorded?', 'SELECT sum(repeated_event_count) AS repeat_occurrences FROM case_metrics'),
 'variant_count': ('How many process variants are recorded?', 'SELECT count(*) AS variants FROM variants'),
 'top_variants': ('Which variants account for most cases?', 'SELECT * FROM variants ORDER BY case_count DESC,variant LIMIT 20'),
 'categories': ('Which matching categories have the longest observed spans?', 'SELECT * FROM category_performance ORDER BY median_span_days DESC,matching_category'),
 'transitions': ('Which transitions have the longest median waiting intervals?', 'SELECT * FROM transition_performance ORDER BY median_wait_hours DESC,from_activity,to_activity LIMIT 30'),
 'missing_users': ('How many events have no resource?', 'SELECT count(*) AS events FROM events WHERE resource IS NULL'),
 'missing_times': ('How many events have no timestamp?', 'SELECT count(*) AS events FROM events WHERE event_time IS NULL'),
 'sequence_signals': ('Which sequences require matching-flow investigation?', 'SELECT * FROM sequence_signals ORDER BY case_id LIMIT 100'),
 'activity_frequency': ('Which activities occur most often?', 'SELECT activity,count(*) AS events FROM events GROUP BY activity ORDER BY events DESC,activity'),
 'monthly_cases': ('How many cases start in each month?', "SELECT date_trunc('month',first_event) AS month,count(*) AS cases FROM case_metrics GROUP BY month ORDER BY month"),
 'resource_frequency': ('Which resources record most events?', 'SELECT resource,count(*) AS events FROM events GROUP BY resource ORDER BY events DESC,resource LIMIT 30'),
 'long_spans': ('Which cases have the longest observed spans?', 'SELECT case_id,observed_span_days FROM case_metrics ORDER BY observed_span_days DESC,case_id LIMIT 30'),
}


def discover(con):
    """PM4Py DFG discovery using an aggregated, bounded representation."""
    import pm4py
    dfg = {(a,b): n for a,b,n in con.execute('SELECT from_activity,to_activity,count(*) FROM transitions GROUP BY ALL').fetchall()}
    starts = dict(con.execute('SELECT activity,count(*) FROM ordered_events WHERE event_rank=1 GROUP BY activity').fetchall())
    ends = dict(con.execute('SELECT activity,count(*) FROM (SELECT *,row_number() OVER(PARTITION BY case_id ORDER BY event_rank DESC) r FROM ordered_events) WHERE r=1 GROUP BY activity').fetchall())
    if None in starts or None in ends:
        raise ValueError('Resolve missing activities before process discovery')
    net, initial, final = pm4py.convert_to_petri_net(dfg, starts, ends)
    return {'method': 'PM4Py DFG to Petri net (descriptive, not a normative compliance model)',
            'edges': [{'from':a,'to':b,'count':n} for (a,b),n in sorted(dfg.items())],
            'start_activities':starts,'end_activities':ends,
            'petri_places':len(net.places),'petri_transitions':len(net.transitions),
            'initial_marking_places':len(initial),'final_marking_places':len(final)}
