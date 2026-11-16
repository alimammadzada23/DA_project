# Methods of the automatic analysis

Every statistic the automatic analysis reports, with its formula, its reference, the
setting that controls it and the test that checks it. Settings are fields of
`AnalysisConfig` in `src/insightpilot/universal/auto/config.py` (shown as
`setting = default`); the configuration and its hash are printed in every report.

Conventions used throughout:

- **Full data vs samples.** Counts, rates, means, tests and intervals use every row,
  computed in DuckDB. Seeded samples (rows ordered by `hash(_row_id + seed)`, `seed = 42`)
  are used only where stated below, and every sample is listed in the report.
- **Missing values** are their own level ("(missing)") or their own bin; nothing is filled.
- **Associations, not causes.** No statement uses causal wording; a test enforces this.
- **Confidence level**: `confidence = 0.95`.

## 1. Ingest and data types

| Method | Rule | Reference / code |
| --- | --- | --- |
| Encoding (opt-in at import) | Byte-order mark; else strict UTF-8; else cp1250 when Central European letters outnumber Western ones under cp1252 | `auto/ingest.detect_encoding` |
| Separator and leading rows | For `, ; tab |`, the share of the first 200 lines with the modal field count; the best share wins; lines before the first modal-width line are left out and listed | `auto/ingest.detect_layout` |
| NA markers | `N/A, NA, NULL, NaN, ?, -, --, #N/A, missing` (any case), empty or whitespace-only. `None` is not a default marker | `NA_MARKERS` |
| Types | empty, constant, binary, numeric (≥ 99% of values parse, no leading zeros), identifier (≥ 99% distinct of ≥ 20 values, or distinct whole numbers), date (ISO, or unambiguous `dd/mm/yyyy` / `mm/dd/yyyy`), free text (> 30 distinct, mean length ≥ 30, ≥ 50% of values contain a space), otherwise categorical | `classify` |
| Formatted numbers | Each value is matched by a template: optional sign, currency symbol or ISO code, digits with one separator convention, `%`, parentheses. A convention is used when it explains ≥ 95% of rows and no convention with the other decimal mark explains as many (otherwise "ambiguous separators"). Percent values are divided by 100; parentheses mean negative | `auto/ingest.detect_number`, `profile.number_expression` |
| Booleans | More than two spellings, all in the yes/no map, become `true` / `false` | `profile.BOOLEAN_TRUE`, `BOOLEAN_FALSE` |
| Dates in other formats | Formats are added greedily by rows explained, never mixing day-first and month-first; converted when ≥ 95% of rows parse. Ambiguous order and two-digit years are listed, not converted | `auto/ingest.detect_dates` |
| Excel serial dates, Unix time | Integers in 20,000–80,000 with a date-like name, or in 10⁹–2.2·10⁹ (seconds) or 10¹²–2.2·10¹² (milliseconds), are listed only | `auto/ingest.numeric_time_hint` |

Conversions are one new table version with an error ledger; values that fail become
missing and are kept in the ledger.

## 2. Quality and structure

**Explained missing values.** For a column with missing values and a candidate condition
C (another column's most common value among the missing rows, or that column being
missing): coverage = P(C | missing), lift = P(C | missing) / P(C), precision =
P(missing | C). Reported when coverage ≥ `missing_explanation_coverage = 0.95`, lift ≥
`missing_explanation_lift = 3` and precision ≥ `missing_explanation_precision = 0.5`;
"missing ⇔ condition" when coverage and precision are both ≥ 0.99.

**Conditional categories.** Level L of X occurs on the same rows as Y = v, with both
overlaps |L ∩ v| / |L| and |L ∩ v| / |v| ≥ `dependency_overlap = 0.99`. X is then compared
with the target on rows where Y ≠ v.

**Functional dependencies.** A determines B when B has one value in ≥ 99% of A's groups
with two or more rows (screened on the sample, confirmed on all rows).

**Derived columns.** C ≈ k·A or C ≈ k·(A ∘ B) for ∘ ∈ {×, +, /, −}, with k fitted through
the origin, k = Σcf / Σf². Screened on 1,000 sampled rows (trimmed R² ≥ 0.80), refitted on
`derived_sample_rows = 5,000` rows with the trimmed R² (largest 1% of squared residuals
dropped) ≥ `derived_r2 = 0.95`, then confirmed with the plain R² ≥ 0.95 on every complete row.

**Associations between fields** (seeded sample of `association_sample_rows = 200,000`):

