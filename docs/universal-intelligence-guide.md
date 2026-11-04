# Universal Intelligence workflow

## Import and understand

Open Universal Intelligence → Data Hub. Import several related files as separate
tables, rather than flattening everything. Existing table/source names are not
overwritten. CSV/TSV preserve text identifiers and leading zeros. JSON accepts a
flat array; JSON Lines accepts flat objects per line. Nested fields require
explicit flattening outside this importer. Invalid files commit no partial table;
source bytes and a parsing-error receipt are retained.

Local SQLite/DuckDB connectors read source tables without editing their databases.
Optional PostgreSQL needs `uv sync --extra postgres` and a securely configured
`INSIGHTPILOT_POSTGRES_DSN`, not a credential entered in chat. Its transaction is
read-only, uses a server cursor and applies a statement timeout. No real remote
PostgreSQL service was available for live acceptance here.

HTTP imports require an explicit HTTPS URL, record-array key and actual
page-number/page-size contract. Limits: 50 pages, 100k records, 10 MiB/page and
request spacing. An empty terminal page is required. Redirects/arbitrary next
links are not followed; rate limits fail without a partial committed table.
Bearer tokens come from configured process variables and are never saved.
Source ordering/snapshot completeness still needs review.

Data Quality describes the **full current table**, not the sidebar selection.
Search every field and inspect distributions, potential identifiers, semantic
hypotheses, date coverage and missingness. A unique field is a key candidate,
not proof of business identity. Suggested dataset types remain unverified.

Literal NA/NULL/N/A/NONE/? values are flagged, not silently removed: they may be
legitimate. Confirm the source dictionary and explicitly nullify markers if
appropriate. Numeric locale separators and slash-date formats are explicit;
selected thousands separators require standard groups of three, otherwise the
value is retained in the conversion-error ledger rather than silently coerced.
ISO timestamps use UTC calendar dates. Year/month/day components can create an
explicit date column, avoiding unintended timezone day shifts.

## Prepare reversibly and approve meaning

Preparation creates another immutable version: numeric/date conversion, date
parts, trim, literal missing markers, rename, select columns and deliberate exact
deduplication. Conversion failures retain original values and stable `_row_id`
references in a separate error ledger. Missing is not zero. Activate an earlier
version to undo; original bytes are retained. Re-review roles after preparation.

In Review and correct semantic roles, explain what one row represents and approve
field meanings. Revenue, cost, quantity, IDs, categories, dates and units require
business review. The engine does not certify a financial meaning from a name.

## Define metrics

Executive Intelligence → Define a reusable metric asks for name, definition,
formula, units/currency and limitations. Supported examples:

| Formula | Interpretation |
|---|---|
| `count()` | Source-row count with reviewed grain |
| `distinct("customer_id")` | Unique nonblank recorded IDs |
| `sum("amount")` | Finite numeric sum; not automatically revenue |
| `avg("duration")`, `median("duration")` | Finite recorded duration summaries |
| `sum("revenue") / sum("quantity")` | Weighted unit amount, not mean of row ratios |
| `valid("delay")` | Finite numeric denominator |
| `count_if("delay", ">", 15)` | Explicit conditional count |
| `100 * count_if("delay", ">", 15) / valid("delay")` | Percentage conditional on known delay |
| `count_if("timely", "==", "Yes") / (count_if("timely", "==", "Yes") + count_if("timely", "==", "No"))` | Timely fraction among known Yes/No |

Only literal column arguments, permitted aggregates, finite constants and basic
arithmetic are accepted. No Python or arbitrary SQL executes. Zero denominators
are unavailable. Identifiers cannot become summed measures without deliberate
numeric-role review. `count("field")` counts present values; `valid("field")`
counts finite numbers. Neither invents a population/exposure denominator.

Check order/line-item grain before summing: repeated order totals can double count
even with a safe join. Numbers are retained, but calculations use double
precision, not exact-decimal ledger arithmetic.

## Relationships and linked exploration

Data Hub screens up to 50 same-name key candidates for uniqueness/nonmissingness
and overlap. These are hypotheses. Validate additional keys manually if needed.
Inspect duplicates, nulls, unmatched rows, cardinality and predicted join rows.
Left/inner/full previews use source-qualified columns. Row multiplication needs
explicit acknowledgement before saving.

Analytical joins require the right-side key to be unique. The left fact grain and
its filters are preserved, including unmatched rows under a missing dimension.
Revalidate relationships after version changes. Arbitrary many-to-many metric
joins and automatic composite-key modeling are not implemented.

