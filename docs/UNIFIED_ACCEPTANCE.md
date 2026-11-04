# Unified InsightPilot: executed acceptance

The working product now has one navigation, catalog, semantic review, analysis
context, SQL Lab and reporting pathway. This is a validated local engineering
release, not a claim of production adoption or universal automatic interpretation.

Base: `156a1169fb982fd3af6de35f52a5fb31abf31c0a`, on
`codex/insightpilot-next`. New work: local `codex/unified-insightpilot`.
No new push, merge, deployment or environment publication was performed.
The exact delivery commit is recorded in the Git bundle's branch reference and
in the final delivery receipt. Existing data, reports and source files remain.

## Before and after

| Before | After |
| --- | --- |
| Independent procurement, complaints, mapped-upload and universal front doors | One InsightPilot; empty launch opens Data Hub; active data opens Overview |
| Separate context, arbitrary first timestamp/category, generic count rankings | Reviewed date purposes; explicit date basis, measure and comparison; approved analytical intent persists between sessions |
| Limited aggregate formulas; no universal SQL workbench | Quantiles, conditional sums, row-derived arithmetic, weighted averages and guarded ratios; guided and highlighted read-only SQL |
| Page-level exports and manifests | Saved queries/views/chart presets/evidence blocks, report assembly, replay, successive downloads and bounded CSV/XLSX/Power Query preparation |
| Legacy XES path tied to earlier presentation | Structural cases/events in the same catalog, generic event analysis, original parser/source retained |

[Migration inventory](UNIFICATION_MIGRATION.md) gives implementations, decisions
and replacement tests. [Architecture](UNIFIED_ARCHITECTURE.md) describes trust
boundaries and deliberate compatibility limits.

## Executed cross-domain matrix

| Authentic input and shape | Import / reviewed semantics | Calculations independently reconciled | Charts / drill-through / delivery | Limits |
| --- | --- | --- | --- | --- |
| NYC TLC sample via seaborn-data; transactional trips | 6,433 CSV rows retained; pickup and drop-off meanings distinguished; pickup selected; USD charges; repeated riders not identifiable | Yellow subset: 5,451 rows, USD **102,938.06**; daily sums; seven-day-window change **+1,129.33**; Pearson correlation; NumPy histogram; exploratory three-cluster segmentation | Daily line, payment bars, charge histogram; genuine pointer selection; guided and edited SQL; AI template; HTML report | Mirror sample selection/modifications not fully specified; not city-wide revenue, profit or representative demand |
| R `airquality`; daily environmental observations | 153 original CSV rows retained; derived Parquet adds documented constant year 1973, then reversible date assembly; ozone units ppb; 37 missing ozone values retained | Mean **42.12931034482759 ppb**, 116 finite measurements; monthly means; NumPy bins; t interval; May/August Welch p **0.000216856559…** matches SciPy | Monthly line, month bars, histogram; pointer drill-through; SQL, report/replay and browser statistical execution | Stations differ by measurement; serial independence not verified; no city-wide exposure or causal claim; forecast correctly refused for insufficient monthly history |
| `nycflights13`; relational operational workspace | 336,776 CSV records, 16 airline rows and 3,322 aircraft rows; NA markers explicitly mapped; departure date assembled; scheduled hour distinguished from outcome time | Mean recorded arrival delay **6.89537675731489 minutes**; monthly values; full 16-operator join; 36 manufacturer groups retain all 336,776 fact rows and **52,606 unmatched equipment keys**; means match independent Pandas merges; NumPy bins | Monthly line, operator bars, histogram; actual pointer drill-through; guided/edited SQL; statistical execution; joined evidence report | Missing arrival delay excluded; flight number is not a unique event ID; negative delay means early; no cancellation-rate inference |
| Held-out R `esoph`; aggregated outcomes/segments | Selected after generic engine and initial reference workflows; 88 rows converted losslessly to JSONL and uploaded through browser; all six roles and row grain approved through ordinary controls | **200 / (200 + 775) = 0.205128205128…**; all six age-group ratios match independent original-CSV calculations; same SQL aggregate, overview and report replay | Rate bars and count distribution; real pointer selection; actual Ace SQL editing; deterministic answer; successive HTML/manifest downloads and replay | Case-control sample composition, **not population incidence, individual risk or a causal alcohol effect**; no date guessed; time analysis explicitly gated |

