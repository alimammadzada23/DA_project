# Open and use InsightPilot

## Start the dashboard on your Mac

Use the existing DA_project checkout. The application is on `feature/insightpilot` until its pull request is merged. Preserve any local work; these commands do not reset or force-push anything.

```sh
cd /path/to/DA_project
git fetch origin
git switch feature/insightpilot
git pull --ff-only
uv python install 3.12
uv sync --frozen --extra test --python 3.12
uv run --frozen streamlit run app.py --server.port 8501
```

If `uv` is not installed, install it using its official instructions, or `brew install uv` if you use Homebrew. In a browser **on the same Mac**, open `http://localhost:8501`. The terminal must remain running; stop it with Ctrl+C. If port 8501 is occupied, use `--server.port 8502` and open that port instead. These are local Mac addresses, not links to the remote cloud workspace. Native Apple Silicon execution remains unverified; the locked Python environment and application interactions were tested on Linux.

No API key, Power BI installation or environment publication is needed for the dashboard and guided SQL analyst.

## Choose a source

- **Original log available:** expand **Load an event log or open the demo** in the sidebar, select an original `.xes` or `.xes.gz`, leave the synthetic checkbox unchecked and click **Import XES**. The source is retained under `data/imports/` and a new analytical database is created. Existing source/databases are not overwritten.
- **Try the application now:** click **Open labeled synthetic demo**. Its banner explicitly identifies the records as synthetic; they are not findings from BPI 2019. Switching to the demo is an explicit action, never a fallback after a real-data failure.
- **Already imported:** enter its database path in **DuckDB database**. The standard real-log path is `data/analytics/bpi2019.duckdb`.

Streamlit uploads are subject to its configured file-size limit and keep the uploaded buffer in memory. For a large original log use the streaming CLI instructions in [download-and-run.md](download-and-run.md), then enter the resulting database path. New imports prepare immutable analytical tables once to avoid regrouping the full event log on every dashboard interaction. For an existing database created by an older version, stop its active use and run:

```sh
uv run --frozen insightpilot prepare data/analytics/bpi2019.duckdb
```

Preparation is idempotent and leaves raw cases/events unchanged. It materializes existing analytical views, not new business semantics. Reimport into a new database to apply changed normalization/model definitions; direct raw-table edits are unsupported and would invalidate derived snapshots.

## Filter the investigation

The sidebar controls matching category, vendor, spend classification, repeated-activity status, an optional item-start date range and an optional observed-span range. Empty multiselects mean all values. Select **(not recorded)** to isolate null attributes. Dimension pickers list at most 1000 distinct values; the UI discloses truncation and advanced database SQL remains available for other values.

Date ranges refer to the **first recorded event of an item, in UTC**, with both selected dates included. All recorded events of matching items are retained, including later events outside that range. Items without dates cannot match an active date filter. Span filters similarly exclude items without a usable span. **Reset filters** restores the full cohort.

Seven KPIs update together: item, document and event counts; items with repeats; median/P90 observed spans; and repeat-item rate. Missing timestamps/activity names produce cohort-specific warnings. A span is first-to-last recorded time, not proven completion time; repetitions may be legitimate multi-receipt/invoice behavior.

## Explore the tabs

1. **Dataset & quality:** download the selected-cohort KPI evidence. Full-source quality/published-count checks remain separate and deliberately do not change with filters.
2. **Process analysis:** compare category spans, monthly item starts, the top 20 variants, transition intervals and waiting bands. The minimum-transition-occurrence control affects the bottleneck table, not which items belong to the cohort. Build a filtered PM4Py process map; only the top 50 edges are drawn, while its evidence contains all discovered edges up to the documented 1000-edge bound. Review signals are not compliance verdicts. Inspect a displayed item or enter an exact item ID to see its ordered event timeline. Lookups stay inside the active filters. Download dashboard evidence for the displayed investigation.
3. **Investigation & SQL:** select one of 20 curated questions. For custom queries, **Selected cohort** requires `selected_cases`, `selected_events`, `selected_transitions` or other `selected_*` relations; raw tables and dynamic table functions are rejected in that mode. **Whole database** explicitly bypasses the filters while retaining read-only/external-access restrictions. Click **Execute SQL**; changing the editor alone does not execute it. Source hash, SQL, parameters, filter context and truncation remain visible. Changing filters or scope clears the previous editor result.
4. **Power BI & approvals:** download the prepared project or explicitly approve exporting a DAX proposal. These controls never modify report files. Windows acceptance is separate from operating this dashboard.
5. **Validation & limitations:** review the documented evidence and interpretation limits.

## Ask a question without an API key

Keep **Guided SQL analyst (no API)** selected and ask, for example:

- “How many purchase-order items are recorded?”
- “Which flows have the longest observed spans?”
- “Which process variants are most common?”
- “Where are long waiting intervals?”
- “How many items have repeated activities?”
- “Show recorded exceptions for review.”

This mode uses transparent intent routing and real SQL. It is **not an LLM**, does not invent query results, and retains the current case filters. Unsupported questions return a list of supported investigations; causality, verified compliance and financial-saving claims are not supplied. Inspect SQL/rows under each saved answer; saved investigations retain their original filters even after you change the dashboard cohort.

Optional **Live LLM with filtered tools** uses the existing provider integration. It requires configured credentials and consent to transmit schema/results. Its only analytical tool executes a curated question in the fixed cohort; the model cannot change filters or call database-wide SQL. Its transport is tested with mocks, not a live provider in this instance. None of this blocks guided analysis.

The same guided workflow is available in Terminal:

```sh
uv run --frozen insightpilot guide data/analytics/bpi2019.duckdb \
  'Which flows have the longest observed spans?'
```

## Current genuine-data dependency

The official TU/e description and DOI resolver were retrieved successfully. The resolver points to **https://data.4tu.nl/articles/_/12715853/1**. That exact file-repository landing page returns HTTP 503 upstream connection timeouts in this instance. No original log or file license has been retrieved, so no genuine-log business findings are claimed. See [official-source-verification.json](official-source-verification.json) for recorded source facts and access evidence. If the record opens in your browser, obtain the original XES/XES.gz and import it as above.

## Reproduce validation

```sh
uv run --frozen --extra test pytest -q
uv run --frozen --extra test python powerbi/validate.py
```

The current suite includes data pipeline/reference tests, independent source reconciliation, filtered metric/event/document-count agreement, date/cohort semantics, empty and missing-data states, SQL scope/injection checks, filtered process discovery, guided chat, mocked live tool calls and Streamlit interactions. Tests on fixtures do not substitute for validation of the original log.