Shared category/date/numeric filters apply across analytical pages. High-cardinality
selectors show 1,000 frequent values plus exact-value entry; nothing is silently
removed from underlying calculations. Reset restores the full selection.

Visual Explorer provides purposeful ranked bars, numeric distributions, scatter,
calendar trends, exact group box summaries and bounded complete heatmaps.
Select a ranked bar to update its distribution and supporting records, intersected
with all shared filters. Record pages contain at most 100 rows. Top-N, first-2k
scatter bounds, missing dates and incomplete periods are disclosed. Box whiskers
are observed min/max, not Tukey fences. Calendar gaps remain missing.

Investigation Lab uses equal, nonoverlapping inclusive windows. Additive segment
contributions reconcile to totals; relative changes from zero are unavailable.
Separate numerator/denominator metrics allow exact symmetric rate/mix
decomposition. New/absent segment rates or invalid denominators are rejected,
not imputed. This is accounting, not a proven causal explanation.

## Advanced methods

- Mean intervals/Welch comparisons report sample sizes, effect sizes and
  assumptions. Independence, representativeness, skew and repeated entities matter.
- Full complete-pair Pearson/one-predictor OLS is association, not causality.
- Categorical association checks cardinality/expected cells; sparse 2×2 uses
  Fisher exact, unsuitable larger tables are rejected.
- Time analysis includes rolling mean/variability and period/year-ago changes.
  Daily/weekly year-ago lags are approximate and explicitly described.
- Baseline monthly forecasts require 36 contiguous interior months after edge
  exclusion, a real 12-month holdout and baseline comparison. Empirical error bands
  are not calibrated prediction guarantees or a structural-change model.
- IQR/MAD screening flags review candidates, not proven errors/fraud. Zero spread
  or insufficient observations makes the method unavailable.
- RFM needs confirmed revenue, customer identity, date and transaction identity.
  Frequency is distinct transactions; no invented lifetime-value model is used.
- Retention uses first observed activity, not verified acquisition. Confirm complete
  months; censored future cells are omitted rather than zero-filled.
- Funnels require identity, strict timestamp order and a confirmed complete window.
  Earliest-start entities are counted; censored starts and invalid sequences are
  disclosed/rejected. Equal-time events do not imply a known progression order.
- Clustering standardizes reviewed numeric features, fits at most 50k complete
  rows with a fixed seed and reports an internal silhouette score on up to 2k
  rows. Confirm independent entity grain. Groups are exploratory model labels;
  a bounded SVD projection is not the complete clustering geometry.
- Text counts English terms/bigrams in up to 5k nonmissing records, each capped at
  10k characters. It is not evaluated sentiment, classification or topic modeling.

Holm correction is available as a library utility for explicit comparison
families. Automated discovery makes bounded descriptive findings, not an
uncontrolled battery of significance claims. It exposes concentration, quality
and equal 28-day-window change evidence with limitations and next investigations.

## Optional AI, reports and replay

The guided analyst supports the selected explicit metric's summary, groups and
trend. Ambiguous average/total requests and unsupported questions receive a
clarification rather than an arbitrary count. This is not full conversational AI.

Optional schema-only planning uses `INSIGHTPILOT_LLM_API_KEY`,
`INSIGHTPILOT_LLM_MODEL` and optionally `INSIGHTPILOT_LLM_BASE_URL` (HTTPS,
OpenAI-compatible chat-completions). Consent sends the question, column names and
metric definition. The response can only select a finite validated plan; the
local engine writes the numerical answer. No source rows/aggregates are sent by
this new planner. Usage is shown if reported. Live-provider acceptance is unrun.
Original live-AI workspaces have their own consent controls and data contracts.

Reports include source/hash/version, filters, definitions/units, transformations,
queries, charts, findings and limitations. Export standalone HTML, PNG/chart HTML,
plotted-value CSV, paginated CSV/XLSX and evidence JSON. Formula-like text/headers
are escaped for spreadsheets. HTML may be printed to PDF; no native PDF engine
is claimed. Existing Power BI preparation stays in its original workspaces.

A manifest replays only against a matching source hash and retained immutable
version; it never executes code or fetches arbitrary URLs. Earlier-version replay
does not change the active version. Fresh-install case reproduction uses the
pinned acquisition script and independent reconciliation, not a claim that old
machine-local version identifiers exist in a new workspace automatically.
