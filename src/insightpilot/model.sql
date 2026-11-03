CREATE TABLE cases (
 case_id VARCHAR PRIMARY KEY, purchase_document VARCHAR, item VARCHAR,
 matching_category VARCHAR, vendor VARCHAR, spend_classification VARCHAR,
 source_attributes JSON
);
CREATE TABLE events (
 case_id VARCHAR, source_order BIGINT, activity VARCHAR,
 event_time TIMESTAMPTZ, resource VARCHAR, lifecycle VARCHAR,
 source_attributes JSON
);
CREATE VIEW ordered_events AS
SELECT *, row_number() OVER (PARTITION BY case_id ORDER BY event_time NULLS LAST, source_order) AS event_rank,
 lag(activity) OVER (PARTITION BY case_id ORDER BY event_time NULLS LAST, source_order) AS previous_activity,
 lag(event_time) OVER (PARTITION BY case_id ORDER BY event_time NULLS LAST, source_order) AS previous_time
FROM events;
CREATE VIEW case_metrics AS
SELECT c.*, m.* EXCLUDE(case_id) FROM cases c JOIN (
 SELECT case_id, count(*) AS event_count, count(DISTINCT activity) AS distinct_activities,
 count(activity) - count(DISTINCT activity) AS repeated_event_count,
 count(*) FILTER (WHERE activity IS NULL) AS missing_activities,
 count(*) FILTER (WHERE event_time IS NULL) AS missing_timestamps,
 min(event_time) AS first_event, max(event_time) AS last_event,
 epoch(max(event_time)-min(event_time))/86400.0 AS observed_span_days,
 string_agg(coalesce(activity,'[missing]'), ' → ' ORDER BY event_time NULLS LAST, source_order) AS variant,
 'unknown' AS completion_status
 FROM events GROUP BY case_id
) m USING(case_id);
CREATE VIEW transitions AS
SELECT case_id, previous_activity AS from_activity, activity AS to_activity,
 epoch(event_time-previous_time)/3600.0 AS waiting_hours
FROM ordered_events WHERE previous_activity IS NOT NULL AND activity IS NOT NULL AND event_time IS NOT NULL AND previous_time IS NOT NULL;
CREATE VIEW variants AS
SELECT variant, count(*) AS case_count FROM case_metrics GROUP BY variant;
CREATE VIEW transition_performance AS
SELECT from_activity,to_activity,count(*) AS occurrences,
 median(waiting_hours) AS median_wait_hours, quantile_cont(waiting_hours,0.9) AS p90_wait_hours
FROM transitions GROUP BY from_activity,to_activity;
CREATE VIEW category_performance AS
SELECT coalesce(matching_category,'[unknown]') AS matching_category, count(*) AS case_count,
 median(observed_span_days) AS median_span_days, quantile_cont(observed_span_days,0.9) AS p90_span_days,
 count(*) FILTER (WHERE repeated_event_count>0) AS cases_with_repeats
FROM case_metrics GROUP BY matching_category;
CREATE VIEW sequence_signals AS
SELECT c.case_id, c.matching_category, 'review_required' AS classification,
 'Invoice receipt recorded before first goods receipt; flow interpretation required' AS reason
FROM cases c JOIN (
 SELECT case_id,min(event_time) FILTER (WHERE activity='Record Invoice Receipt') AS invoice_time,
 min(event_time) FILTER (WHERE activity='Record Goods Receipt') AS goods_time
 FROM events GROUP BY case_id
) e USING(case_id)
WHERE e.invoice_time<e.goods_time;
