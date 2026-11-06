# InsightPilot command-center redesign

Implemented locally from `codex/insightpilot-local-ai` at
`6824394c5233a41ea56f4beec2dc77a3c96f8436`, on `codex/insightpilot-redesign`.
No push, merge, deployment, model replacement, or user-data migration was performed.

The [persistent-wheel follow-up](PERSISTENT_WHEEL.md) keeps the wheel visible on
analytical pages and replaces the original home heading with **Data Analysis**.
Its screenshots show the current navigation; the gallery below records the initial
redesign.

The [explicit project-saving follow-up](PROJECT_SAVING.md) now separates temporary
work from Saved Projects. Only Save Project / Save Changes publishes a snapshot;
older galleries below are historical.

## Run and verify

From the repository root, with Python 3.11+ (validated here on Python 3.12):

```sh
python3 -m pip install -e '.[test,browser]'
python3 -m streamlit run app.py
```

The app starts with a temporary project. Existing saved/legacy projects are listed
for explicit opening. Validation fixtures are never injected into the user catalog.
Import data, analyze and prepare reports freely; use Save Project to retain the
project or Save Changes to commit later edits. See the saving guide for lifecycle
and refresh behavior. Business metrics still
require their normal semantic/units checks. Deterministic analysis requires no model.

```sh
python3 -m pytest -q
python3 scripts/browser_redesign.py --output outputs/my-redesign-check
```

The browser check requires Chromium at `/usr/bin/chromium`. It starts and stops an
isolated loopback test server and generates an explicitly synthetic fixture. Its
output directory must not already exist. It does not run against existing user
projects. Browser results and original screenshots are retained in
`outputs/redesign-verified`; representative screenshots are committed below.

## What changed

- Exactly ten destinations remain: Workspace Home, Data Hub, Quality & Preparation,
  Explore, Investigate, Reports, Overview, AI Analyst, SQL Lab, Advanced.
- A dependency-free local SVG component draws ten equal 36-degree annular sectors.
  Every sector sends a consumed-once routing event to Python session state. Native
  navigation and quick actions cannot be overridden by a stale component event.
- Primary workflow stages use turquoise icons; supporting tools use lavender icons.
  Hover, selected, and keyboard-focus states are distinct. Arrow keys, Home/End,
  Enter and Space are supported; the center identifies InsightPilot.
- The full wheel occupies the desktop home hero. Other desktop pages have a
  persistent compact wheel in a reserved side rail, with immediate expansion.
  Smaller screens use a one-tap wheel dock with reserved viewport space.
  Native radio navigation remains available as an accessible alternative. Reduced-motion
  preferences are respected.
- Shared navy, turquoise, typography, spacing, border, surface, focus, table,
  chart, and button styling replaces the previous button-grid appearance. The
  sidebar is reduced to 270px; card layouts wrap at narrow sizes.
- Home presents genuine project counts, active data metadata, consistent quick
  actions, the project collection, and recent saved work. No sample KPI or finding
  is fabricated. Existing unnamed projects receive an explicit naming action,
  without renaming their directories or stable IDs.
- Project name, business purpose, and description are persisted separately.
  The old metadata format remains readable. Search includes all three fields.
  Creation has a single central form; the sidebar links to that flow.
- Quality adds a full-version field-completeness chart. Very wide tables show the
  30 fields with most missing values; the complete catalog remains available.
  Missingness is not represented as proof of inaccuracy.
- Explore organizes recommendations across comparison, distribution, relationship,
  time, and composition, and retains all existing chart modes and record inspection.

## New analytical capabilities

**Composition and Pareto analysis:** calculates nonnegative additive category totals,
shares, cumulative shares, and the number of groups needed to reach 80%, 90%, or 95%.
The denominator includes every selected group before chart truncation. Missing
grouping values remain a separate category; missing aggregate values are unavailable,
not zero. Negative/unavailable totals, zero denominators, nonadditive metrics, and
results exceeding 10,000 groups are rejected with an explanation. The plot displays
at most 20 groups and explicitly discloses that bound. Donuts are available only for
at most eight groups. Saved evidence includes the actual displayed shares, SQL,
source hash, filters, version and method. Pie/donut value exports now retain their
actual labels and values.

**Metric templates:** row count, distinct entities, average, total, quantity times
unit price, and total per distinct entity. Templates feed the existing restricted
aggregate-expression compiler; they do not execute Python, model SQL, or arbitrary
expressions. Analysts still supply definitions, units, denominator policy, and
limitations. Numeric role gates and zero-denominator protection remain intact.

