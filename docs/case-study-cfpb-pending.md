# CFPB: preserved workflow, blocked authentic-data case

This is **pending**, not a completed genuine-data case. The existing complaint
connector, source validator, denominator rules, comparisons, drill-through,
ISO timestamp fixes and Power BI exports remain covered by the original tests.

On 2026-11-04 the connector requested
`https://www.consumerfinance.gov/data-research/consumer-complaints/search/api/v1/`
for 2025-01-01 inclusive to 2025-01-08 exclusive locally. Result:
`ProxyError: 403 Forbidden`. The receipt is retained at
`data/real-cases/cfpb-2025-01-week/download-failure.json` in this instance.
The required host is absent from the restricted network allowlist. No genuine
CFPB records were acquired. The previously described 81,915-record file is absent.
No synthetic fixture is substituted for real portfolio evidence.

## Acquisition and validation

Use a machine with access to `www.consumerfinance.gov`, or configure that
destination in environment settings. No public-API token is required.
No network policy was broadened or published by this implementation.

```sh
uv run --frozen insightpilot cfpb-download data/cfpb/real-week --start 2025-01-01 --end 2025-01-08
uv run --frozen insightpilot cfpb-validate data/cfpb/real-week/complaints.csv data/cfpb/real-week/analytics.duckdb
uv run --frozen insightpilot cfpb-analyze data/cfpb/real-week/analytics.duckdb outputs/cfpb-real-analysis
uv run --frozen streamlit run app.py
```

Choose Customer Experience & Risk Intelligence. Review source usage/schema,
download receipt, original bytes, exact API count, date coverage and rejected
rows. Record only calculations actually executed. A manual browser export can
be imported but remains user-supplied; columns alone do not authenticate it.

Questions: which complaint volumes and issue mixes changed across compatible
periods; which segments contributed; how much reported timeliness is known?
Complaint counts are not incidence rates without exposure data. Timely share is
Yes/(Yes+No), not Yes/all complaints. Missing dates/unknown responses are disclosed.

After acquisition, generate actual findings, charts, a management summary and
independent reconciliation using the [existing case methodology](complaints-case-study.md).
Until then, the third authentic-data case remains blocked.
