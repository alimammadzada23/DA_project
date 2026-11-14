"""No-target mode: cluster structure, unusual rows and distribution shape.

Methods and references
- Gower distance (Gower 1971): the mean over fields of |x_i − x_j| / range for numbers and [x_i ≠ x_j] for
  categories, skipping fields missing in either row. Ranges are the 1st–99th percentile span and each numeric
  difference is capped at 1, so a few extreme rows do not compress everyone else.
- k-medoids by alternating assignment and medoid update (Park & Jun 2009) from seeded k-medoids++ starts
  (several starts, lowest total distance kept); standardised k-means (scikit-learn) when every field is numeric
  and complete.
- k is chosen in [k_min, k_max] by the mean silhouette width (Rousseeuw 1987). Stability: the mean adjusted Rand
  index (Hubert & Arabie 1985) between the clustering and clusterings of seeded bootstrap resamples, with every
  row assigned to the nearest resample medoid or centre. A structure is reported only when some k reaches
  silhouette ≥ 0.25 and ARI ≥ 0.6; otherwise "no stable cluster structure".
- Cluster profiles: standardised differences (cluster mean − rest mean) / overall sd for numbers, and
  (share in cluster − share in the rest) / √(p(1 − p)) for the most different level of each category.
- Unusual rows: Isolation Forest (Liu, Ting & Zhou 2008), seeded, on numbers (median-filled, plus missing flags)
  and categories (encoded as the share of rows with that level). The top share of scores is listed with the fields
  that make each row unusual: robust z = (x − median) / (1.4826 · MAD) for numbers, rare levels for categories.
  Rows are reported only, never removed.
- Distribution shape: skewness, excess kurtosis, the share of values beyond the outer fences
  (Q1 − 3·IQR, Q3 + 3·IQR), and Hartigan's dip test of unimodality (see dip.py).
"""
from dataclasses import asdict, dataclass, field
import math

import numpy as np
import pandas as pd

from .dip import dip_test


@dataclass
class ClusterResult:
    method: str = ''
    fields: list = field(default_factory=list)
    fit_rows: int = 0
    assigned_rows: int = 0
    total_rows: int = 0
    candidates: list = field(default_factory=list)      # [k, silhouette, ARI or None]
    k: int | None = None
    stable: bool = False
    sizes: list = field(default_factory=list)            # [cluster, rows, share]
    profiles: list = field(default_factory=list)         # [cluster, field, description, standardised difference]
    note: str = ''

    def to_dict(self):
        return asdict(self)


# ---------------- preparation ----------------
def encode(frame, numeric, categorical):
    """Numbers as floats (NaN when missing) and categories as integer codes (−1 when missing)."""
    nums = np.column_stack([pd.to_numeric(frame[c], errors='coerce').to_numpy(dtype=float) for c in numeric]) if numeric else np.zeros((len(frame), 0))
    cats = []
    for c in categorical:
        values = frame[c].astype(object).where(frame[c].notna(), None)
        levels = sorted({v for v in values if v is not None}, key=str)
        index = {v: i for i, v in enumerate(levels)}
        cats.append(np.array([index.get(v, -1) if v is not None else -1 for v in values], dtype=int))
    return nums, (np.column_stack(cats) if cats else np.zeros((len(frame), 0), dtype=int))


def numeric_ranges(nums):
    ranges = []
    for j in range(nums.shape[1]):
        column = nums[:, j][~np.isnan(nums[:, j])]
        span = float(np.percentile(column, 99) - np.percentile(column, 1)) if len(column) else 0.0
        ranges.append(span if span > 0 else (float(column.max() - column.min()) if len(column) else 0.0) or 1.0)
    return np.array(ranges)


