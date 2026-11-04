# Next-generation implementation audit

Baseline: `3c19a2e6368ccc888a92c3912853c31d34160abe`, from
`feature/insightpilot`. `main` contains only a README. This work extends the
existing application on local branch `codex/insightpilot-next`; nothing is pushed
or deployed automatically.

## Existing architecture and limits

- Streamlit routes procurement, complaint and mapped-upload workspaces.
- DuckDB stores immutable local snapshots; SQL aggregates and paginated records
  keep large record sets out of browser memory.
- XES ingestion, process discovery, complaint denominators, ISO timestamp fixes,
  guided analysis, optional consent-controlled LLM calls, and Power BI preparation
  already exist and must remain operational.
- Tabular uploads support CSV/XLSX/Parquet, fixed roles, 500,000 rows and a single
  flat table. Only one arbitrary numeric measure is exposed. Schema suggestions
  are name-based and there is no general reusable metric/relationship model.
- Fixed numeric bands, hard-coded domain queries and only two generic views limit
  useful exploration of unfamiliar datasets. There is no general statistical,
  forecasting, cohort, reversible-preparation or relationship workflow.
- Existing source-verification documents explicitly report unavailable genuine
  CFPB/BPI source data. Their synthetic screenshots are not real-data evidence.
- Optional LLM tests use mocks. Live-provider and native Mac/Power BI Desktop
  acceptance must not be inferred from Linux tests.

## Backlog, in dependency order

1. Verify the baseline tests before changes; preserve all existing entry points.
2. Add a bounded, local multi-table store, immutable original values, strict
   import errors, type conversion receipts and reversible version history.
3. Profile arbitrary columns with uncertainty-aware semantic suggestions,
   searchable metadata, quality evidence and user-approved roles.
4. Define explicit safe metrics and relationships with denominator/cardinality
   checks and join multiplication protection.
5. Provide a complete import → understand → explore → investigate → report
   workflow, with linked record selection and purposeful readable charts.
6. Add independently tested statistical comparisons, time analysis, interpretable
   anomalies, segmentation, cohort/funnel/text analysis with applicability gates.
7. Validate authentic cross-domain sources, benchmark 10k/100k/1m synthetic rows,
   run regression and UI checks, and document measured results and limitations.

Final implementation status, evidence and incomplete acceptance criteria are
recorded in `next-generation-validation.md`. This backlog is not a completion claim.