- Categorical × categorical: bias-corrected Cramér's V (Bergsma 2013):
  φ̃² = max(0, χ²/n − (k−1)(r−1)/(n−1)), r̃ = r − (r−1)²/(n−1), k̃ = k − (k−1)²/(n−1),
  V = √(φ̃² / min(k̃−1, r̃−1)).
- Numeric × numeric: Spearman ρ, the Pearson correlation of average ranks.
- Mixed: correlation ratio η = √(SS_between / SS_total).

**Redundancy clusters.** Average-linkage hierarchical clustering on 1 − |association|,
cut at `redundancy_threshold = 0.7`. The representative is the member with the highest
single-field power (or the lowest missing share without a target).

## 3. Field-versus-target tests (all rows)

| Field \ target | Binary | Multiclass | Numeric |
| --- | --- | --- | --- |
| Categorical | χ² test of independence; Cramér's V | χ²; Cramér's V | Kruskal–Wallis H; ε² |
| Numeric | Mann–Whitney U; rank-biserial r | Kruskal–Wallis H; ε² | Spearman ρ (Pearson for reference) |

- **χ²** (Pearson, no continuity correction): χ² = Σ (O − E)² / E, df = (r−1)(c−1).
  Cramér's V = √(χ² / (n (min(r,c) − 1))).
- **Exact tests for sparse tables.** When any expected count is below 5: Fisher's exact
  test for 2×2 tables (Fisher 1922), otherwise the Fisher–Freeman–Halton test (Freeman &
  Halton 1951) with a Monte Carlo p-value from 9,999 seeded tables with the same margins.
  The χ² p-value is shown for reference.
- **Mann–Whitney U** with average ranks for ties, normal approximation with tie and
  continuity correction (Mann & Whitney 1947). Rank-biserial r = 2U / (n₁n₂) − 1 (Kerby 2014).
- **Kruskal–Wallis H** with the tie correction (Kruskal & Wallis 1952); ε² = H / (n − 1)
  (Tomczak & Tomczak 2014).
- **Spearman ρ** p-value from t = ρ √((n−2)/(1−ρ²)) with n − 2 df.
- **Wilson interval** for every rate (Wilson 1927): centre = (p + z²/2n) / (1 + z²/n),
  half-width = z √(p(1−p)/n + z²/4n²) / (1 + z²/n).
- **Practical effect** of a group: difference from the overall rate in percentage points,
  lift = group rate / overall rate, share of all positives.
- **Multiple testing**: Benjamini–Hochberg adjusted p-values across fields (Benjamini &
  Hochberg 1995): q₍ᵢ₎ = min_{j ≥ i} p₍ⱼ₎ m / j.
- **Median intervals** (numeric targets): percentile bootstrap (Efron & Tibshirani 1993),
  `bootstrap_resamples = 1,000` (200 above `bootstrap_large_n = 50,000` rows per group),
  at most `bootstrap_rows_cap = 20,000` sampled rows per group.

## 4. Comparable ranking: single-field predictive power

Every field is binned the same way: levels for categories (`ranking_level_cap = 2,000`,
the rest pooled), `numeric_bins = 10` equal-count bins for numbers, calendar months for
dates, plus a missing bin. Folds: rows dealt round-robin in `hash(_row_id + seed)` order
within each class (`cv_folds = 5`). With a time axis: `cv_folds + 1` consecutive time blocks;
block f is scored from blocks before it only.

- **Binary / multiclass**: cross-validated target encoding (Micci-Barreca 2001). The
  score of bin b is (positives_b + m · prior) / (rows_b + m) from the training folds,
  `encoding_smoothing = m = 20`. Power = out-of-fold AUC − 0.5 (macro one-vs-rest for
  multiclass). AUC from grouped scores = P(score₊ > score₋) + ½ P(tie) (Hanley & McNeil 1982).
- **Numeric**: the prediction is the smoothed training mean of the bin; power = out-of-fold
  R² = 1 − SSE/SST around the held-out fold's mean.
- **Secondary metric**: normalised mutual information NMI = MI / mean(H(X), H(Y)),
  compared with `permutations = 100` permutations of the target (margins fixed); the
  report shows NMI minus the permutation mean and the permutation p-value.
- **Non-linear shape**: the share of between-bin variation explained by the best monotone
  (isotonic) fit (Barlow et al. 1972); below `monotone_share_minimum = 0.8` with real
  differences between bins is a non-linear shape.

## 5. Leakage screen