The first three imports are reproducible service/API acquisitions followed by real
browser workflows. The held-out import, semantic review and metric creation are
performed through the visible UI. No held-out mappings, recognized filenames,
field-specific runtime branches or developer chart presets were installed.
The app labels that upload as `user_upload`; it does not automatically assert
source authenticity. The independent source receipt links original CSV and derived
JSONL hashes.

Raw source bytes remain under ignored `data/` and workspace `sources/` folders.
Sources are public educational extracts, not newly acquired primary publisher
records. Exact source revisions, SHA-256, licenses/usage caveats, citations and row
counts are recorded in [initial evaluations](unified-initial-evaluation.json) and
[held-out source receipt](unified-heldout-source.json). R dataset licenses are
GPL-2/GPL-3; nycflights13 is CC0; the TLC sample follows public-use source terms,
with no separate dataset license asserted by its mirror. Raw datasets are not
redistributed in this Git change.

## Generalization, security and consistency

The full regression suite passes **294 tests**, with zero failures/errors/skips.
The initial baseline passed 271 tests with 12 inherited pandas warnings. Obsolete
industry-UI assertions were deliberately replaced, while ingestion, analytical,
security, reconciliation, CLI and Power BI backend tests remain. The explicit
[retirement receipt](retired-ui-tests.json) documents the change; test count alone
is not the acceptance argument.

The cross-cutting contract includes neutral file/column names, column reordering,
category relabeling, sparse values, duplicates, ambiguous slash dates, CSV/TSV/JSONL
variants, reviewed role overrides, zero denominators, incompatible units,
immutable-version replay and undo. Core metric fixtures independently check
counts, distinct counts, sums, means, medians, quantiles, conditional metrics,
weighted averages and derived expressions. Original unknown values are retained;
users must explicitly define eligible denominator categories.

SQL negative tests reject mutations, multiple statements, COPY/ATTACH/INSTALL/LOAD,
file/network readers, secrets/settings functions, raw/internal catalogs, other
workspaces, table functions, recursive/forward/shadowed CTEs, unapproved/self/CROSS
joins and row multiplication. Positive tests cover SELECT/CTE, windows, EXPLAIN,
paging, saved-context reopening and validated left joins with unmatched records.
Connection read-only, external-access, memory, row and interruption safeguards
are independent of AST validation. All important outputs expose source/version,
SQL, parameters, filters, definitions and context-bound evidence IDs.

The independent references use Pandas/NumPy/SciPy and original source files,
without reusing the production expression compiler or parser. Histogram reference
checks use NumPy bins plus the documented separate exact-maximum boundary bin.
Group sizes, denominators and missing coverage remain explicit. Decomposition is
accounting; clustering is exploratory; statistical independence and sampling
representativeness are not asserted.

The [preselection engine receipt](unified-engine-freeze.json) predates held-out
selection. Subsequent generic defects found during testing are disclosed in
[postselection fixes](unified-postselection-fixes.json), including scalar ratio
components, NULL drill identity, persistent exports/defaults, units and structural
XES projection. This was an integration holdout, not a claim that every source file
remained byte-identical after selection. Repeating the published source is now a
regression run, not a new blind evaluation.

## Browser, accessibility and packaging

[Initial browser receipt](unified-browser.json) and
[held-out browser receipt](unified-heldout-evaluation.json) record real Linux
Chromium executions, not screenshots of mocked data. All four datasets complete
metric selection, chart rendering, actual pointer drill-through, SQL editing,
zero-token answers and report download. The first three also execute a real
mean-interval UI analysis. The held-out run reviews fields and creates its metric
through the UI, verifies unsupported time analysis, downloads both report and
manifest, then replays the manifest. JavaScript page-error lists are empty.

Desktop 1512px and tablet 768px views were checked; no horizontal document overflow
was observed. Keyboard focus and labeled controls were exercised, including native
keyboard checkbox interaction and a plain-text SQL alternative. This is a basic
accessibility check, not a full WCAG or screen-reader certification.

Representative actual captures:

- [Transaction overview](screenshots/unified/transactions-overview.png)
- [Environmental overview](screenshots/unified/time-observations-overview.png)
- [Relational overview](screenshots/unified/relational-overview.png)
- [Held-out overview](screenshots/unified/heldout-overview.png)
- [Held-out SQL editor](screenshots/unified/heldout-sql-editor.png)

Source distribution and wheel build successfully. The source package explicitly
includes `app.py`, the lockfile and Streamlit configuration. Installation/import
checks and final artifact hashes are recorded in `unified-packaging.json`.
Original parser/CLI Power BI assets remain compatible; generic Reports preparation
exports active records, definitions and a Power Query template. No native Windows
Power BI visual validation is claimed.