def gower(nums_a, cats_a, nums_b, cats_b, ranges):
    """Gower distances between every row of a and every row of b (rows × rows)."""
    total = np.zeros((max(len(nums_a), len(cats_a)), max(len(nums_b), len(cats_b))))
    weight = np.zeros_like(total)
    for j in range(nums_a.shape[1]):
        a = nums_a[:, j][:, None]; b = nums_b[:, j][None, :]
        d = np.minimum(np.abs(a - b) / ranges[j], 1.0)
        ok = ~np.isnan(d)
        total += np.where(ok, d, 0.0); weight += ok
    for j in range(cats_a.shape[1]):
        a = cats_a[:, j][:, None]; b = cats_b[:, j][None, :]
        ok = (a >= 0) & (b >= 0)
        total += np.where(ok, (a != b).astype(float), 0.0); weight += ok
    return np.where(weight > 0, total / np.maximum(weight, 1), 1.0)


# ---------------- clustering ----------------
def kmedoids(distance, k, seed, starts=5, iterations=50):
    """Alternating k-medoids on a precomputed distance matrix; returns (medoid indices, labels, total distance)."""
    n = len(distance); rng = np.random.default_rng(seed); best = None
    for _ in range(starts):
        medoids = [int(rng.integers(n))]
        for _ in range(1, k):                                  # k-medoids++ seeding
            nearest = distance[:, medoids].min(axis=1) ** 2
            if nearest.sum() <= 0: break
            medoids.append(int(rng.choice(n, p=nearest / nearest.sum())))
        medoids = np.array(sorted(set(medoids)))
        for _ in range(iterations):
            labels = np.argmin(distance[:, medoids], axis=1)
            updated = medoids.copy()
            for c in range(len(medoids)):
                members = np.flatnonzero(labels == c)
                if len(members): updated[c] = members[np.argmin(distance[np.ix_(members, members)].sum(axis=1))]
            updated = np.array(sorted(set(updated.tolist())))
            if len(updated) == len(medoids) and np.array_equal(updated, medoids): break
            medoids = updated
        labels = np.argmin(distance[:, medoids], axis=1)
        cost = float(distance[np.arange(n), medoids[labels]].sum())
        if best is None or cost < best[2]: best = (medoids, labels, cost)
    return best


def choose_k(fit, cfg):
    """Silhouette for each k, bootstrap ARI for each k that reaches the silhouette bar, and the chosen k."""
    from sklearn.metrics import adjusted_rand_score, silhouette_score
    candidates = []; models = {}
    n = fit['n']
    for k in range(cfg.cluster_k_min, min(cfg.cluster_k_max, n - 1) + 1):
        labels, model = fit['cluster'](k, cfg.seed)
        if len(set(labels.tolist())) < 2: continue
        silhouette = float(silhouette_score(fit['distance'], labels, metric='precomputed')) if fit['distance'] is not None else \
            float(silhouette_score(fit['matrix'], labels, sample_size=min(n, 5000), random_state=cfg.seed))
        candidates.append([k, silhouette, None]); models[k] = (labels, model)
    rng = np.random.default_rng(cfg.seed)
    for row in candidates:
        if row[1] < cfg.cluster_silhouette_minimum: continue
        k = row[0]; labels = models[k][0]; scores = []
        for b in range(cfg.cluster_bootstrap):
            sample = rng.choice(n, n, replace=True)
            scores.append(adjusted_rand_score(labels, fit['refit'](k, sample, cfg.seed + b + 1)))
        row[2] = float(np.mean(scores))
    stable = [r for r in candidates if r[2] is not None and r[2] >= cfg.cluster_ari_minimum]
    if not stable: return candidates, None, None
    k = max(stable, key=lambda r: (r[1], -r[0]))[0]
    return candidates, k, models[k]