A field is a suspect when (1) it alone reaches AUC ≥ `leakage_auc = 0.95` (R² ≥
`leakage_r2 = 0.90`), (2) it determines the target in ≥ 99% of its repeated values or takes
one value per target class, (3) a date name contains a post-outcome word (close, cancel,
end, resolved, …; a disclosed hint), or (4) with a time axis, the date is later than the
time axis on ≥ `temporal_leakage_share = 0.99` of rows. Suspects stay in the ranking and
are excluded from the model and segments.

## 6. Multivariate model

- **Model**: scikit-learn HistGradientBoosting, `model_iterations = 100`, learning rate 0.1,
  31 leaves, 20 rows per leaf, native categorical support (`model_level_cap = 30` levels),
  native missing values. Seeded sample of `model_sample_rows = 100,000` rows.
- **Validation**: 5-fold stratified cross-validation (time-ordered folds with a time
  axis). Reported against baselines: AUC 0.5, PR-AUC = positive share, Brier score and
  expected calibration error of the class shares, log loss of the class shares, accuracy
  of the majority class; R² and MAE of the training mean and median.
  ECE = Σ_b (n_b/n) |mean prediction_b − observed rate_b| over 10 equal-width bins.
- **No signal**: if the gain over the baseline (AUC − 0.5, or R² − baseline R²) is below
  `signal_minimum = 0.02`, importance and adjusted effects are not reported.
- **Permutation importance** (Breiman 2001; Fisher, Rudin & Dominici 2019): drop in the
  held-out score when a field, or a whole redundancy cluster at once, is shuffled;
  `permutation_rows = 5,000` held-out rows per fold, `permutation_repeats = 3`, for the
  `importance_fields = 25` highest-ranked fields and their clusters.
- **Adjusted model**: logistic regression or OLS with HC3 standard errors (statsmodels) on
  one representative per redundancy cluster, after dropping fields with VIF >
  `vif_maximum = 5`. VIF_j = 1 / (1 − R²_j) from the correlation matrix of the design.
  Numbers are standardised and median-filled with a missing indicator; categories are
  one-hot against their most common applicable level. Fitted on at most
  `regression_sample_rows = 50,000` rows of the model sample.
- **Univariate vs adjusted**: likelihood-ratio χ² of a field alone (vs the null model) and
  adjusted (full model vs the model without it), LR = 2 (log L_bigger − log L_smaller),
  df = the field's columns. Effect retained = √(adjusted χ² / univariate χ²). Verdicts:
  holds (≥ `weakens_share = 0.5`), weakens substantially, disappears (adjusted p ≥ 0.05),
  reverses (both coefficient intervals exclude 0 with opposite signs).
- **Accumulated local effects** (Apley & Zhu 2020) for the `ale_fields = 5` top numeric
  fields: mean prediction difference across `ale_bins = 10` equal-count intervals,
  accumulated and centred.
- **Wide data**: above `wide_columns = 200` ranked fields, the model steps use the
  `wide_model_fields = 50` with the highest single-field power plus every cluster representative.

## 7. Confounding and interactions

- **Comparison per field**: the level most different from the rest (or above vs at or
  below the median for numbers), stratified by each of the next `confounding_strata = 3`
  ranked fields (numbers in quartiles, categories by their 5 most frequent levels).
- **Binary target**: Cochran–Mantel–Haenszel common odds ratio (Mantel & Haenszel 1959)
  OR_MH = Σ_s (a_s d_s / n_s) / Σ_s (b_s c_s / n_s), with its interval and test of no
  association, and the Breslow–Day test of homogeneity (statsmodels `StratifiedTable`).
  The crude odds ratio uses Haldane's 0.5 correction for zero cells and a Woolf interval.
- **Numeric target**: weighted mean difference Σ w_s Δ_s / Σ w_s with w_s = n₁n₀/(n₁+n₀).
- **Flags**: reverses (direction changes and the stratified interval excludes no effect),
  drops by more than half (on the log scale for odds ratios).
- **Interactions** for pairs of the `interaction_fields = 8` top fields (numbers in
  quintiles, categories by `interaction_levels = 8` levels): the cross-validated gain of a
  model with joint levels over one with separate levels (L2 logistic regression, C = 1, AUC;
  or ridge, α = 1, R²), fitted on per-cell counts with counts as weights (the same
  objective as fitting every row). Reported when the likelihood-ratio test of joint vs
  separate levels has BH-adjusted p < 0.05 and the gain is ≥ `interaction_minimum = 0.01`
  and more than twice its standard error.

## 8. Segments

