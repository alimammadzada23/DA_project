# InsightPilot — Enterprise Process Intelligence

A Business Analyst / Data Analyst portfolio application for investigating procurement process performance. The sponsor scenario is hypothetical; no real engagement, stakeholder sign-off, implemented saving or causal finding is claimed.

**Current scope:** working analytical software and a four-page dark Streamlit interface. The original BPI Challenge 2019 log remains inaccessible from this cloud instance (4TU upstream HTTP 503). Demonstration patterns are explicitly synthetic. Genuine-log findings and source-license acceptance remain incomplete.

[Open and use the dashboard](docs/dashboard-guide.md) · [Actual UI screenshots](docs/screenshots/) · [Validation evidence](docs/validation.md) · [Data definitions](docs/data-and-methods.md)

## Open the application

From your existing repository checkout, after stopping any previous server:

```sh
git switch feature/insightpilot
git pull --ff-only
uv python install 3.12
uv sync --frozen --extra test --python 3.12
uv run --frozen streamlit run app.py --server.port 8501 --server.headless false
```

Run these on your Mac to open its local browser. The cloud task has no supported public-preview endpoint. Use **Data & import → Explore synthetic portfolio demo** for the 1,200-item seeded UI demonstration, or import a genuine original XES/XES.gz. The original four-item fixture is retained as a separate option. API keys and Power BI are optional for using the dashboard.

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
