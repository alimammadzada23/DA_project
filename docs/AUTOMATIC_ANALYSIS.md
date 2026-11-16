# Automatic analysis

Automatic analysis runs a complete, deterministic analysis of one table and builds
one report. It computes and organises numbers; it does not interpret them, recommend
anything or use a language model. The same data and settings always give the same
tables.

## How to use it

1. Import a dataset in **Data Hub**.
2. On the dataset page, choose **Run automatic analysis**. (**Analyze manually** keeps
   the existing step-by-step workflow.) You can also start it from **Workspace Home**
   or **Reports → Automatic analysis** for any imported table.
3. Check the suggested target and change it if needed: a column, its type (binary,
   multiclass or numeric), the positive class for a binary target, and optionally a
   date column as the time axis. **No target** gives a descriptive report.
4. Press **Run automatic analysis** and watch each stage.
5. Read the report in the app, or download it: **Download HTML** (one self-contained
   file with inline SVG charts, opens offline, usually well under 2 MB), **Download
   Excel** (one sheet per table plus a Findings sheet), `results.json` (every structured
   result and finding; identical for identical data and settings), `findings.csv` and
   `evidence.json` (the query or computation behind every table and chart). Earlier
   reports stay under **Previous reports**, and every chart is also saved as evidence
   for the manual report builder.

## The report

Sections, in order: header (file hash, versions, target, date, configuration hash,
seed), at a glance (up to five numbers), warnings, top 10 findings, fields associated
with the target, segments, structure, field details (collapsed), relationships,
confounding and interactions, time, data quality and corrections, method and
limitations, **Analyst conclusions** (left empty for you), and the evidence appendix.

Each finding has an ID (F01, F02, …), a type, a severity from fixed rules
(critical, important, informative), one templated sentence with its numbers and
interval, caveats, and a link to the table or chart that supports it. Charts use the
Okabe–Ito colour-blind-safe palette; rate charts show 95% intervals and a dashed
overall-rate line.

**Optional local AI summary.** Off by default. Under the report, **Write local AI
summary** sends only the findings list to the local model. Every sentence must cite
finding IDs, may only use numbers from the cited findings, and may not use causal
wording; failing sentences are removed. The summary appears in the HTML as a separate,
labelled section and is never part of `results.json`.

## Stages

| Stage | What it does |
| --- | --- |
| 1. Data profile | Row and column counts, the detected type of every column, duplicates, missing values, NA-like markers, whitespace, spelling variants, identifiers, constant columns, free text and extreme values |
| 2. Target selection | Ranked suggestions with reasons. Column names are only hints. You confirm the target, or choose none |
| 3. Safe corrections | One new version (your active version stays active): trimmed whitespace, NA-like markers as missing, numbers and dates stored as text converted, plus format detection (below). Ambiguous cases are listed, not converted |
| 4. Structure | Conditional categories, functional dependencies, approximately derived columns, associations between fields and redundancy clusters |
| 5. Target overview | Class counts, or a numeric summary |
| 6. Fields versus the target | Every usable field with the test for its type, cross-validated single-field power, free-text and geographic summaries |
| 7. Leakage screen | Fields that predict the target too well, mirror it, or are dated after the time axis |
| 8. Ranking | All fields by cross-validated power, with Benjamini–Hochberg adjusted p-values |
| 9. Multivariate model | Gradient boosting against a baseline, permutation importance, univariate vs adjusted effects |
| 10. Confounding and interactions | Effects within strata of other fields, and pairs that matter jointly |
| 11. Segments | Decision-tree segments checked on held-out rows |
| 12. Clusters and unusual rows | Without a target only: clusters, unusual rows and distribution shapes |
| 13. Relationships | Associations (with 95% intervals) between the top fields |
| 14. Time analysis | With a time axis: aggregation level, trend, seasonality, breaks and drift |
| 15. Findings digest | Ranked, templated findings with evidence links |
| 16. Saved evidence | Each chart is stored as a normal evidence block |

If a stage fails, later stages still run where they can, and the report lists the
failed stage and its error at the top. A stage that reaches its time budget stops,
keeps what it finished and is marked **partial** with the reason.

## Time analysis

Choose a date or timestamp column as the time axis. The engine reports coverage, the
granularity (median gap between timestamps), rows sharing a timestamp and empty periods,
then aggregates to the finest level (hour, day, week, month, quarter, year) with at
least 20 periods and 30 rows per period on average. Incomplete first and last periods
are left out of the tests.

- **Trend:** Mann–Kendall test (variance inflated for positive autocorrelation) and the
  Theil–Sen slope with its 95% interval, for the row count and for the target rate or mean.
- **Seasonality:** STL decomposition when two full cycles exist; seasonal strength with a
  permutation p-value. Strength ≥ 0.4 with p ≤ 0.01 is a finding and is removed before
  the trend and break tests.
- **Breaks:** binary segmentation into straight-line segments with a BIC-type penalty
  raised for autocorrelation; dates with the level jump.
