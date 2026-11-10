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
5. Read the report in the app, or use **Download HTML** (standalone, opens offline) or
   **Download Excel** (one sheet per table). Earlier reports stay under **Previous
   reports**, and every chart is also saved as evidence for the manual report builder.

## Stages

| Stage | What it does |
| --- | --- |
| 1. Data profile | Row and column counts, the detected type of every column, duplicates, missing values with their `_row_id`s, numbers or dates stored as text, NA-like markers, whitespace, spellings that differ only by case or whitespace, identifiers, constant and near-constant columns, high-cardinality text and extreme values |
| 2. Target selection | Ranked suggestions with reasons. Column names are only hints. You confirm the target |
| 3. Safe corrections | Trims whitespace, turns NA-like markers into missing values, converts numbers and unambiguous dates stored as text. All of this is one new reversible version; your active version stays active. Missing values, duplicates, ambiguous dates and spelling variants are only reported. Missing values are checked for a shared value in other columns |
| 4. Target overview | Class counts and shares and the accuracy of always predicting the majority class, or a numeric summary with a histogram |
| 5. Fields versus the target | Every usable field, with the method chosen by field and target type (below) |
| 6. Ranking | All fields by effect size, with raw and Benjamini–Hochberg adjusted p-values |
| 7. Relationships | Associations between the top 8 fields (or the first 12 fields without a target), with detail for the 3 closest pairs |
| 8. Time analysis | Rows and the target per period, with incomplete first and last periods flagged |
| 9. Saved evidence | Each chart is stored as a normal evidence block |

If a stage fails, later stages still run where they can, and the report lists the
failed stage and its error at the top.

## Methods

| Field \ target | Binary | Multiclass | Numeric |
| --- | --- | --- | --- |
| Categorical | Rows, positives, positive rate and share of positives per category; count heatmap; positive-rate chart; chi-square and Cramér's V | Counts and row shares; stacked share chart; chi-square and Cramér's V | Count, median, mean and quartiles per category; box plot; Kruskal–Wallis and epsilon-squared |
| Numeric | Summary and box plot per class; positive rate by 5 equal-count bins; Mann–Whitney U and rank-biserial r | Summary and box plot per class; Kruskal–Wallis and epsilon-squared | Spearman ρ (Pearson for reference); median by 10 equal-count bins; scatter plot |
| Date | Positive rate per period | Class shares per period | Median per period |

- Rank tests use average ranks with tie corrections, computed inside DuckDB, so no
  table is loaded into memory. Mann–Whitney uses the normal approximation with tie
  and continuity correction. Every test is checked against SciPy in the test suite.
- Chi-square has no continuity correction. Cells with an expected count below 5 are
  flagged; groups with fewer than 30 rows are flagged.
- Epsilon-squared is H / (n − 1). Rank-biserial r is 2U / (n₁n₂) − 1, positive when
  values tend to be larger in the positive class.
- Field pairs: Cramér's V (categorical), Spearman ρ (numeric), correlation ratio η (mixed).
- Effect sizes are reported as numbers only, without labels such as "strong".

## Rules and thresholds

All thresholds live in `SETTINGS` in `src/insightpilot/universal/auto_analysis.py`
and are printed in every report.

- Types come from the data. Identifiers are text columns with at least 99% distinct
  values, or whole-number columns where every value is distinct (at least 20 values).
  Free text has more than 30 categories and a mean length
  of 30 characters or more. Numbers stored as text are converted when at least 99% of
  values parse; numbers with leading zeros stay text.
- Categorical fields show the 30 most frequent categories; the rest are grouped as
  "(other)", and missing values form "(missing)".
- NA-like markers: `N/A`, `NA`, `NULL`, `?`, `-` (any case), empty and whitespace-only values.
- Dates in `dd/mm/yyyy` or `mm/dd/yyyy` are converted only when a value larger than 12
  decides the order; otherwise they are reported and left as text.
- Periods are months for ranges up to 2 years, quarters up to 8 years, otherwise years.
- Only scatter plots are sampled (5,000 rows ordered by `hash(_row_id + 42)`); every
  statistic uses all rows. Sampling is listed in the report.
- Statistical associations are not causes, and the report says so.

## What it does not do

It does not fill missing values, remove duplicates, merge spellings, build predictive
models or explain results. To fill missing values yourself after reading the report,
use **Quality & Preparation → fill_missing** (constant, median, mean or mode, an
optional condition on another column, and an optional `<column>_was_missing`
indicator). The fill value and filled rows are recorded in the version history.

## Tested datasets

Runtimes on a 16 GB, 10-core Mac, measured with
`scripts/auto_analysis_datasets.py`. "Checks" compares reported numbers with pandas and
SciPy on the raw file (for Online Retail II, with direct SQL on the original version).

| Dataset | Domain | Rows × columns | Target | Import | Profile | Analysis | Reports | Checks |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [IBM Telco Customer Churn](https://github.com/IBM/telco-customer-churn-on-icp4d) | Telecom | 7,043 × 21 | Churn (binary, Yes) | 0.1 s | 0.8 s | 3.8 s | 0.6 s | 20/20 |
| [UCI Adult](https://archive.ics.uci.edu/dataset/2/adult) | Census | 32,561 × 15 | income (binary, >50K) | 0.2 s | 0.8 s | 4.0 s | 0.5 s | 11/11 |
| [Palmer Penguins](https://github.com/allisonhorst/palmerpenguins) | Biology | 344 × 8 | species (multiclass) | 0.0 s | 0.3 s | 1.5 s | 0.2 s | 7/7 |
| [UCI Bike Sharing](https://archive.ics.uci.edu/dataset/275/bike+sharing+dataset) | Mobility | 17,379 × 17 | cnt (numeric), time axis dteday | 0.2 s | 0.8 s | 4.0 s | 0.3 s | 6/6 |
| [UCI Covertype](https://archive.ics.uci.edu/dataset/31/covertype) | Forestry | 581,012 × 55 | Cover_Type (multiclass) | 5.2 s | 8.8 s | 18.1 s | 1.2 s | 10/10 |
| [UCI Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii) | Retail | 1,067,371 × 9 | none, time axis InvoiceDate | 43.2 s | 3.8 s | 13.1 s | 0.2 s | 6/6 |

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