A decision tree (depth `segment_depth = 3`, minimum leaf max(`segment_min_rows = 30`,
`segment_min_share = 1%` of rows)) is grown on 4 of 5 folds of the model sample (the
earlier 80% with a time axis) and pruned bottom-up: a split whose two children do not differ on the training rows
(χ² test for binary targets, Welch t test for numeric ones, p ≥ 0.001) becomes a leaf. Each
leaf is checked on the held-out rows: kept when the held-out lift is ≥
`segment_lift_minimum = 1.2` (or ≤ 1/1.2), its held-out interval (Wilson, or mean ± 1.96 SE)
excludes the overall rate, and it has ≥ 30 held-out rows. Kept rules are recounted on all
rows; impact = rows × |rate − overall|. Top `segments_high = 5` and `segments_low = 3`.

## 9. No target

- **Clusters**: Gower distance (Gower 1971) = mean over fields of |xᵢ − xⱼ| / range
  (range = 1st–99th percentile, each difference capped at 1) and [xᵢ ≠ xⱼ] for categories,
  skipping fields missing in either row. k-medoids by alternating assignment and medoid
  update (Park & Jun 2009) from k-medoids++ starts; standardised k-means when every field is
  numeric and complete. k ∈ [`cluster_k_min = 2`, `cluster_k_max = 8`] by the mean
  silhouette (Rousseeuw 1987) on `cluster_sample_rows = 1,000` rows; stability = mean
  adjusted Rand index (Hubert & Arabie 1985) over `cluster_bootstrap = 10` bootstrap refits.
  A structure needs silhouette ≥ 0.25 and ARI ≥ 0.6.
- **Cluster profiles**: (cluster mean − rest mean) / overall SD for numbers; (p_in − p_out) /
  √(p(1−p)) for the most different level of each category.
- **Unusual rows**: Isolation Forest (Liu, Ting & Zhou 2008), 200 trees, seeded; the top
  `anomaly_share = 1%`. Reasons: robust z = (x − median) / (1.4826 · MAD) ≥ 3, or levels held
  by under 1% of rows.
- **Distribution shape**: skewness, excess kurtosis, the share beyond Q1 − 3·IQR and
  Q3 + 3·IQR, and Hartigan's dip test (Hartigan & Hartigan 1985; algorithm AS 217). The dip
  D = inf_{G unimodal} sup_x |Fₙ(x) − G(x)|; its p-value is the share of
  `dip_simulations = 200` uniform samples of the same size with a dip at least as large, on
  at most `dip_sample_rows = 2,000` values.

## 10. Time analysis

- **Granularity**: the median gap between consecutive distinct timestamps.
- **Aggregation level**: the finest of hour, day, week (Monday), month, quarter, year with
  ≥ `time_min_periods = 20` periods and ≥ `time_min_rows_per_period = 30` rows per period.
  Incomplete first and last periods are left out of the tests.
- **Mann–Kendall** (Mann 1945; Kendall 1975): S = Σ_{i<j} sign(y_j − y_i),
  Var(S) = [n(n−1)(2n+5) − Σ_g t_g(t_g−1)(2t_g+5)] / 18, Z = (S − sign S) / √Var(S). With
  lag-1 autocorrelation r > 1.96/√n of the detrended series, Var(S) is multiplied by
  1 + (2/n) Σ_{k=1}^{n−1} (n−k) r^k (Yue & Wang 2004).
- **Theil–Sen slope** (Theil 1950; Sen 1968): the median pairwise slope with Sen's interval.
  A trend finding needs p < 0.05, an interval excluding 0 and a change over the range of
  ≥ `trend_min_change = 10%` of the mean level.
- **STL** (Cleveland et al. 1990) for cycles with two complete repetitions; seasonal
  strength F_S = max(0, 1 − Var(R) / Var(S + R)) (Wang, Smith & Hyndman 2006), p-value from
  `seasonal_permutations = 99` permutations of the detrended series. A finding needs
  F_S ≥ 0.4 and p ≤ `seasonal_p_maximum = 0.01`; the seasonal part is then removed before
  the trend and break tests.
- **Breaks**: binary segmentation into straight-line segments of ≥ `break_min_periods = 4`;
  a split is kept when n ln(SSE_without / SSE_with) > `break_penalty = 1.5` × c × 3 ln n,
  with c = (1+ρ)/(1−ρ) ∈ [1, 4] from the lag-1 autocorrelation of the residuals of the best
  two-segment fit, and when the level jump is ≥ `break_min_jump_sd = 2` noise SDs
  (noise SD = SD of first differences / √2). At most `break_max = 3`.
