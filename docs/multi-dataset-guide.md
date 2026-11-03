# Install, launch and use the dataset workspaces

## Mac installation and update

Use Terminal on the Mac. No Codex publication, AI key or Windows installation is needed to run the application.

1. Install Git if missing: run `xcode-select --install` and finish Apple’s installer. Install uv with `brew install uv` if you use Homebrew. Otherwise follow the official [uv standalone installer](https://docs.astral.sh/uv/getting-started/installation/):

   ```sh
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

   Open a new Terminal window so `uv` is on PATH.

2. Go to your **existing** DA_project checkout. Do not clone over it. If this is the Mac’s first checkout only:

   ```sh
   cd ~
   git clone https://github.com/alimammadzada23/DA_project.git
   cd DA_project
   ```

3. Stop any running dashboard with Control-C in its Terminal. From the repository directory:

   ```sh
   git fetch origin
   git switch feature/insightpilot
   git pull --ff-only
   uv python install 3.12
   uv sync --frozen --extra test --python 3.12
   uv run --frozen --extra test pytest -q
   uv run --frozen streamlit run app.py --server.port 8501 --server.headless false
   ```

4. Keep that Terminal running. Streamlit opens a browser on **your Mac**. If it does not, run `open http://localhost:8501` in another Mac Terminal. This is a local Mac address, not a cloud preview. If the port is occupied, stop the old app or use `--server.port 8502` and open the corresponding local address.

Python 3.12 is selected because the locked pipeline was tested with it. uv installs an Apple Silicon build appropriate to the Mac. Native Mac execution has not been observed in this Linux task; report the complete error if installation fails. Test evidence here covers Linux.

## Select a dataset

Fresh launches open **InsightPilot — AI Business Intelligence**, with three workspace cards. You can always use **Dataset workspace** in the sidebar. An explicit legacy `INSIGHTPILOT_DATABASE` setting opens procurement directly, preserving previous launch configurations.

- **Procurement Intelligence:** all original four business pages remain. Use Data & import for original XES/XES.gz or the two explicitly synthetic demonstrations. The seeded portfolio has 1,200 items / 4,750 events; those are software examples. Existing SQL, mining and Power BI controls are retained. See [procurement guide](dashboard-guide.md).
- **Customer Experience & Risk Intelligence:** load an official CFPB period extract, choose a snapshot, then use Executive Overview, Company Comparison, Root-Cause Investigation or AI Business Analyst.
- **Analyze My Dataset:** inspect a CSV, .xlsx or flat Parquet file, review schema/sample/quality, choose an analytical profile, approve or change suggested roles, and create an isolated workspace. XES imports reuse the existing event-log engine.

Every active dataset displays its name, source category, record count and snapshot identifier. Official connector receipts, unverified uploads and synthetic references are separate categories. Nothing is silently replaced with a sample when a source is unavailable.

## Genuine CFPB download

The cloud’s actual official website/API attempt returned proxy HTTP 403. The public endpoint needs no API key. On the Mac, stop the server or use another Terminal in the repository:

```sh
uv run --frozen insightpilot cfpb-download data/cfpb/2026-09-01-week --start 2026-09-01 --end 2026-09-08
uv run --frozen insightpilot cfpb-validate data/cfpb/2026-09-01-week/complaints.csv data/cfpb/2026-09-01-week/analytics.duckdb
uv run --frozen insightpilot cfpb-analyze data/cfpb/2026-09-01-week/analytics.duckdb outputs/cfpb-report
```

Use a new, empty output directory per download. Select a past 1–31-day period with a publication buffer. A shorter extract is appropriate if the server’s export limit, our 200 MiB/500,000-row cap, or a timeout is reached. Read `download-failure.json` for the actual cause. Do not repeatedly retry a known outage.

The connector preserves the source CSV unchanged and a receipt, validates headers, verifies CSV rows against an exact API hit count, and records API freshness/indexing metadata. The documented upper boundary and deployed search implementation need empirical verification, so local ingestion enforces **start inclusive, end exclusive** and counts any excluded end-date rows. A changing count, unexpected date/schema or publisher indexing issue fails the download rather than claiming a complete snapshot.

If official download fails on the Mac:

1. Open https://www.consumerfinance.gov/data-research/consumer-complaints/ in your browser.
2. Open the complaint search view, set a past received-date range, inspect active filters and choose export/download CSV for all matching complaints.
3. Preserve the original CSV and note the retrieval date, URL, chosen dates, filters, export count and publisher usage/limitations. Do not substitute another website’s sample.
4. In the complaint workspace, use **CFPB CSV export → Import complaint CSV**. Leave the synthetic checkbox clear only for a genuine source file. The app still labels a manual file user supplied because it cannot authenticate your browser download.
5. Inspect the manifest and quality report, then run `cfpb-validate` and `cfpb-analyze` with the imported file/database paths shown in the manifest. Compare row counts and dates to your export settings.

In the cloud, a narrowly scoped draft adds `www.consumerfinance.gov`, `consumerfinance.gov`, `files.consumerfinance.gov` and `cfpb.github.io` to the existing network policy. Saving that draft does not apply it; review/save in environment settings and publish would be needed for cloud access. This does not prevent local Mac use, and publication is not a product dependency.

## Complaint dashboard

All views share company, product, issue, state, response, timely and inclusive record-date filters. Reset dataset filters restores that snapshot’s whole cohort. Missing-date rows are included in totals and excluded from trends; an active date filter excludes them from the cohort.

- The executive cards show complaints, named companies, timely share and known-response coverage. Timely share is **Yes / (Yes + No)**, not Yes / all complaints. Unknown does not become No or zero.
- The donut includes the top five products and a computed Other, across the complete selection. Hover shows counts and the denominator. Volume distributions describe complaints per company, not complaint incidence per customer.
- Company comparison selects two companies already present in the active cohort. Compare reported counts, product mix, date trends and known-response denominators. Counts do not prove one company has worse service.
- Investigation uses a bounded, complete company/product grid and two equally long adjacent periods. An unmatched final day is disclosed and excluded. A zero earlier count makes relative growth undefined. Changes are descriptive, without significance or causal claims.
- Drag a box over an issue/company bar to populate corresponding records. The record selector is an accessible alternative. Pagination and CSV export show at most 100 matching rows per page and intersect all active filters.
- The Plotly toolbar exports PNG; **Export this chart** exports a standalone interactive HTML file. **Download one-page management summary** exports the selected cohort’s observations and limitations. Raw query details live under **View analytical evidence** or **Advanced / Developer Tools**.

## Upload and mapping rules

Uploads are local, bounded at 100 MiB / 500,000 rows / 150 columns. `.xlsx` reads a chosen sheet; blank selects the first. CSV is UTF-8. Nested Parquet must be flattened first. No Excel macros or formulas are executed. Files and databases are ignored by Git.

Mappings are suggestions until you approve them. Each column has at most one role. ISO dates and an explicit month/day/year or day/month/year convention are supported; ambiguous locales are not guessed. Invalid mapped dates/numbers remain missing and their counts are reported. Exact duplicate rows are retained because their business meaning is unknown.

| Profile | Required/optional roles | Enabled behavior |
|---|---|---|
| Descriptive | Optional date, category/product/company, numeric measure | Only capabilities supported by approved fields; absent KPIs disabled with an explanation |
| Transactions | Product/customer/date optional; measure or quantity + price for revenue | Record trends, mapped measure distribution, product/customer counts; revenue requires explicit semantic confirmation, with units supplied by the user |
| Complaints | Company, product, issue, date; explicitly one row per complaint | Complaint views on mapped fields; unknown response values excluded from timeliness; uploaded rows receive technical IDs, without claiming genuine CFPB identities |
| Process event log | Case ID, activity, ISO timestamp; resource optional | Full-case process variants, spans, repetitions, waits, flow and case timelines; offset-free timestamps require explicit UTC confirmation |

Text-only or unmapped datasets remain usable for record inspection/quality; no revenue, time or procurement KPI is invented. Transaction fields named “amount” are not automatically revenue or profit. Process spans are not verified cycle times. Source authenticity and rights for private uploads remain the user’s responsibility.

## Guided and optional live AI

Guided SQL requires no key. Use a suggested question or ask about supported metrics. Complaint examples include company volumes, product shares, daily change, selected-company categories, increasing issues and management priorities. Uploaded profiles expose only compatible investigations; mapped process logs support case counts, spans, variants, repeats and waits.

Responses cite executed evidence, source hash, dataset ID and filters. Unsupported causes, company-quality claims, ROI and savings are declined. The guided router is deterministic SQL, not an LLM.

Live mode is optional. Configure `INSIGHTPILOT_LLM_API_KEY`, `INSIGHTPILOT_LLM_MODEL` and optionally an HTTPS `INSIGHTPILOT_LLM_BASE_URL` securely in the process. Never commit keys or paste them into chat. Explicitly authorize transmitting the active dataset’s schema/results/question before using it. Consent and current answers are scoped to the dataset selection. The live tool can invoke only the active module’s fixed analytical catalog, with read-only SQL, limits and audit logs. Live-provider execution and arbitrary narrative accuracy have not been validated in this task; mocked protocol tests do not establish that.

## Power BI and reproducible evidence

The original procurement PBIP remains intact. The complaint export contains Parquet, manifest/hash, independent CSV/SQL validation, query evidence, a management summary and case study. See [Power BI complaint design and Windows acceptance](../powerbi/complaints/README.md).

Run the full automated suite with `uv run --frozen --extra test pytest -q`. Optional actual-browser acceptance scripts are under `scripts/browser_workspaces.py`, `browser_uploads.py` and the preserved `browser_smoke.py`. Prepare their **synthetic QA sources only** using `uv run --frozen python scripts/prepare_browser_fixtures.py`, then start a server with its printed environment settings. Playwright/Chromium are optional QA dependencies, not application requirements. The browser evidence does not validate genuine CFPB/BPI findings, native Mac behavior, live LLM or Windows Desktop.
