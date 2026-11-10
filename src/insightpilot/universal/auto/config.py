"""One versioned configuration for automatic analysis. Its hash is printed in every report."""
from dataclasses import asdict, dataclass, replace
import hashlib
import json

CONFIG_VERSION = 2


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
    # Missingness explanations
    missing_explanation_coverage: float = 0.95   # share of missing rows that meet the condition
    missing_explanation_lift: float = 3.0        # P(condition | missing) / P(condition)
    missing_explanation_precision: float = 0.5   # share of rows meeting the condition that are missing
    missing_explanation_columns: int = 50

    def to_dict(self):
        return {'config_version': CONFIG_VERSION, **asdict(self)}

    @property
    def hash(self):
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()[:16]

    def with_changes(self, **changes):
        return replace(self, **changes)


DEFAULT = AnalysisConfig()
