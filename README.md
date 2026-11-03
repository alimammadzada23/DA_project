# InsightPilot — AI Business Intelligence

A Business Analyst / Data Analyst portfolio application with independent procurement, customer-complaint and mapped-upload workspaces. The sponsor scenarios are hypothetical; no real engagement, stakeholder sign-off, implemented saving or causal finding is claimed. Existing procurement ingestion, DuckDB queries, dashboards, guided analyst and Power BI artifacts are preserved.

**Current data status:** zero genuine CFPB records and zero genuine BPI records imported in this cloud task. CFPB website/API access is denied by the proxy (HTTP 403); the BPI source previously returned upstream HTTP 503. Test results and screenshots use clearly labeled synthetic references. Real-data findings, deployed CFPB schema/usage acceptance, Windows Power BI and live LLM acceptance remain incomplete. The product is not claimed fully interview-ready.

[Mac installation and all workspaces](docs/multi-dataset-guide.md) · [Procurement guide](docs/dashboard-guide.md) · [Actual UI screenshots](docs/screenshots/) · [Validation evidence](docs/validation.md) · [CFPB source verification](docs/cfpb-source-verification.json)

## Open the application

From your existing repository checkout, after stopping any previous server:

```sh
git switch feature/insightpilot
git pull --ff-only
uv python install 3.12
uv sync --frozen --extra test --python 3.12
uv run --frozen streamlit run app.py --server.port 8501 --server.headless false
```

Run these on your Mac to open its local browser. The cloud task has no supported public-preview endpoint. Fresh launches show a dataset selector. Choose **Procurement Intelligence → Data & import → Explore synthetic portfolio demo** for the 1,200-item seeded demonstration, or import an original XES/XES.gz. The original four-item fixture remains available. API keys and Power BI are optional.

## Independent dataset workspaces

| Workspace | Supported analysis | Source treatment |
|---|---|---|
| Procurement Intelligence | Existing four pages, case filters, process discovery, variants, waits, repeated activities, SQL and AI | Original BPI import is unverified until obtained; demonstrations visibly synthetic |
| Customer Experience & Risk Intelligence | Reported volumes, product donut, company comparison/stacks, daily trends, concentration heatmap, equal-period issue changes, response coverage, paginated drill-down and guided SQL | Official HTTPS period connector records raw bytes, schema, hash, download receipt and quality; uploaded exports remain user supplied |
| Analyze My Dataset | CSV, .xlsx, Parquet; schema/quality review, sample rows and approved mapping; compatible transactional, complaint or process profiles; revenue only after explicit confirmation | Isolated local snapshots; missing/invalid values disclosed; private records stay local unless live-AI transmission is authorized |

Each dataset owns its metadata, filters, analytical profile and history scope. Charts use SQL aggregates; record views paginate at 100 rows. Bar selection reveals matching records within the active filters. Chart PNG/HTML, current-page CSV, manifests, evidence and a complaint management summary can be exported. Raw SQL/JSON and Power BI preparation remain in expandable evidence/Developer Tools.

## Obtain and analyze official CFPB records

On your Mac or another machine with source access:

```sh
uv run --frozen insightpilot cfpb-download data/cfpb/2026-09-01-week --start 2026-09-01 --end 2026-09-08
uv run --frozen insightpilot cfpb-validate data/cfpb/2026-09-01-week/complaints.csv data/cfpb/2026-09-01-week/analytics.duckdb
uv run --frozen insightpilot cfpb-analyze data/cfpb/2026-09-01-week/analytics.duckdb outputs/cfpb-report
uv run --frozen streamlit run app.py --server.port 8501 --server.headless false
```

Choose **Customer Experience & Risk Intelligence**. The connector obtains a full date-filtered CSV, checks its actual headers, compares source rows with the exact JSON API count, and enforces the requested half-open period locally. Source boundary rows and all exclusions are disclosed. It retains source pages for usage review; the software repository’s CC0 terms are not represented as a verified dataset license. The API may change or reject large exports: choose a shorter period and inspect recorded failures. Read [source and KPI definitions](docs/complaints-case-study.md).