**Comparison improvements:** the existing equal-period contribution calculation now
shows the earlier baseline, later value, absolute change, and relative change where
defined. Results survive chart saving and other reruns. The cache key includes scope,
metric, dimension, and periods; different inputs do not display a stale result.
Unavailable group totals suppress an overall summary. Contribution remains descriptive,
not a causal explanation.

## Reused systems and preservation

Streamlit remains the frontend/backend; DuckDB remains the analytical engine and
Plotly the chart renderer. Existing source retention, schema profiling, original
bytes, reversible preparation/version selection, shared filters, reviewed metric
language, SQL safeguards, saved views, evidence manifests, report drafts/ordering,
HTML/Excel/CSV/Power BI preparation exports, statistical methods, and local
Ollama/Qwen planning architecture are reused. No new charting or JavaScript framework
was added. The component HTML is included in package data for installed builds.

Two baseline validation problems were investigated independently on an untouched
archive of the baseline commit:

1. The legacy stacked complaints chart dropped a missing-company bucket with nullable
   string data. Missing labels are now filled before company selection, preserving
   selected totals.
2. A histogram reference test's default fast CSV parser rounded adjacent IEEE values
   across bin boundaries. Its independent NumPy reference now uses round-trip float
   parsing. The production histogram calculation was unchanged; its exact source
   values agree with the corrected independent reference.

## Automated test results

The complete regression run passed **926 tests**, with no failures or skips. Five
additional UI regressions added afterward also passed: composition defaults and
saved evidence, comparison persistence while saving a chart, consumed-once routing
events, business-purpose search, and metric-template execution through the existing
compiler. This covers **931 unique passing tests** across the two runs. The only
warning was a third-party anyio/pytest deprecation. See
[test results and installed versions](test-verification.json).

## Browser evidence

Actual Chromium interactions verify all ten SVG sectors and active states, arrow-key
and Enter navigation, project creation, browser file upload, rendered group values
against independent pandas counts, cumulative shares `[0.5, 0.75, 0.9, 1.0]`, donut
rendering, evidence saving, shared-filter retention, investigation saving, report
draft creation, and downloading HTML containing the original source SHA-256.

Viewports: **1440 × 900**, **1920 × 1080**, **1280 × 832**, **768 × 1024**, and
**390 × 844**. Narrow route selection was exercised, including retention of the
80-of-160-row selection. No page-level horizontal overflow or browser page errors
were reported. The actual captures were visually inspected; a cramped 768px card
layout was corrected and recaptured. See [machine-readable browser results](browser-verification.json).

All screenshot data is generated software-validation data, not business evidence.

| View | Screenshot |
| --- | --- |
| Complete ten-sector home | [Home](screenshots/01-home-empty-1440.png) |
| Hover | [Wheel hover](screenshots/02-wheel-hover.png) |
| Active sector | [Explore selected](screenshots/02b-wheel-active.png) |
| Named project | [Project home](screenshots/04-project-home.png) |
| Data Hub | [Imported source, metadata and preview](screenshots/03-data-hub.png) |
| Quality | [Field completeness](screenshots/05-quality.png) |
| Explore | [Ranked groups](screenshots/06-explore.png) |
| Pareto | [Concentration](screenshots/07-pareto.png) |
| Composition | [Donut](screenshots/08-composition.png) |
| Investigation | [Question and classified evidence](screenshots/09-investigation.png) |
| Reports | [Builder](screenshots/10-report-builder.png), [narrative and export](screenshots/10b-report-narrative.png) |
| Large desktop | [1920px home](screenshots/11-home-1920.png) |
| MacBook-sized viewport | [1280px home](screenshots/11-home-1280.png) |
| Narrow tablet | [768px home](screenshots/11-home-768.png) |
| Mobile | [390px home](screenshots/11-home-390.png), [Explore](screenshots/12-responsive-explore-390.png) |

## Limits and Streamlit tradeoffs

The wheel is an iframe component; routing causes the normal Streamlit rerun.
Analytical state lives in Python rather than in the iframe. Complex forms, tables,
filters, and editors remain native Streamlit controls with shared styling. The
non-home wheel is compact by default and stays visible during scrolling; expansion
reserves additional content space instead of covering analytical controls. UI
styling targets documented test IDs and some native DOM structures; rerun browser
acceptance after Streamlit upgrades.

A live Ollama/Qwen inference, native macOS/Safari behavior, new statistical-method
reliability, large-production-data performance, external connectors, and a formal
screen-reader audit were not validated in this redesign run. Existing statistical
methods and their applicability gates are retained; no new regression, clustering,
cohort, forecasting, or causal-inference backend is claimed. HTML export and browser
print-to-PDF remain the supported report delivery path; no new native PDF renderer
was added. This is a locally validated implementation, not a claim of production
certification or best-in-world performance.
