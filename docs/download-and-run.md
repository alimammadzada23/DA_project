# Obtain the original log and run InsightPilot on a Mac

## Actual source-access result

On 2026-11-02 the latest retry successfully retrieved the official TU/e description at https://research.tue.nl/p/en/datasets/bpi-challenge-2019 and resolved the DOI through its official Handle API. The resolver identifies **https://data.4tu.nl/articles/_/12715853/1** as the dataset landing page. That exact page and the 4TU API/canonical paths return HTTP 503 with `upstream connect error ... connection timeout` from this instance. Original files/license remain inaccessible here. The successful description confirms the field names and published counts, not our own data findings. See official-source-verification.json. No unofficial mirror or synthetic substitution was used.

## Download in your browser

1. Open **https://doi.org/10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1**. The corresponding publisher reference is **https://research.tue.nl/en/datasets/bpi-challenge-2019/**.
2. Confirm the title is **BPI Challenge 2019** and the DOI matches exactly. The exact resolver-confirmed 4TU page is https://data.4tu.nl/articles/_/12715853/1. Follow its download controls. Read the dataset description, field guide and license/terms before using it; save these alongside the log. The license has not been verified by this project yet, so no license name is asserted here.
3. Download the original **XES event log**, retaining its original compressed file if one is supplied. Do not download a CSV sample, a challenge submission, or another year's log. If a ZIP is supplied, extract it and retain both ZIP and contained XES. This importer accepts XES or gzip-compressed XES, not ZIP directly.
4. In your `DA_project` checkout, create `data/raw`. Copy the downloaded `.xes` or `.xes.gz` file there without modifying its contents. Use its actual filename in the commands below. The application intentionally refuses to overwrite an existing analytical database.
5. Record the exact original filename, source URL, license, version/download date and any publisher-provided checksum in `data/metadata/source-notes.txt`. A SHA-256 is computed by the importer; it proves byte identity for later reconciliation, not official authenticity by itself.

There is no verified direct binary URL in this environment. Inventing one would be misleading; the DOI record is the authoritative download entry point. If it also fails in your browser, the genuine-data work must wait for the publisher or another authorized copy of that exact artifact.

## Install and run on Apple Silicon

Install `uv` using its official installation instructions or `brew install uv` if you already use Homebrew. Open Terminal in the existing repository. Python 3.12 is the tested interpreter; `uv` can provision it:

```sh
cd /path/to/DA_project
uv python install 3.12
uv sync --frozen --extra test --python 3.12
uv run --frozen --extra test pytest -q
uv run --frozen --extra test python powerbi/validate.py
mkdir -p data/raw data/metadata data/analytics outputs
```

For an uncompressed XES file, set the exact path you downloaded:

```sh
SOURCE='data/raw/your-actual-downloaded-filename.xes'
uv run --frozen insightpilot ingest "$SOURCE" data/analytics/bpi2019.duckdb
uv run --frozen insightpilot validate-source "$SOURCE" data/analytics/bpi2019.duckdb > outputs/source-reconciliation.json
uv run --frozen insightpilot quality data/analytics/bpi2019.duckdb > outputs/real-quality.json
uv run --frozen insightpilot analyze data/analytics/bpi2019.duckdb outputs/real-analysis
uv run --frozen insightpilot export data/analytics/bpi2019.duckdb data/exports
uv run --frozen streamlit run app.py --server.headless true
```

For gzip, set `SOURCE` to its actual `.xes.gz` filename instead; no decompression command is necessary. Never use `--synthetic` for the real log. Inspect every published-count discrepancy, missing field and timestamp warning before presenting the report. Mapping overrides are documented in README. `validate-source` independently recomputes ten counts/metrics from the same bytes, including document count, median/P90 span, repeats and variant count. Its success does not resolve source licensing, business semantics or censoring.

The Streamlit terminal displays the local browser address on your Mac. Deterministic questions, custom read-only SQL, process discovery, charts, evidence downloads and Power BI project export work without an API key. These commands have been exercised on Linux; the Mac instructions and locked dependencies are prepared, but native Apple Silicon execution has **not** been verified on an actual Mac.

## Try the functioning application while waiting for the real file

This creates **clearly synthetic data**, with an explicit warning in the UI:

```sh
uv run --frozen insightpilot demo data/demo
uv run --frozen insightpilot validate-source data/demo/SYNTHETIC-demo.xes data/demo/SYNTHETIC-demo.duckdb
uv run --frozen insightpilot analyze data/demo/SYNTHETIC-demo.duckdb outputs/demo-analysis
INSIGHTPILOT_DATABASE=data/demo/SYNTHETIC-demo.duckdb uv run --frozen streamlit run app.py --server.headless true
```

Run `demo` only once per directory; choose a new directory if its files exist. The real database default remains `data/analytics/bpi2019.duckdb`. The demo never replaces genuine data.

## Live AI: credentials required, deterministic analysis remains available

No API credentials are configured in the current cloud instance. The attempted live investigation stopped at the missing-configuration check; it did not call a provider. Supply a provider API key and a tool-calling model you can access. A ChatGPT subscription is not API access.

On your Mac, enter a key interactively, keeping it out of shell history and Git. In zsh:

```sh
read -rs 'INSIGHTPILOT_LLM_API_KEY?Provider API key: '; echo
export INSIGHTPILOT_LLM_API_KEY
export INSIGHTPILOT_LLM_MODEL='your-available-tool-calling-model'
# Optional for a compatible provider; default is https://api.openai.com/v1
# export INSIGHTPILOT_LLM_BASE_URL='https://your-provider.example/v1'
uv run --frozen insightpilot chat data/analytics/bpi2019.duckdb \
  'Which purchase-order flows have the longest observed spans? Show SQL and limitations.' \
  --audit outputs/ai-investigation.json
```

For this cloud session, use secure secret/environment configuration instead of chat. Provider network access must also be available (the default OpenAI endpoint is `api.openai.com`, not currently configured). Do not paste the key into chat. After access is provided, run the question set in `docs/ai-acceptance.md` and inspect each audit. A successful query is required for an answer, but human review is still needed to establish that the narrative faithfully interprets it.

## Windows step

Power BI Desktop runs on Windows, not natively on macOS. Open the exported project on a suitable Windows machine and follow `powerbi/README.md`. A JSON-schema pass does not establish that TMDL parses, Parquet refresh works, DAX returns the expected numbers, or visuals render correctly.

## Use the dashboard without credentials

Follow [dashboard-guide.md](dashboard-guide.md) to open the local browser UI, use all filters and run the guided SQL analyst. Live LLM credentials are optional and do not block these features. For existing pre-update databases, `uv run --frozen insightpilot prepare <database>` materializes the existing analytical views without changing raw cases/events. Reimport to apply new normalization/model semantics.
