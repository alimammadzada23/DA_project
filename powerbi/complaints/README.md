# Customer complaint semantic-model design

This extends the preserved procurement PBIP candidate. It is a separate dataset and report design. **No complaint report has been opened, refreshed, calculated or rendered in Windows Power BI Desktop.** No genuine CFPB extract was available in the cloud on 2026-11-03 (proxy HTTP 403).

## Prepare data

On a machine with CFPB access:

```sh
uv run --frozen insightpilot cfpb-download data/cfpb/2026-09-01-week --start 2026-09-01 --end 2026-09-08
uv run --frozen insightpilot cfpb-validate data/cfpb/2026-09-01-week/complaints.csv data/cfpb/2026-09-01-week/analytics.duckdb
uv run --frozen insightpilot cfpb-analyze data/cfpb/2026-09-01-week/analytics.duckdb outputs/cfpb-report
```

Copy `outputs/cfpb-report` to Windows. Preserve `manifest.json`, `source-validation.json`, `query-evidence.json`, the management summary and case study beside `Complaints.parquet`. The manifest distinguishes an official connector receipt, unverified user uploads and synthetic tests. Never use a synthetic export as a genuine case study.

## Model

| Table | Grain / fields | Relationships |
|---|---|---|
| Complaints | One retained valid complaint ID; company, product, issue, state, response, timely, record_date | Many side of all relationships |
| DimDate | One calendar date, plus year/month | Single direction, active 1:* to record_date |
| DimCompany | One distinct company, including missing category | Single direction 1:* to company |
| DimProduct | One distinct product | Single direction 1:* to product |
| DimIssue | One distinct issue | Single direction 1:* to issue |
| DimResponse | One distinct response category | Single direction 1:* to response |

Keep complaint IDs as text, dates as Date, timely as nullable text (`Yes`, `No`, blank). `record_id` is source row order, not an enterprise business key. Hide technical IDs from summary visuals. Missing dates stay in total complaints and are excluded by a date slicer; disclose that effect. Do not add a complaint-rate measure without a defensible exposure denominator.

Create a `DataFolder` text parameter and a `Complaints` query using [Complaints.pq](Complaints.pq). The dimensions can reference Complaints and remove duplicate keys. Create a continuous date table with CALENDAR from the nonblank observed min/max, mark it as the date table, and add a year-month sort key. Check duplicate dimension keys before defining relationships.

Candidate measures are in [measures.dax](measures.dax). They use native filter context. Timely-response share uses only known Yes/No records, and its coverage is a separate measure. Monthly change needs a continuous marked date table and a comparable date selection; partial periods and immature recent data require disclosure. DAX is prepared text, not executed validation.

## Report pages

1. **Executive:** complaint count, represented companies, timely share, known coverage; received-date trend; product donut (top five plus a computed Other); issue bars. Display source period, origin and missing-date count.
2. **Company comparison:** company slicer, volume bars, two company cards, product stacks, company trends and a known-response denominator table. Label rankings “Reported complaint volume.” State that volume does not establish quality.
3. **Investigation:** company/product heatmap (bounded top groups), issue changes across equal periods, response categories, source-record drill-through. Separate observations, hypotheses and actions.
4. **Evidence:** source manifest, quality/exclusion counts, KPI definitions, validation status and analytical limitations. Exclude complaint narrative text from normal overview pages.

## Exact Windows acceptance checks

- Import the local Parquet with Power BI Desktop; verify all source types, record count and unique complaint-ID grain against `source-validation.json`.
- Apply Power Query, inspect errors and confirm zero unintended row exclusions. Check missing dates/timely values remain blank.
- Create dimensions/relationships; verify cardinality, single direction, and active date relationship. Refresh twice and confirm stable counts for the frozen file.
- Paste and execute each candidate DAX measure. Compare the totals with `query-evidence.json → summary`: complaints, companies, known_timely, timely_yes, timely_no, timely_share.
- Select a company; compare all cards, product counts and trends with the corresponding filtered SQL from the application. Test two companies and a zero-record intersection.
- Test an inclusive date range and account explicitly for records without usable dates. Confirm no hidden total-row denominator in percentage visuals.
- Confirm donut totals include Other and missing categories; check that stacked bars reconcile with company totals. Check heatmap group selection and drill-through preserve report filters.
- Confirm labels say reported volumes, no unsupported company-quality ranks/rates/causes/savings, and the source origin is always visible.
- Validate monthly-change behavior for a complete month, a partial month, no preceding month and missing dates. Hide that measure when periods cannot be compared.
- Verify all four page layouts, tooltips, slicers, reset, drill-through, export and refresh on actual Windows Desktop. Save the tested `.pbix`, Desktop version, screenshots and a completed reconciliation table.

Until these checks are executed and recorded, this is **Power BI-ready data and a candidate semantic design**, not a validated Power BI report.
