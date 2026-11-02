# InsightPilot — Enterprise Process Intelligence

Portfolio case study for procurement business analysis using the **BPI Challenge 2019** public anonymized event log. The stakeholder scenario is hypothetical; no consulting engagement, stakeholder interview, approval, saving, or implementation outcome is claimed.

**Status: working software milestone, not a completed real-data case study.** The official TU/e description and DOI resolver are now verified. The original log remains inaccessible because its 4TU landing page returns upstream HTTP 503 connection timeouts; original-file/license inspection and real-data findings remain incomplete. Tests use explicitly synthetic records. Power BI JSON components are generated for structural validation; Windows Desktop import, visual review and DAX reconciliation remain outstanding. Start with the [dashboard opening and usage guide](docs/dashboard-guide.md). See [validation](docs/validation.md), [exact download and Mac commands](docs/download-and-run.md), and [live AI acceptance](docs/ai-acceptance.md).

## Run locally (Apple Silicon or Linux)

Use Python 3.11+ and [uv](https://docs.astral.sh/uv/). Python 3.12 is tested in this cloud instance.

```sh
cd DA_project
uv sync --frozen --extra test
uv run --frozen --extra test pytest -q
```

No API credential is needed for ingestion, SQL, process discovery, report export, or predefined investigations. Local package installation preserves TLS verification. `uv.lock` pins resolved packages; the full runtime is not claimed tested on macOS.

## Obtain the real dataset

1. Open the [official TU/e record](https://research.tue.nl/en/datasets/bpi-challenge-2019/) and [DOI](https://doi.org/10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1).
2. Follow the DOI to the official 4TU record. Read its documentation, license, field definitions and download terms before use. Record the exact filename, version and license in your analysis notes. The source description and attribute names are verified; the original artifact filename/version and license remain **unverified** in this environment. This repository does not redistribute the data.
3. Download the official XES artifact to `data/raw/`. The importer accepts `.xes` or `.xes.gz`. Extract an archive locally if the source supplies another archive format. Do not convert to CSV or substitute another dataset.
4. Run the following with the actual downloaded filename. The filename below is a local naming convention, not a verified source artifact name.

```sh
uv run insightpilot ingest data/raw/BPI_Challenge_2019.xes data/analytics/bpi2019.duckdb
uv run insightpilot validate-source data/raw/BPI_Challenge_2019.xes data/analytics/bpi2019.duckdb
uv run insightpilot quality data/analytics/bpi2019.duckdb
uv run insightpilot analyze data/analytics/bpi2019.duckdb outputs/real-analysis
uv run insightpilot discover data/analytics/bpi2019.duckdb
uv run insightpilot questions data/analytics/bpi2019.duckdb
uv run insightpilot export data/analytics/bpi2019.duckdb data/exports
uv run streamlit run app.py --server.headless true
```

The database target must not exist. Use a new versioned target when reimporting; never overwrite the raw source. JSON case-field overrides are supported by `--mapping file.json`, for example `{"purchase_document":"Purchasing Document"}`. The initial field mapping is provisional until checked against official documentation. Never infer purchase documents by splitting case identifiers.

Counts reported by the source description (76,349 documents; 251,734 items; 1,595,923 events; 42 activities; 627 resources) are comparison targets, not our findings. A mismatch is retained in the report and requires investigation. Setting `--synthetic` labels a fixture; omitting it does not certify official provenance.

## Investigate

```sh
uv run insightpilot ask data/analytics/bpi2019.duckdb categories
```

Twenty predefined analytical questions execute real SQL. The UI exposes SQL and source hash. Custom SQL has a SELECT-only gate, read-only DuckDB connection, disabled external access, a row limit and a ten-second interrupt. The LLM has no shell tool or report-write tool. Local users who can edit code or the database remain outside this application security boundary.

Optional LLM mode uses an OpenAI-compatible `/chat/completions` tool-calling endpoint. Set `INSIGHTPILOT_LLM_API_KEY`, `INSIGHTPILOT_LLM_MODEL`, and optionally `INSIGHTPILOT_LLM_BASE_URL` in your process or secure environment settings. Never commit them. A ChatGPT subscription does not supply API credits. The UI requires consent before sending schema/results to the provider. Query execution is enforced; numerical fidelity of arbitrary LLM prose is **not** automatically guaranteed. Review the evidence. Live provider access has not been tested.

## Project layout

- `src/insightpilot`: streaming importer, analytical SQL, CLI, PM4Py discovery, controlled agent
- `tests`: synthetic edge cases, hand-calculated references, app and mocked provider tests
- `powerbi`: generated PBIP/PBIR/TMDL candidate and official-schema validation
- `docs`: business analysis, data dictionary, portfolio narrative and validation evidence

Use the existing checkout in this isolated cloud task. Do not create a worktree unless explicitly requested. No raw data, credentials, databases, virtual environments or generated analysis exports belong in Git.

## Latest application additions

The existing app now provides charts, parameterized category investigation, persisted SQL evidence/chat history, data-quality and query downloads, a process-map rendering, and Power BI project ZIP export. An explicitly synthetic `insightpilot demo data/demo` command enables local demonstration without silently substituting data. See the download/run guide for the environment variable selecting it. `validate-source` re-reads the original bytes and independently checks ten quantities; `analyze` creates a source-labeled decision brief and complete SQL evidence bundle.

## Interactive dashboard update

All dashboard KPIs, charts, timelines and curated questions now share parameterized item filters (category, vendor, spend, first-event dates, span and repeats). Source-level integrity counts remain separate. The filtered SQL editor enforces selected_* relations; database-wide mode is an explicit choice. A guided SQL analyst works without credentials and is labeled as a non-LLM tool. Optional live-LLM tools are restricted to the same cohort. The sidebar supports original XES uploads and an explicit synthetic-demo action. New imports materialize derived tables once; older databases can use `insightpilot prepare <database>`. See the dashboard guide for scope semantics and exact browser instructions.
