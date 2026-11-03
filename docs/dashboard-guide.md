# InsightPilot dashboard — opening and interview walkthrough

This guide covers the preserved procurement module. Choose **Dataset workspace → Procurement Intelligence** first. For the new complaint/upload workspaces and complete Mac setup, see [multi-dataset-guide.md](multi-dataset-guide.md).

## Update and reopen on your Mac

Stop an existing Streamlit process with **Ctrl+C first**. Use the current checkout; no reset or force-push is required.

```sh
cd /path/to/DA_project
git fetch origin
git switch feature/insightpilot
git pull --ff-only
uv python install 3.12
uv sync --frozen --extra test --python 3.12
uv run --frozen streamlit run app.py --server.port 8501 --server.headless false
```

Install `uv` using its official installer, or `brew install uv` if you use Homebrew. On your Mac the final command opens its browser; otherwise use the Local URL printed in that same Mac terminal. Keep Terminal running and stop with Ctrl+C. If the port is occupied, use `--server.port 8502`. These commands launch locally; the cloud workspace does not expose a supported public preview. Native Apple Silicon execution has not been observed here; Linux installation, browser interaction and tests are verified.

The dark theme is tracked in `.streamlit/config.toml`. Restart the server after pulling changes so it loads the updated presentation modules and theme. Plotly is included in the frozen dependency lock.

## Choose data explicitly

Expand **Data & import** in the sidebar:

- **Explore synthetic portfolio demo** prepares 1,200 reproducible synthetic items across four illustrative matching flows, eight start months, four companies and multiple vendors/spend classes. Its seed is 2019. A purchasing document retains a single company and vendor across its items. Timing and repetition patterns are invented solely for UI exploration. A prominent warning labels every page; these are not BPI findings.
- **Open labeled synthetic demo** retains the original four-item test fixture.
- **Import XES** accepts original `.xes`/`.xes.gz` files. Read the source license first. Leave the synthetic checkbox unchecked only for a genuine source; that checkbox alone does not certify provenance. Original bytes are retained and analytical targets are new files.

The real-file default remains `data/analytics/bpi2019.duckdb`; a failed genuine-data import never silently switches to a fixture. Existing database paths can be selected in **Advanced / Developer Tools**. Large logs should use streaming CLI ingestion because browser upload buffers have Streamlit memory/file-size limits.

You can also prepare the portfolio demo explicitly:

```sh
uv run --frozen insightpilot portfolio-demo data/portfolio-demo-v2
```

Do not repeat that command on an existing target; use the sidebar to reopen it. The original CLI `demo` command is unchanged.

## The four business pages

### Executive Overview

Start with item volume, median/P90 observed span and repeat-item rate. Document, event and repeated-item counts remain visible in the selection/summary. Monthly volume, duration trends, duration bands, flow comparisons and sequence-review signals are computed from the selected items. The executive summary reports executed metrics; it does not infer inefficiency. Recommendations show evidence, a possible explanation, an operational next step, expected direction, a measurement plan and limitations. The Power BI companion is downloadable without exposing report code.

### Process Explorer

Inspect the source/destination Sankey, variant frequencies, waiting bands, activity counts, repetitions and flow differences. Link widths represent occurrences; the two-layer Sankey is not an end-to-end BPMN model. Untimed edges are retained, with unknown intervals labeled as unavailable. The separate PM4Py action discovers a graph/Petri-net summary, including cycles. Up to 50 edges are drawn; large selections disclose the discovery bound. Full variant sequences and ordered item events remain accessible. Select an item or look up an exact ID; all active filters still apply.

### Root-Cause Investigation

Compare process types, companies, vendors or spend classes. The heading is an investigation lens, not a causal claim. Use Group A/B for side-by-side metrics. **Drag a box across a duration bar** using the chart selection tool to inspect the underlying group. If multiple bars are selected, the first drives details. Without a chart selection, Group A supplies the drill-down. Case lookups remain inside both the global cohort and this group. Rankings require an adjustable minimum group size and prioritize above-cohort-median groups by observed median span. This is a transparent review heuristic, not estimated savings or causal impact.

### AI Business Analyst

**Guided Analyst** is available without credentials and is explicitly not an LLM. It answers supported questions by executing controlled SQL. Use the suggested investigations or type a question. Business answers and relevant charts appear first; expand **Evidence & methodology** for SQL, parameters, source hash and limitations. Supported investigations include management summary, longest flows, waiting intervals, repeated activities, two-flow comparison and management priorities.

