# One InsightPilot application

Starting point: `156a116` (271 passing tests, 12 pandas deprecation warnings).
Work is local on `codex/unified-insightpilot`. No new push, merge or deployment is authorized.

The old front door offered independent procurement, complaints, mapped custom-data
and universal applications. Their navigation, filters, definitions and evidence
were separate. The replacement has one catalog, immutable data versions, reviewed
semantics, analysis context, SQL Lab and evidence/report pipeline.

| Previous capability | Starting implementation | Unified destination | Decision | Regression evidence |
| --- | --- | --- | --- | --- |
| Industry/workspace front doors | `workspace_ui.py`, legacy `ui.main` | One `workspace_ui.main` → `universal.ui` | Retire routes | `test_fresh_launch_has_one_product_and_no_synthetic_substitution` |
| CSV/Excel/Parquet/JSON/DB/API/PostgreSQL | `universal/store.py`, `connectors.py` | Data Hub | Keep/refactor | Universal import and boundary suites; four authentic imports |
| Streaming XES | `ingest.ingest`, fixed parser DB | `universal/process.import_xes` → catalog cases/events | Reuse parser; generic analysis | `test_xes_enters_same_catalog_and_process_evidence`; original parser/reconciliation suites |
| Domain dashboard/guided SQL | `Dashboard`, `CustomAnalytics`, `Complaints`, legacy UI | Overview/Explore/SQL Lab | Replace UI; retain CLI libraries | Unified UI, scoped SQL and independent reconciliation suites |
| Developer SQL editor | `Dashboard.custom_sql`, `Analyst.query` | `universal/sql_lab.SQLLab` | Refactor AST scope + guards | `test_sql_lab_fails_closed`, CTE/window/EXPLAIN/paging tests; real Ace browser edits |
| Filters and drill-through | Separate domain cohorts + universal filters | `Engine` + `AnalysisContext` | Unify | Shared-filter UI test; saved-view restore/reset; actual pointer selection on all four sources |
| Type correction and deduplication | `Workspace.transform`, version history | Quality & Preparation | Keep; restore reviewed metadata on undo | Store/boundary tests; `test_reports_replay_preserve_semantics_and_undo` |
| Role/metric definitions | `approve_roles`, limited `Metric.compile` | Reviewed units/date purposes + metric dictionary | Extend | Aggregate/ratio/weighted/quantile/derived-expression/manual-reference tests; incompatible units rejected |
| Advanced statistics and investigations | Universal statistics/advanced/segmentation/investigation | Advanced | Reuse, shared context and gating | Original algorithm suites; real regression, CI, Welch, clustering, contribution and time reference receipts |
| Process/root cause | `Dashboard` process methods; `CustomProcessAnalytics` | Generic event sequence and contribution workflows | Integrate generalizable analysis; retain old CLI semantics | XES integration test + process/parser suites; actual additive investigation reference |
| Deterministic/live analyst | Legacy `Analyst`; universal finite planner | AI Analyst | Consolidate UI around finite planner | Zero-token real-data answers; invalid provider plans/consent tests; no live provider claim |
| HTML/CSV/Excel exports | Universal `report.py`; domain export helpers | Reports/saved query and chart blocks | Extend | Replay tests; successive HTML+manifest browser downloads; bounded full-selection package tests |
| Power BI assets | `powerbi/`, domain export ZIP | Current-selection CSV/XLSX, dictionary, `PowerQuery.m` | Preserve assets; general preparation | Selection package scope/formula safety tests; no native PBIX claim |
| Old files and DB formats | Local `data/`, `outputs/`, domain schemas | Read-only snapshot import / CLI compatibility | Preserve | Hash-before/after source DB tests; no destructive migration |
| Obsolete domain UI assertions | Four UI test files + old workspace UI suite | Unified contract and browser suites | Retire obsolete assertions; preserve backend tests | `retired-ui-tests.json`; final complete regression receipt |

Compatibility boundary: `insightpilot.cli`, ingestion, procurement/complaint
libraries and the existing `powerbi/` assets remain available for earlier
database formats. They are not alternate applications in the Streamlit product.
Existing files under `data/` and `outputs/` are retained. Universal metadata changes
are additive; saved objects are created only when explicitly saved.

Validation receipts, independent reference calculations, real browser checks and
the final limitations are recorded in `UNIFIED_ACCEPTANCE.md` when executed.