def cluster(frame, numeric, categorical, cfg):
    """Fit on `frame` (a seeded sample); returns (ClusterResult without sizes, assign function for any frame)."""
    nums, cats = encode(frame, numeric, categorical)
    result = ClusterResult(fields=numeric + categorical, fit_rows=len(frame))
    complete_numeric = not categorical and not np.isnan(nums).any()
    if complete_numeric:
        from sklearn.cluster import KMeans
        mean, sd = nums.mean(axis=0), nums.std(axis=0); sd[sd == 0] = 1
        z = (nums - mean) / sd
        result.method = 'k-means on standardised numbers'

        def fit_k(k, seed, rows=None):
            return KMeans(k, n_init=10, random_state=seed).fit(z if rows is None else z[rows])
        fit = {'n': len(z), 'distance': None, 'matrix': z,
               'cluster': lambda k, seed: (lambda m: (m.labels_, m))(fit_k(k, seed)),
               'refit': lambda k, rows, seed: fit_k(k, seed, rows).predict(z)}
        candidates, k, chosen = choose_k(fit, cfg)

        def assign(other):
            o_nums, _ = encode(other, numeric, [])
            filled = np.where(np.isnan(o_nums), mean, o_nums)
            return chosen[1].predict((filled - mean) / sd)
    else:
        ranges = numeric_ranges(nums)
        distance = gower(nums, cats, nums, cats, ranges)
        result.method = 'k-medoids on Gower distance'
        fit = {'n': len(distance), 'distance': distance, 'matrix': None,
               'cluster': lambda k, seed: (lambda m: (m[1], m))(kmedoids(distance, k, seed)),
               'refit': lambda k, rows, seed: np.argmin(distance[:, rows[kmedoids(distance[np.ix_(rows, rows)], k, seed, starts=2)[0]]], axis=1)}
        candidates, k, chosen = choose_k(fit, cfg)

        def assign(other):
            medoids = chosen[1][0]
            o_nums, o_cats = encode(other, numeric, categorical)
            # Category codes must match the fitting sample's coding.
            o_cats = np.column_stack([recode(other[c], frame[c]) for c in categorical]) if categorical else o_cats
            labels = []
            for start in range(0, len(other), 20_000):
                d = gower(o_nums[start:start + 20_000], o_cats[start:start + 20_000], nums[medoids], cats[medoids], ranges)
                labels.append(np.argmin(d, axis=1))
            return np.concatenate(labels) if labels else np.array([], dtype=int)
    result.candidates = candidates; result.k = k; result.stable = k is not None
    return result, (assign if k is not None else None)


def recode(values, reference):
    levels = sorted({v for v in reference.astype(object) if v is not None and v == v}, key=str)
    index = {v: i for i, v in enumerate(levels)}
    return np.array([index.get(v, -1) if v is not None and v == v else -1 for v in values.astype(object)], dtype=int)


def profile(frame, labels, numeric, categorical, top=3):
    """Sizes and the most distinguishing fields of each cluster."""
    sizes = []; profiles = []
    order = sorted(set(labels.tolist()), key=lambda c: (-int((labels == c).sum()), c))
    names = {c: i + 1 for i, c in enumerate(order)}
    for c in order:
        inside = labels == c
        sizes.append([names[c], int(inside.sum()), float(inside.mean())])
        rows = []
        for name in numeric:
            x = pd.to_numeric(frame[name], errors='coerce').to_numpy(dtype=float)
            sd = np.nanstd(x)
            if not sd or np.isnan(x[inside]).all() or np.isnan(x[~inside]).all(): continue
            a, b = np.nanmean(x[inside]), np.nanmean(x[~inside]); d = (a - b) / sd
            rows.append([names[c], name, f'{name} {"higher" if d > 0 else "lower"}: mean {a:,.4g} vs {b:,.4g} in the other rows', float(d)])
        for name in categorical:
            values = frame[name].astype(object).where(frame[name].notna(), '(missing)').astype(str)
            best = None
            for level in values.unique():
                p_in = float((values[inside] == level).mean()); p_out = float((values[~inside] == level).mean())
                p = float((values == level).mean())
                if 0 < p < 1:
                    d = (p_in - p_out) / math.sqrt(p * (1 - p))
                    if best is None or abs(d) > abs(best[1]) or (abs(d) == abs(best[1]) and level < best[0]): best = (level, d, p_in, p_out)
            if best:
                rows.append([names[c], name, f'{name} = {best[0]}: {best[2]:.1%} vs {best[3]:.1%} in the other rows', float(best[1])])
        rows.sort(key=lambda r: (-abs(r[3]), r[1]))
        profiles += rows[:top]
    return sizes, profiles, names


