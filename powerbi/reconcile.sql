-- Execute against the exact snapshot exported to Power BI; compare to reconcile.dax.
SELECT count(*) AS items, count(DISTINCT purchase_document) AS documents,
 median(observed_span_days) AS median_days,
 quantile_cont(observed_span_days,0.9) AS p90_days,
 count(*) FILTER (WHERE repeated_event_count>0) AS repeat_cases,
 count(*) FILTER (WHERE repeated_event_count>0)*1.0/nullif(count(*),0) AS repeat_rate
FROM case_metrics;

SELECT matching_category, count(*) AS items, count(DISTINCT purchase_document) AS documents,
 median(observed_span_days) AS median_days,
 quantile_cont(observed_span_days,0.9) AS p90_days,
 count(*) FILTER (WHERE repeated_event_count>0) AS repeat_cases,
 count(*) FILTER (WHERE repeated_event_count>0)*1.0/nullif(count(*),0) AS repeat_rate
FROM case_metrics GROUP BY matching_category ORDER BY matching_category;

SELECT count(*) AS items, count(DISTINCT purchase_document) AS documents,
 median(observed_span_days) AS median_days,
 quantile_cont(observed_span_days,0.9) AS p90_days,
 count(*) FILTER (WHERE repeated_event_count>0) AS repeat_cases,
 count(*) FILTER (WHERE repeated_event_count>0)*1.0/nullif(count(*),0) AS repeat_rate
FROM case_metrics WHERE matching_category IS NULL;

SELECT c.matching_category, count(t.case_id) AS transitions,
 median(t.waiting_hours) AS median_wait_hours,
 quantile_cont(t.waiting_hours,0.9) AS p90_wait_hours
FROM cases c LEFT JOIN transitions t USING (case_id)
GROUP BY c.matching_category ORDER BY c.matching_category;

SELECT CASE WHEN waiting_hours<1 THEN '<1 hour'
 WHEN waiting_hours<24 THEN '1-24 hours'
 WHEN waiting_hours<168 THEN '1-7 days' ELSE '7+ days' END AS waiting_band,
 count(*) AS transitions
FROM transitions GROUP BY waiting_band;
