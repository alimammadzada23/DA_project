# InsightPilot

One local-first analytics application built with Python 3.12, Streamlit, DuckDB,
Plotly and SciPy. Import unfamiliar structured data, review its meaning, define
metrics, explore comparisons, investigate with safe SQL, and export reproducible
evidence. Deterministic analysis works without an account, model or API key.

The application now has one catalog and navigation. The former procurement,
complaint and mapped-upload front doors are retired. Existing databases, sources,
reports and backend/CLI compatibility remain intact.

[Local AI acceptance](docs/LOCAL_AI_ACCEPTANCE.md) ·
[Mac local AI setup](docs/LOCAL_AI_MAC_GUIDE.md) ·
[Local AI architecture](docs/LOCAL_AI_ARCHITECTURE.md) ·
[Unified baseline acceptance](docs/UNIFIED_ACCEPTANCE.md) ·
[Migration inventory](docs/UNIFICATION_MIGRATION.md) ·
[Architecture and SQL security](docs/UNIFIED_ARCHITECTURE.md) ·
[Measured scale](docs/unified-benchmarks.json) ·
[Independent initial evaluations](docs/unified-initial-evaluation.json)

## Run on your Mac

The local AI upgrade is on **`codex/insightpilot-local-ai`**, based on the published
unified baseline `e3874ee`. **This new branch has not been pushed.** Transfer its
source archive or Git bundle; fetching the baseline alone does not install this
upgrade. These commands use ordinary Mac paths:

```bash
# If you do not already have a clone:
git clone https://github.com/alimammadzada23/DA_project.git
cd DA_project

# Otherwise cd into your existing DA_project checkout first.
git status --short
# Preserve any local edits before changing branches; do not reset or clean data.
git bundle verify ~/Downloads/insightpilot-local-ai.bundle
git fetch ~/Downloads/insightpilot-local-ai.bundle codex/insightpilot-local-ai:codex/insightpilot-local-ai
git switch codex/insightpilot-local-ai

# Install uv first if needed: https://docs.astral.sh/uv/getting-started/installation/
uv python install 3.12
uv sync --locked --extra test
uv run streamlit run app.py --server.address 127.0.0.1
```

Open **http://localhost:8501** in your Mac browser. No shared cloud desktop is
required. Native macOS acceptance has not been executed in this Linux environment.

For optional local inference, install Ollama yourself and explicitly run
`ollama pull qwen3.5:4b`. Follow the [Mac guide](docs/LOCAL_AI_MAC_GUIDE.md),
including disabling Ollama cloud features. Then choose **AI Analyst → Local AI
Analyst** and refresh installed models. Default endpoint is loopback
`http://127.0.0.1:11434`; no paid provider is required. Standard analysis, SQL Lab
and all other pages work without Ollama. Actual Qwen quality and Apple Silicon
performance remain unverified here; mocked integration is reported separately.

## First minute

1. **Data Hub:** import a CSV/TSV/XLSX/Parquet/JSON/JSONL file or read-only database
   snapshot. Original source bytes, SHA-256, row count and versions are retained.
2. **Quality & Preparation:** search every field, inspect values and missingness,
   and confirm row grain, field roles, units and each timestamp's purpose. Choose
   a date basis explicitly. Reversible preparation retains rejected conversions.
3. Define a metric using the ordinary metric builder. A record count is a
   structural description; rates need an explicit eligible denominator.
4. **Overview / Explore:** choose the shared metric and comparison field. Review
   calculated interpretations, select a bar and inspect its supporting records.
5. **SQL Lab:** generate a query or edit a read-only SELECT/CTE. Inspect its plan,
   save it, chart the result or send executed evidence to Reports.
6. **Reports:** download standalone HTML, replay a manifest, assemble saved chart
   and query blocks, or export the current selection as CSV/XLSX with a Power
   Query template and metric dictionary.

No industry wizard or demonstration filename controls the analytics. Multiple
timestamps remain ambiguous until reviewed. Short observed date ranges suggest
daily grain; a single period is not presented as a time trend. Shared sidebar
filters, metric, comparison and date basis persist across pages. Saved views also
retain the data version and chart preset.

## Capabilities and boundaries