- **Target over time**: rate with Wilson intervals, or mean ± t SE, per period.
- **Drift**: population stability index between the first and last
  `psi_window_share = 25%` of the time range: PSI = Σ_b (a_b − e_b) ln(a_b / e_b), shares
  floored at 10⁻⁴; bins are deciles (numbers) or the 20 most frequent levels plus other and
  missing, from the first window. Below 0.1 stable, 0.1–0.25 moderate, above 0.25 major
  (`psi_moderate`, `psi_major`; Yurdakul 2018).

## 11. Free text and geographic fields

- **Text**: characters and words per value (quartiles), empty share, tokens = lower-cased
  runs of letters or digits, without one-character tokens, pure numbers and the stop-words
  of the detected language (the language whose stop-words make up the largest share of
  tokens, at least 5%). Bigrams are adjacent token pairs without stop-words. Target by
  length quintile: χ² (or the exact test) for binary targets, Spearman ρ for numeric ones.
- **Country codes**: ISO 3166-1 alpha-2 or alpha-3 when ≥ 75% of distinct values and ≥ 90%
  of rows are valid codes; invalid values are listed.
- **Coordinates**: numeric fields named lat/latitude and lon/lng/long/longitude (disclosed
  hints); checks for |lat| > 90, |lon| > 180, (0, 0) and one coordinate missing; grid cells
  of the finest of 0.01°–10° with at most 200 occupied cells.

## 12. Relationships

Pairs of the top fields with Spearman ρ, Cramér's V or η and a 95% interval: Fisher z with
the Bonett & Wright (2000) standard error √((1 + ρ²/2)/(n − 3)) for ρ; inversion of the
noncentral χ² for V (Smithson 2003), V = √(λ / (n(k−1))); inversion of the noncentral F for
η (Steiger 2004), η² = λ / (λ + n). For very large statistics the normal approximation
λ̂ ± z √(2(df + 2λ̂)) is used.

## 13. Findings digest

Findings are built from the structured results with fixed templates and severity rules
(never by a model). Types: leakage suspect (critical); no signal, strong association
(rank test or permutation test BH q < 0.05 and power ≥ `finding_power_auc = 0.05` or
`finding_power_r2 = 0.02`), segment, confounding reversal, interaction, explained missing
values, drift, target trend, seasonality and break, clusters (important); data quality
(important when ≥ `finding_missing_share = 20%` missing or ≥ 1% duplicates), adjusted-effect
changes, non-linear shape, no association (both tests fail and |power| < `null_power = 0.01`),
derived columns, redundancy clusters, conditional categories, row-count trends, unusual
rows and shapes (informative). Within each severity, types take turns so the first ten
cover different kinds of result.

## 14. Scale and determinism

- One read-only DuckDB connection per read-only stage; a stage that exceeds
  `stage_budget_seconds = 600` stops its remaining loop and is marked partial.
- Identical data and settings give an identical `results.json` (run ids, timestamps and
  version table names are left out). Shuffling rows changes no full-data statistic, and
  renaming every column changes only labels and the disclosed target-name hint; both are
  tested in `tests/test_auto_suite.py`.

## Test map

| Spec item | Test |
| --- | --- |
| Wilson, Cramér's V (bias-corrected), AUC, NMI, CV power, bootstrap | `tests/test_auto_engine_stats.py` |
| Rank tests, χ², Spearman, BH, η | `tests/test_auto_stats.py` |
| CMH, Fisher exact | `tests/test_auto_suite.py` |
| Mann–Kendall, Theil–Sen, PSI, STL, breaks, dip (vs exact LP), intervals | `tests/test_auto_phase5.py` |
| Fixtures 1, 3, 4, 16 | `tests/test_auto_engine_structure.py` |
| Fixtures 2, 4, 7, 17 | `tests/test_auto_engine_multivariate.py` |
| Fixtures 5, 6, 8 | `tests/test_auto_engine_segments.py` |
| Findings for 2, 5, 6, 7, 8, 16; results.json determinism | `tests/test_auto_findings.py` |
| Fixtures 9, 11, 12, 14 | `tests/test_auto_phase5.py` |
| Fixtures 1, 10, 13, 14, 15, 18; shuffle and rename properties | `tests/test_auto_suite.py` |
| Telco regression (§9.3) and five public datasets | `scripts/auto_analysis_datasets.py`, `tests/test_auto_public.py` |

Fixture 15 (1,000,000 rows × 50 columns) runs with `INSIGHTPILOT_LARGE_TESTS=1`.