## Scale comparison

Same Linux synthetic protocol, fresh process per size, two DuckDB threads and
768 MB connection budget. Generated CSV files may remain in OS cache; filesystem
caches were not cleared. Values below are single-run observations, not statistical
performance claims or native Mac results.

| Rows | Import before → after | Full profile before → after | Overview calculations before → after | Peak process RSS before → after |
| --- | --- | --- | --- | --- |
| 10,000 | 0.0855 → 0.0726 s | 0.0909 → 0.0709 s | 0.0610 → 0.0499 s | 174.8 → 113.1 MiB |
| 100,000 | 0.2438 → 0.2382 s | 0.3471 → 0.2992 s | 0.1161 → 0.1591 s | 194.5 → 135.7 MiB |
| 1,000,000 | 1.2878 → 1.4298 s | 1.7792 → 1.5549 s | 0.7768 → 0.6320 s | 323.2 → 281.6 MiB |

The million-row import was 11% slower; the 100k overview was 43ms slower. Both are
reported rather than hidden. Profiling, million-row overview and memory improved
in this observation; cache/process/concurrent-load variability prevents attributing
those differences solely to the refactor. Aggregate reference sums still match.
[Before](next-generation-benchmarks.json) · [After](unified-benchmarks.json).

Limits: imports 512 MiB/5m rows/150 columns; Excel 100 MiB/500k rows; paginated
records; SQL 5,000 rows/page, default 10-second interruption and conservative
function/join allowlists; selection ZIP exports at most 100k rows. No silent
sampling or truncated full-export claim is made.

## Sixty-second walkthrough

This example was chosen after the general workflow: download the pinned held-out
`esoph` CSV from its source receipt, or use the lossless JSONL prepared by the
reproduction script. Timings describe a short walkthrough, not a measured first
user usability study.

1. Data Hub: upload the file; confirm 88 imported strata and retained source hash.
2. Quality & Preparation: review count fields as quantities, three segment bands
   as categories, and the row grain as an aggregated stratum. Leave date basis
   unselected. The browser receipt demonstrates the full ordinary review.
3. Define **Sample case share** as
   `sum("ncases") / (sum("ncases") + sum("ncontrols"))`, units **proportion (0–1)**.
   Describe case-control sampling and the eligible denominator.
4. Overview: select age-group comparison; inspect 200/975 and the sampling caveat.
   In Explore, box-select a bar to inspect contributing strata.
5. SQL Lab: run the guided ratio, edit the SELECT if desired, inspect its plan,
   and save executed results to Reports. Download HTML and its replay manifest.

A new analyst's first full semantic review will take longer than a minute. No
business meaning is automatically inferred from this filename or its column names.

## Honest remaining scope

No native macOS acceptance, Apple Silicon benchmark, live paid-provider test,
production PostgreSQL/API acceptance, full screen-reader audit, native PDF engine,
map workflow or native Power BI Desktop/PBIX validation was performed. Forecasts
remain guarded baselines with empirical holdout errors, not calibrated production
forecasts. AI is finite planning, not arbitrary conversational analysis.

SQL deliberately restricts joins to a direct fact and one validated unique-key
dimension; arbitrary chain/composite/subquery joins are not supported. Unit checks
catch explicit incompatible addition/subtraction, not every possible dimensional
mistake. Automatic semantics remain conservative; users review definitions.
Structural XES cases retain complete attributes as JSON text without assuming a
domain; flattening arbitrary nested attributes requires explicit preparation.
Existing normalized parser DBs remain available read-only through compatibility.

Saved views/replay validate local retained versions; portability still requires the
source/workspace. CSV/XLSX exports carry formula-safe text, which adds an apostrophe
to formula-like strings. Sharing an evidence block can share source records; all
saved outputs remain local until deliberately shared.

## Mac and synchronization

See the exact [Mac commands in README](../README.md#run-on-your-mac). Import the
provided local Git bundle into the clone, switch `codex/unified-insightpilot`, run
`uv sync --locked --extra test`, then
`uv run streamlit run app.py --server.address 127.0.0.1` and open
`http://localhost:8501`. Downloaded bundle paths use your Mac's `~/Downloads`.
The old remote branch alone does not contain this local refactor. No public push
was performed. Source ZIP is also supplied for running without importing Git history.