# ---------------- unusual rows ----------------
def anomalies(frame, numeric, categorical, cfg, ids):
    """Isolation Forest scores; the top share of rows with the fields that make each one unusual."""
    from sklearn.ensemble import IsolationForest
    columns = []; explain = []
    for name in numeric:
        x = pd.to_numeric(frame[name], errors='coerce').to_numpy(dtype=float)
        median = np.nanmedian(x) if (~np.isnan(x)).any() else 0.0
        mad = 1.4826 * np.nanmedian(np.abs(x - median)) if (~np.isnan(x)).any() else 0.0
        scale = mad or (np.nanstd(x) or 1.0)
        columns.append(np.where(np.isnan(x), median, x))
        if np.isnan(x).any(): columns.append(np.isnan(x).astype(float))
        explain.append((name, 'number', (x - median) / scale, x))
    for name in categorical:
        values = frame[name].astype(object).where(frame[name].notna(), '(missing)').astype(str)
        share = values.map(values.value_counts(normalize=True)).to_numpy(dtype=float)
        columns.append(share)
        explain.append((name, 'category', share, values.to_numpy()))
    if not columns: return [], 0
    X = np.column_stack(columns)
    forest = IsolationForest(n_estimators=200, random_state=cfg.seed).fit(X)
    score = -forest.score_samples(X)                  # higher = more unusual
    flagged = max(1, int(round(cfg.anomaly_share * len(X))))
    order = np.lexsort((ids, -score))[:flagged]
    rows = []
    for i in order:
        reasons = []
        for name, kind, values, raw in explain:
            if kind == 'number':
                z = values[i]
                if not np.isnan(z) and abs(z) >= 3: reasons.append((abs(z), f'{name} = {raw[i]:,.4g} ({z:+.1f} robust sd)'))
                elif np.isnan(raw[i]): reasons.append((3.0, f'{name} missing'))
            elif values[i] < 0.01:
                reasons.append((3 + (0.01 - values[i]) * 100, f'{name} = {raw[i]} ({values[i]:.2%} of rows)'))
        reasons.sort(key=lambda r: -r[0])
        rows.append([int(ids[i]), float(score[i]), '; '.join(r[1] for r in reasons[:3]) or 'an unusual combination of ordinary values'])
    return rows, flagged


# ---------------- distribution shape ----------------
def shape(values, cfg):
    """Skewness, excess kurtosis, share beyond the outer fences and the dip test on a seeded sample."""
    from scipy import stats as sps
    x = np.asarray(values, dtype=float); x = x[~np.isnan(x)]
    if len(x) < 20 or np.ptp(x) == 0: return None
    q1, q3 = np.percentile(x, [25, 75]); iqr = q3 - q1
    far = float(((x < q1 - 3 * iqr) | (x > q3 + 3 * iqr)).mean()) if iqr > 0 else 0.0
    sample = x if len(x) <= cfg.dip_sample_rows else np.random.default_rng(cfg.seed).choice(x, cfg.dip_sample_rows, replace=False)
    distinct = len(np.unique(sample))
    dip, p = dip_test(sample, cfg.dip_simulations, cfg.seed) if distinct >= 20 else (None, None)
    skew = float(sps.skew(x)); kurt = float(sps.kurtosis(x))
    labels = []
    if abs(skew) >= 1: labels.append('right-skewed' if skew > 0 else 'left-skewed')
    if kurt >= 3 or far >= 0.01: labels.append('heavy tails')
    if p is not None and p < 0.05: labels.append('more than one mode')
    return {'n': int(len(x)), 'skewness': skew, 'kurtosis': kurt, 'far_share': far, 'dip': dip, 'dip_p': p,
            'dip_rows': int(len(sample)), 'labels': labels}