| Navigation | Working capability |
| --- | --- |
| Overview | Reviewed metric, data-specific charts, deterministic findings, SQL/provenance, supporting records |
| Data Hub | Multi-table file imports, separator preview, SQLite/DuckDB snapshots, bounded configured HTTP APIs, optional PostgreSQL, structural XES import, relationship receipts |
| Quality & Preparation | Full searchable catalog, summaries, reviewed roles/units/date purposes, reversible typing, locale parsing, null mapping, trim, date assembly, rename/select, deduplication and undo |
| Explore | Ranked bars, histograms, scatterplots, calendar trends, group box summaries, bounded heatmaps, genuine bar-selection drill-through and paginated records |
| SQL Lab | Guided builder, SQL-highlighted editor and accessible text alternative, SELECT/CTE validation, approved joins, time/memory/row limits, EXPLAIN, named queries, charts and report blocks |
| Advanced | Statistical comparisons, regression, outliers, time analysis, guarded baseline forecasts, cohorts, funnels, RFM, text, clustering, contribution/rate-mix and event sequences |
| AI Analyst | Standard finite questions or optional local Ollama structured plans; compound measures, guarded rates, follow-ups, charts, evidence and saved replay; model never supplies executable SQL or numerical findings |
| Reports | Version-bound replay, standalone escaped HTML, saved evidence blocks, chart/PNG/HTML downloads and bounded source-scope CSV/XLSX/Power Query preparation |

File imports: 512 MiB / 5 million rows / 150 columns; Excel 100 MiB / 500,000 rows.
CSV and TSV require UTF-8. Delimiter is explicit; ambiguous slash dates and numeric
locales require a reviewed transformation. Nested records must be flattened.
SQL results are paged, at most 5,000 rows per page, with a 10-second default
interruption budget and 768 MB connection memory budget. The ordinary records
viewer sends 100 rows per page to the browser. Full-selection export packages
are capped at 100,000 rows, with an explicit refusal rather than silent truncation.

The SQL allowlist is intentionally conservative: joins must directly connect the
active fact to one validated unique-key dimension with explicit equality.
Join CTE/subquery results only after expressing the approved join in a CTE.
CROSS/ASOF, recursive queries, arbitrary table functions, commands and external
reads are unavailable. Unknown functions fail closed.

Statistical assumptions remain visible; descriptive contributions are not causes.
Forecasting is a guarded baseline with empirical holdout errors, not a calibrated
production forecast. Full conversational AI, maps, native PDF rendering, live
provider/PostgreSQL acceptance, and Power BI Desktop validation remain outside the
verified scope. Browser printing can produce PDF; no native PDF engine is claimed.

## Development and reproduction

```bash
uv sync --locked --extra test --extra browser
uv run pytest -q
uv run python scripts/acquire_unified_sources.py
uv run python scripts/validate_unified.py
uv run python scripts/browser_unified.py
uv run python scripts/prepare_heldout_evaluation.py
uv run python scripts/browser_heldout.py
uv run python scripts/benchmark_universal.py --output outputs/local-benchmarks.json
uv build --out-dir outputs/package
```

Browser validation needs Chromium installed, or Playwright's Chromium installed
with `uv run playwright install chromium`. Reports distinguish actual downloads
and executions from untested integrations. Source URLs, licenses, exact hashes,
reference methods and the held-out procedure are recorded in the acceptance
receipts. Public sources stay under ignored `data/`; reports and workspaces stay
under ignored `outputs/`. No private dataset, generated secret or credential is
needed in Git.

Optional extras: `uv sync --extra postgres` installs the PostgreSQL client.
Credentials are read from secure process configuration, never saved in receipts.
The separate optional external planner uses `INSIGHTPILOT_LLM_API_KEY`, `INSIGHTPILOT_LLM_MODEL` and
`INSIGHTPILOT_LLM_BASE_URL`; transmitting the question/schema/metric requires
explicit UI consent. Source rows and calculated values are not sent by that planner.
Local Ollama configuration and its separate privacy boundaries are documented in
[LOCAL_AI_ARCHITECTURE.md](docs/LOCAL_AI_ARCHITECTURE.md).

Earlier database formats remain usable through `uv run insightpilot --help` and
read-only Data Hub snapshots. Earlier source-specific Power BI assets remain in
`powerbi/`; the unified Reports page prepares the current dataset independently.
