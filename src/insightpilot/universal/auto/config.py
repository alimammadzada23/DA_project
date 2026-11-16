"""One versioned configuration for automatic analysis. Its hash is printed in every report."""
from dataclasses import asdict, dataclass, replace
import hashlib
import json

CONFIG_VERSION = 3


@dataclass(frozen=True)
class AnalysisConfig:
    seed: int = 42
    # Categories
    category_limit: int = 30                # categories shown per field in tables
    ranking_level_cap: int = 2000           # levels used for cross-validated encoding; the rest become one group
    rare_level_share: float = 0.01
    # Groups and tests
    small_group_rows: int = 30
    expected_count_minimum: int = 5
    numeric_bins: int = 10                  # equal-count bins for numeric fields
    confidence: float = 0.95
    # Cross-validated single-feature power
    cv_folds: int = 5
    encoding_smoothing: float = 20.0        # pseudo-rows pulling each level toward the training-fold rate
    permutations: int = 100                 # mutual-information permutation baseline
    bootstrap_resamples: int = 1000
    bootstrap_large_n: int = 50_000         # above this many rows per group, resamples drop to 200
    bootstrap_rows_cap: int = 20_000        # rows per group used for bootstrap intervals (seeded sample)
    # Structure discovery
    dependency_overlap: float = 0.99        # share of groups or rows that must agree
    structure_level_cap: int = 50           # categorical columns with more levels are not checked for conditional levels
    structure_column_cap: int = 60          # columns checked pairwise for structure
    dependency_level_cap: int = 10_000
    derived_r2: float = 0.95
    derived_sample_rows: int = 5000
    derived_column_cap: int = 30
    redundancy_threshold: float = 0.7
    association_sample_rows: int = 200_000   # seeded sample for field-to-field associations
    # Missingness explanations
    missing_explanation_coverage: float = 0.95   # share of missing rows that meet the condition
    missing_explanation_lift: float = 3.0        # P(condition | missing) / P(condition)
    missing_explanation_precision: float = 0.5   # share of rows meeting the condition that are missing
    missing_explanation_columns: int = 50
    # Target warnings
    imbalance_share: float = 0.05           # warn when a class is below this share (or above 1 − share)
    minority_rows_minimum: int = 50
    # Leakage screen
    leakage_auc: float = 0.95
    leakage_r2: float = 0.90
    # Multivariate model
    model_sample_rows: int = 100_000        # seeded sample used to fit models
    model_iterations: int = 100             # boosting iterations
    model_level_cap: int = 30               # categorical levels given to the model; the rest are pooled
    permutation_rows: int = 5_000           # held-out rows per fold used for permutation importance
    regression_sample_rows: int = 50_000    # seeded subset of the model sample for the adjusted regression and its LR tests
    importance_fields: int = 25             # top-ranked fields (plus their clusters) given permutation importance
    permutation_repeats: int = 3
    signal_minimum: float = 0.02            # ΔAUC or R² over the baseline needed before importance is reported
    vif_maximum: float = 5.0
    adjusted_fields: int = 10               # fields in the univariate vs adjusted table
    weakens_share: float = 0.5              # adjusted below this share of univariate = weakens substantially
    ale_fields: int = 5
    ale_bins: int = 10
    # Confounding and interactions
    confounding_fields: int = 10
    confounding_strata: int = 3
    interaction_fields: int = 8
    interaction_levels: int = 8
    interaction_minimum: float = 0.01       # ΔAUC or ΔR² from joint levels needed to report an interaction
    interactions_shown: int = 5
    # Segments
    segment_depth: int = 3
    segment_min_rows: int = 30
    segment_min_share: float = 0.01
    segment_lift_minimum: float = 1.2
    segments_high: int = 5
    segments_low: int = 3
    # Findings digest
    finding_power_auc: float = 0.05         # ΔAUC from one field before an association becomes a finding
    finding_power_r2: float = 0.02
    finding_missing_share: float = 0.20     # columns missing at least this share become a data-quality finding
    null_power: float = 0.01                # |power| below this with adjusted p ≥ 0.05 is reported as no association
    top_findings: int = 10
    # Shape
    monotone_share_minimum: float = 0.8     # below this share of between-bin variation explained by a monotone fit = non-linear
    # Time analysis
    time_min_periods: int = 20              # aggregation: finest level with at least this many periods …
    time_min_rows_per_period: int = 30      # … and at least this many rows per period on average
    time_min_periods_tests: int = 12        # complete periods needed for trend, seasonality and break tests
    time_rate_min_rows: int = 5             # periods with fewer rows get no rate or mean in the tests
    seasonal_permutations: int = 99
    seasonal_strength_minimum: float = 0.4  # STL seasonal strength reported as a finding …
    seasonal_p_maximum: float = 0.01        # … when its permutation p is at most this (99 permutations: none as strong)
    trend_min_change: float = 0.10          # Theil–Sen change over the range, relative to the mean level, before a trend becomes a finding
    break_min_periods: int = 4              # shortest segment on either side of a break
    break_max: int = 3
    break_penalty: float = 1.5              # multiplier of the BIC-type penalty 3·ln(n)
    break_min_jump_sd: float = 2.0          # level jump in noise standard deviations before a break is reported
    psi_window_share: float = 0.25          # PSI compares the first and last 25% of the time range
    psi_moderate: float = 0.10
    psi_major: float = 0.25
    psi_min_rows: int = 100                 # rows needed in each window
    psi_bins: int = 10
    psi_levels: int = 20
    temporal_leakage_share: float = 0.99    # a date later than the time axis on this share of rows is a leakage suspect
    # Scale
    stage_budget_seconds: float = 600.0     # a stage that runs longer stops its remaining work and is marked partial
    wide_columns: int = 200                 # above this many ranked fields, the model steps use a pre-screened subset …
    wide_model_fields: int = 50             # … the top fields by single-field power plus every redundancy-cluster representative
    # No-target mode
    cluster_k_min: int = 2
    cluster_k_max: int = 8
    cluster_silhouette_minimum: float = 0.25
    cluster_ari_minimum: float = 0.6
    cluster_bootstrap: int = 10
    cluster_sample_rows: int = 1000         # seeded sample used to choose k (Gower distances are rows × rows)
    cluster_fields: int = 20
    unsupervised_rows: int = 200_000        # seeded sample for cluster sizes, profiles and unusual rows
    anomaly_share: float = 0.01
    anomalies_shown: int = 20
    dip_sample_rows: int = 2000
    dip_simulations: int = 200

    def to_dict(self):
        return {'config_version': CONFIG_VERSION, **asdict(self)}

    @property
    def hash(self):
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()[:16]

    def with_changes(self, **changes):
        return replace(self, **changes)


DEFAULT = AnalysisConfig()