Optional **Live LLM** retains the existing tool-calling integration, requires secure API configuration and explicit transmission consent, and can only execute curated investigations inside the current cohort. Mocked tool protocol tests are distinct from live-provider acceptance. A filter change starts a fresh visible investigation; earlier answers/audits remain available in the developer archive with their original filters.

## Filters and analytical definitions

Process type, first-recorded-date range and repetition are in the sidebar. **More investigation filters** adds company, vendor, spend, item-containing-activity and observed-span ranges. Empty multiselects mean all values; **Not recorded** isolates null attributes. Dates include both boundaries in UTC and select whole items: later events outside the start-date window remain in the history. An activity filter selects items containing any chosen activity, not only those events. Date/span filters exclude observations without usable dates. Reset returns to the full cohort; active filters and selected record counts are always visible.

Observed span is last minus first recorded timestamp in calendar days. It is not a verified completed-case cycle time. Repeat-item rate is the share of items with excess non-null activity occurrences; multiple receipts/invoices can be legitimate. Consecutive-event intervals combine waiting, service and calendar effects. Invoice-before-receipt signals require matching-flow interpretation and are never automatically called violations. Group differences are associations with possible confounding and censoring.

## Technical capabilities are preserved

The collapsed **Advanced / Developer Tools** section keeps all existing functionality:

- Twenty verified SQL questions; filtered custom SQL uses selected_* relations. Explicit Whole database mode remains read-only and bypasses dashboard filters. Arbitrary filesystem/network tools, mutation and table-function scope escapes are blocked.
- SQL/JSON evidence, source identity, quality report and cohort evidence downloads; full-source integrity checks deliberately remain separate from filtered KPIs.
- PM4Py and chart-selection diagnostics, plus archived investigations with recorded filter contexts.
- Original PBIP/PBIR/TMDL/DAX artifacts, project ZIP, measure proposals and explicit approval before exporting a proposal. No report files are changed by the assistant.
- Test commands, query diagnostics and secure optional provider variable names. No credentials belong in chat or Git.

## Genuine-data dependency and manual import

The official DOI is https://doi.org/10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1 and its resolver-confirmed file-repository page is https://data.4tu.nl/articles/_/12715853/1. The latest cloud attempts still return upstream HTTP 503; this retry of TU/e also returned HTTP 403. Previously retrieved official source-description facts are retained in `docs/official-source-verification.json`. The original file and license have not been retrieved, so genuine-data findings remain incomplete.

On your Mac, open the DOI/4TU record, confirm **BPI Challenge 2019**, read and retain its license/documentation and download the **original XES** (or XES.gz, extracting ZIP if supplied). Copy it unchanged to `data/raw/`. Use the actual filename, not an invented binary URL:

```sh
mkdir -p data/raw data/analytics outputs
SOURCE='data/raw/your-actual-original-filename.xes.gz'
uv run --frozen insightpilot ingest "$SOURCE" data/analytics/bpi2019.duckdb
uv run --frozen insightpilot validate-source "$SOURCE" data/analytics/bpi2019.duckdb
uv run --frozen insightpilot analyze data/analytics/bpi2019.duckdb outputs/real-analysis
```

Set SOURCE to `.xes` instead for an uncompressed original. Review count/schema/quality differences before findings. Restart the dashboard with that database selected. Source hashes prove byte identity, not publisher authenticity. See `docs/download-and-run.md` for full provenance and Windows companion steps.

## Reproduce checks

```sh
uv run --frozen --extra test pytest -q
uv run --frozen --extra test python powerbi/validate.py
```

Source integrity, independent reference answers, chart values, shared filters, empty/error states, drill-downs, guided questions and technical restrictions are tested. Browser acceptance verifies rendered charts, navigation, filtered KPIs, group-to-case selection, guided answers and absence of raw technical output by default. Synthetic checks do not validate the original BPI log. Windows Desktop TMDL parsing, data refresh, DAX reconciliation and visual acceptance remain separate and unverified.

### Optional browser regression

Playwright is not needed to use the dashboard. For a developer browser check, install Playwright/Chromium, start the app with the portfolio-v2 database, then run `scripts/browser_smoke.py --url <your-local-server-url>`. A system browser can be selected with `--chromium <path>`. Results and screenshots are written under `outputs/browser-check/`. The committed acceptance result and four screenshots are in `docs/browser-verification.json` and `docs/screenshots/`.