- **Drift:** population stability index per field between the first and last 25% of the
  time range (above 0.25 is a major shift).
- With a time axis, every cross-validation (single-field power, the model, interactions,
  segments) trains on earlier rows and checks on later rows. A date that falls after the
  time axis on 99% of rows is a leakage suspect.

## No target

Without a target the report adds clusters, unusual rows and distribution shapes, all on
a seeded sample (up to 200,000 rows) that the report discloses:

- **Clusters:** Gower distance with k-medoids for mixed fields, standardised k-means for
  complete numeric fields; k from 2 to 8 by silhouette, checked with bootstrap adjusted
  Rand index. Without silhouette ≥ 0.25 and ARI ≥ 0.6 the report says there is no
  stable cluster structure. Each cluster is described by its most different fields.
- **Unusual rows:** Isolation Forest; the top 1% with the fields that make each row
  unusual. Rows are never removed.
- **Shape:** skewness, excess kurtosis, values beyond the outer fences and Hartigan's
  dip test of unimodality.

## Free text and geographic fields

Free text (many distinct values with spaces and a mean length of 30 characters or more)
is described by length, empty share, frequent words and word pairs after stop-words of
the detected language (English, German, French, Spanish, Italian, Portuguese, Dutch),
and by the target per text-length group. ISO 3166 country codes are recognised and
invalid codes listed; latitude/longitude pairs (name hints plus valid ranges) get range
checks and counts and the target per grid cell.

## Format detection

At import, **Detect encoding, separator and title rows above the table** (off by
default) reads UTF-8, cp1250 or cp1252, picks the separator with the most consistent
field count and leaves out rows above the table; everything detected is shown and kept
in the table's provenance. During safe corrections, text columns are checked for
formatted numbers (decimal comma, thousands separators, currency symbols or codes,
percent signs divided by 100, accounting negatives in parentheses), mixed yes/no
spellings and dates in other formats. A column is converted only when one reading
explains at least 95% of its rows; failed values become missing and are kept in the
error ledger. Ambiguous separators, ambiguous day/month order, mixed currencies,
two-digit years and numbers that look like Excel serial dates or Unix timestamps are
listed for you and left unchanged.

## Methods

Every method, with its formula, reference, setting and test, is in [methods.md](methods.md).
The table below summarises the tests used per field and target type.

| Field \ target | Binary | Multiclass | Numeric |
| --- | --- | --- | --- |
| Categorical | Rows, positives, positive rate and share of positives per category; count heatmap; positive-rate chart; chi-square and Cramér's V | Counts and row shares; stacked share chart; chi-square and Cramér's V | Count, median, mean and quartiles per category; box plot; Kruskal–Wallis and epsilon-squared |
| Numeric | Summary and box plot per class; positive rate by 5 equal-count bins; Mann–Whitney U and rank-biserial r | Summary and box plot per class; Kruskal–Wallis and epsilon-squared | Spearman ρ (Pearson for reference); median by 10 equal-count bins; scatter plot |
| Date | Positive rate per period | Class shares per period | Median per period |
| Free text | Summary, frequent words, rate by text length (chi-square) | Summary and frequent words | Summary, frequent words, mean by text length (Spearman ρ) |

- Rank tests use average ranks with tie corrections, computed inside DuckDB, so no
  table is loaded into memory. Mann–Whitney uses the normal approximation with tie
  and continuity correction. Every test is checked against SciPy in the test suite.
- Chi-square has no continuity correction. Cells with an expected count below 5 are
  flagged; groups with fewer than 30 rows are flagged.
- Epsilon-squared is H / (n − 1). Rank-biserial r is 2U / (n₁n₂) − 1, positive when
  values tend to be larger in the positive class.
- Field pairs: Cramér's V (categorical), Spearman ρ (numeric), correlation ratio η (mixed),
  with 95% intervals (Fisher z for ρ; noncentral χ² and F inversion for V and η).
- Effect sizes are reported as numbers only, without labels such as "strong".

## Rules and thresholds

Thresholds live in `AnalysisConfig` (`src/insightpilot/universal/auto/config.py`) and
`SETTINGS` (`src/insightpilot/universal/auto_analysis.py`); the configuration and its
hash are printed in every report.

- Types come from the data. Identifiers are text columns with at least 99% distinct
  values, or whole-number columns where every value is distinct (at least 20 values).
  Free text has more than 30 categories and a mean length
  of 30 characters or more. Numbers stored as text are converted when at least 99% of
  values parse; numbers with leading zeros stay text.
- Categorical fields show the 30 most frequent categories; the rest are grouped as
  "(other)", and missing values form "(missing)".
- NA-like markers: `N/A`, `NA`, `NULL`, `NaN`, `?`, `-`, `--`, `#N/A`, `missing` (any case),
  empty and whitespace-only values. `None` is not a marker by default, because it is often a
  real level (for example a service level "none").
- Dates in `dd/mm/yyyy` or `mm/dd/yyyy` are converted only when a value larger than 12
  decides the order; otherwise they are reported and left as text.