If the connector is unavailable, export CSV from the [official CFPB database](https://www.consumerfinance.gov/data-research/consumer-complaints/) and use the complaint workspace’s import form. A manual export remains labeled **user uploaded**, with an unverified completeness/provenance claim. No real findings are prefilled. `cfpb-analyze` produces actual SQL evidence, independent source validation, a management summary, case study and [Power BI-ready export](powerbi/complaints/README.md) after a successful import.

## Four business pages

| Page | Business question | Working capabilities |
|---|---|---|
| Executive Overview | How is the recorded process performing? | KPIs, monthly volume, median/P90 trends, duration bands, flow comparisons, review signals and an executive summary |
| Process Explorer | Where are delays, complexity and repeated activities? | Interactive flow links, variants, activity/repetition analysis, waiting distributions, PM4Py discovery and ordered item timelines |
| Root-Cause Investigation | What factors are associated with longer spans? | Group comparisons, chart-to-case selection, contextual drill-down and ranked investigation cards with evidence/limitations |
| AI Business Analyst | What should management investigate? | Guided SQL questions without credentials, optional restricted LLM tools, business answers/charts and expandable evidence |

A shared case cohort governs every main-page result. Filters cover process type, company, vendor, spend, item-start dates, observed spans, repeats and items containing an activity. Technical SQL, DAX, JSON, source configuration and Power BI proposals are preserved under collapsed **Advanced / Developer Tools**.

## Real-source workflow

Authoritative entry: [official DOI](https://doi.org/10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1). The published expected totals are 76,349 purchasing documents, 251,734 items, 1,595,923 events, 42 activities and 627 resources. These are source-description expectations, not our observed findings.

Follow the [download/import procedure](docs/download-and-run.md), preserve the original file/license and use its actual filename:

```sh
uv run insightpilot ingest data/raw/your-original-log.xes.gz data/analytics/bpi2019.duckdb
uv run insightpilot validate-source data/raw/your-original-log.xes.gz data/analytics/bpi2019.duckdb
uv run insightpilot quality data/analytics/bpi2019.duckdb
uv run insightpilot analyze data/analytics/bpi2019.duckdb outputs/real-analysis
uv run insightpilot export data/analytics/bpi2019.duckdb data/exports
```

Targets must be new files; failed imports do not replace sources. The pipeline streams XES, retains original attributes/order, rejects malformed data atomically, honors global defaults and materializes derived snapshots once. It does not load a full raw-log copy into every chart. `prepare <database>` idempotently materializes an older database's existing views without changing raw tables; reimport to apply changed normalization/model semantics.

## Reliability and limitations

- Items, documents and events remain distinct grains. Dates select full items by first recorded UTC timestamp, retaining complete histories.
- Observed spans are not completed-case cycle time; legitimate multiple receipts/invoices may create repetitions. Matching-flow review signals are not verified noncompliance.
- Source-byte reconciliation independently checks ten metrics. Automated fixtures/reference questions and browser tests support the software; original-log and native Mac acceptance remain outstanding.
- The SQL tool uses a read-only connection, external-access restrictions, statement/table scope checks, row limits and timeout. No LLM shell or report-writing tool exists.
- Optional live LLM credentials use secure process variables. A ChatGPT subscription does not provide API credits. Mocked provider tests are not live-provider acceptance, and arbitrary narrative claims still need evidence review.
- The Power BI companion is preserved with official-schema/static-binding validation. Windows Desktop TMDL/refresh/DAX/rendering acceptance remains unverified; see [Power BI guide](powerbi/README.md).

```sh
uv run --frozen --extra test pytest -q
uv run --frozen --extra test python powerbi/validate.py
```

## Evidence and interview package

[Business case / BRD / backlog / UAT](docs/business-analysis.md), [data and KPI dictionary](docs/data-and-methods.md), [source verification](docs/official-source-verification.json), [portfolio and interview questions](docs/portfolio.md), [live AI acceptance protocol](docs/ai-acceptance.md).

The business story is **Question → Dataset → Investigation → Observations → Recommendations → Validation**. Observations and recommendations must be supported by executed results and disclose source scope; the synthetic UI demo is not an original-company case study.

Use this existing checkout in the isolated cloud task. Do not create a worktree unless explicitly requested. Raw data, databases, credentials, virtual environments and analysis exports are ignored by Git.