- Date fields compared with the target use months for ranges up to 2 years, quarters up to
  8 years, otherwise years; the time analysis chooses its own level (above).
- Counts, rates and tests use all rows. Samples (rows ordered by `hash(_row_id + 42)`) are
  used for scatter plots, field associations (200,000 rows), models (100,000 rows; the
  adjusted regression 50,000) and clusters; every sample is listed in the report.
- Wide data: above 200 ranked fields, the model steps use the 50 with the highest
  single-field power plus every redundancy-cluster representative, and the report says so.
- Time budget: 600 seconds per stage (`stage_budget_seconds`).
- Statistical associations are not causes, and the report says so.

## What it does not do

It does not fill missing values, remove duplicates, merge spellings or explain results.
To fill missing values yourself after reading the report, use **Quality & Preparation →
fill_missing** (constant, median, mean or mode, an optional condition on another
column, and an optional `<column>_was_missing` indicator). The fill value and filled
rows are recorded in the version history.

## Scale

On a 10-core Mac with 16 GB of memory, a synthetic table of 100,000 rows × 50 columns
takes about 65 seconds and 1,000,000 rows × 50 columns about 3 minutes. DuckDB may use
up to 55% of memory by default; with `INSIGHTPILOT_DUCKDB_MEMORY=2GB` the 1,000,000-row
run peaks at about 3.2 GB of memory at the same speed.

## Tested datasets

Runtimes on a 16 GB, 10-core Mac, measured with
`scripts/auto_analysis_datasets.py`. "Checks" compares reported numbers with pandas and
SciPy on the raw file (for Online Retail II, with direct SQL on the original version).

| Dataset | Domain | Rows × columns | Target | Import | Profile | Analysis | Checks |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [IBM Telco Customer Churn](https://github.com/IBM/telco-customer-churn-on-icp4d) | Telecom | 7,043 × 21 | Churn (binary, Yes) | 0.2 s | 0.8 s | 13.3 s | 53/53 |
| [UCI Adult](https://archive.ics.uci.edu/dataset/2/adult) | Census | 32,561 × 15 | income (binary, >50K) | 0.2 s | 0.8 s | 17.5 s | 11/11 |
| [Palmer Penguins](https://github.com/allisonhorst/palmerpenguins) | Biology | 344 × 8 | species (multiclass) | 0.0 s | 0.5 s | 3.4 s | 7/7 |
| [UCI Bike Sharing](https://archive.ics.uci.edu/dataset/275/bike+sharing+dataset) | Mobility | 17,379 × 17 | cnt (numeric), time axis dteday | 0.2 s | 0.8 s | 18.8 s | 8/8 |
| [UCI Covertype](https://archive.ics.uci.edu/dataset/31/covertype) | Forestry | 581,012 × 55 | Cover_Type (multiclass) | 5.4 s | 9.7 s | 138.7 s | 10/10 |
| [UCI Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii) | Retail | 1,067,371 × 9 | none, time axis InvoiceDate | 42.7 s | 3.8 s | 25.1 s | 6/6 |

Together they cover binary, multiclass, numeric and no target; dates; real quality
problems (`?` and `NA` markers, blank values, padded text); header-less files; 344 to
1,067,371 rows; 55 columns; and high-cardinality text. "Import" is the normal file
import, which happens once; "Analysis" includes the safe corrections, which create one
new version of the table. Adult, Bike Sharing, Covertype and Online Retail II are
CC BY 4.0 and Palmer Penguins is CC0. None of the files are committed.

To repeat the measurements, download the files and run the script:

```bash
mkdir -p outputs/public-datasets/{adult,penguins,bike,covertype,retail}
cd outputs/public-datasets
curl -L -o adult/adult.zip https://archive.ics.uci.edu/static/public/2/adult.zip && unzip -j adult/adult.zip adult.data -d adult
curl -L -o penguins/penguins.csv https://raw.githubusercontent.com/allisonhorst/palmerpenguins/main/inst/extdata/penguins.csv
curl -L -o bike/bike.zip "https://archive.ics.uci.edu/static/public/275/bike+sharing+dataset.zip" && unzip -j bike/bike.zip hour.csv -d bike
curl -L -o covertype/covertype.zip https://archive.ics.uci.edu/static/public/31/covertype.zip && unzip -j covertype/covertype.zip covtype.data.gz -d covertype && gunzip covertype/covtype.data.gz
curl -L -o retail/retail.zip "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip" && unzip -j retail/retail.zip online_retail_II.xlsx -d retail
cd ../..
.venv/bin/python scripts/auto_analysis_datasets.py --output outputs/auto-analysis-datasets.json
INSIGHTPILOT_LARGE_TESTS=1 .venv/bin/python -m pytest tests/test_auto_public.py -q
```

The Telco file is read from `~/Downloads/telco_churn.csv` or `INSIGHTPILOT_TELCO_CSV`; it
is not included because its license is not clear.
