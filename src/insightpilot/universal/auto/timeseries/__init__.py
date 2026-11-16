"""Time analysis: granularity, aggregation level, trend, seasonality, structural breaks and distribution drift.

Formulas and references
- Mann–Kendall trend test (Mann 1945; Kendall 1975): S = Σ_{i<j} sign(y_j − y_i),
  Var(S) = [n(n−1)(2n+5) − Σ_g t_g(t_g−1)(2t_g+5)] / 18 over groups of tied values,
  Z = (S − sign(S)) / √Var(S), two-sided p = 2(1 − Φ(|Z|)), Kendall's τ = S / (n(n−1)/2).
  With positive lag-1 autocorrelation r > 1.96/√n of the detrended series, Var(S) is multiplied by
  1 + (2/n) Σ_{k=1}^{n−1} (n − k) r^k (Yue & Wang 2004). Seasonal series are tested after removing the STL seasonal part.
- Theil–Sen slope (Theil 1950; Sen 1968): the median of all pairwise slopes, with Sen's rank-based
  confidence interval (scipy.stats.theilslopes).
- STL decomposition (Cleveland et al. 1990; statsmodels, standard non-robust fitting). Seasonal strength
  F_S = max(0, 1 − Var(R) / Var(S + R)) (Wang, Smith & Hyndman 2006). Its p-value is the share of seeded
  permutations of the detrended series whose strength is at least as large.
- Structural breaks: binary segmentation with piecewise-linear segments (level and slope). A split is kept
  when n·ln(SSE_without / SSE_with) exceeds penalty × c × 3 × ln(n), a BIC-type rule for three added parameters
  (break position, level, slope), where c = (1 + ρ)/(1 − ρ) ∈ [1, 4] corrects for lag-1 autocorrelation ρ of the
  residuals of the best two-segment fit. Breaks whose level jump is under `break_min_jump_sd` noise standard deviations are not reported.
  The optional `ruptures` package is not required.
- Population stability index (PSI) = Σ_b (a_b − e_b) · ln(a_b / e_b), with bin shares floored at 1e-4.
  Conventions: below 0.1 stable, 0.1–0.25 moderate shift, above 0.25 major shift.
"""
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
import math

import numpy as np
from scipy import stats as sps

GRAINS = ['hour', 'day', 'week', 'month', 'quarter', 'year']
GRAIN_DAYS = {'hour': 1 / 24, 'day': 1, 'week': 7, 'month': 30.44, 'quarter': 91.31, 'year': 365.25}
SEASONS = {'hour': [(24, 'hour of day'), (168, 'hour of week')], 'day': [(7, 'day of week'), (365, 'day of year')],
           'week': [(52, 'week of year')], 'month': [(12, 'month of year')], 'quarter': [(4, 'quarter of year')], 'year': []}
PSI_FLOOR = 1e-4


def timestamp_expression(column):
    """ISO dates or timestamps as TIMESTAMP; values with a UTC offset are converted to UTC, naive values kept as written."""
    from ....datasets import quote
    text = f"NULLIF(trim(CAST({quote(column)} AS VARCHAR)),'')"
    iso = f"regexp_matches({text}, '^\\d{{4}}-\\d{{2}}-\\d{{2}}($|[T ])')"
    offset = f"regexp_matches({text}, '(Z|[+-]\\d{{2}}(:?\\d{{2}})?)$') AND length({text}) > 10"
    return (f'CASE WHEN {iso} AND {offset} THEN timezone(\'UTC\', try_cast({text} AS TIMESTAMPTZ)) '
            f'WHEN {iso} THEN try_cast({text} AS TIMESTAMP) END')


def native_grain(median_gap_days):
    """Granularity from the median gap between consecutive distinct timestamps."""
    if median_gap_days is None: return None
    if median_gap_days < 0.9: return 'sub-daily'
    if median_gap_days < 6.5: return 'daily'
    if median_gap_days < 27: return 'weekly'
    if median_gap_days < 88: return 'monthly'
    if median_gap_days < 360: return 'quarterly'
    return 'yearly'


FINEST = {'sub-daily': 'hour', 'daily': 'day', 'weekly': 'week', 'monthly': 'month', 'quarterly': 'quarter', 'yearly': 'year'}


def choose_grain(native, candidates, min_periods, min_rows):
    """The finest level with at least `min_periods` periods and `min_rows` rows per period on average.

    candidates: {grain: (periods spanned, rows)}. Falls back to the finest level with enough rows per period
    and at least 4 periods, then to the coarsest level, and says which rule applied.
    """
    start = GRAINS.index(FINEST.get(native, 'day'))
    usable = [g for g in GRAINS[start:] if g in candidates]
    for g in usable:
        periods, rows = candidates[g]
        if periods >= min_periods and rows / max(periods, 1) >= min_rows:
            return g, f'finest level with at least {min_periods} periods and {min_rows} rows per period on average'
    for g in usable:
        periods, rows = candidates[g]
        if periods >= 4 and rows / max(periods, 1) >= min_rows:
            return g, f'no level has {min_periods} periods with {min_rows} rows each; finest level with {min_rows} rows per period'
    return (usable[-1] if usable else 'year'), 'too few rows for the usual rule; coarsest level used'


def period_start(value, grain):
    if isinstance(value, datetime) and grain != 'hour': value = value.date()
    return value


def next_period(value, grain):
    if grain == 'hour': return value + timedelta(hours=1)
    if grain == 'day': return value + timedelta(days=1)
    if grain == 'week': return value + timedelta(days=7)
    months = {'month': 1, 'quarter': 3, 'year': 12}[grain]
    month = value.month - 1 + months
    return date(value.year + month // 12, month % 12 + 1, 1)


def all_periods(first, last, grain):
    out = []; current = first
    while current <= last and len(out) < 200_000:
        out.append(current); current = next_period(current, grain)
    return out


def label(value, grain):
    if grain == 'hour': return value.strftime('%Y-%m-%d %H:00')
    if grain == 'month': return value.strftime('%Y-%m')
    if grain == 'quarter': return f'{value.year}-Q{(value.month - 1) // 3 + 1}'
    if grain == 'year': return str(value.year)
    return value.isoformat()


# ---------------- tests ----------------
def mann_kendall(values):
    y = np.asarray([v for v in values if v is not None and math.isfinite(v)], dtype=float)
    n = len(y)
    if n < 4: return None
    diffs = np.sign(y[None, :] - y[:, None])
    s = float(np.triu(diffs, 1).sum())
    _, counts = np.unique(y, return_counts=True)
    ties = sum(t * (t - 1) * (2 * t + 5) for t in counts if t > 1)
    var = (n * (n - 1) * (2 * n + 5) - ties) / 18
    # Variance correction for positive lag-1 autocorrelation of the Theil–Sen-detrended series (Yue & Wang 2004):
    # Var*(S) = Var(S) · (1 + 2/n · Σ_{k=1}^{n−1} (n − k) r^k), applied when r exceeds 1.96/√n.
    x = np.arange(n, dtype=float)
    slope = sps.theilslopes(y, x)[0]; detrended = y - slope * x
    r = float(np.corrcoef(detrended[:-1], detrended[1:])[0, 1]) if np.std(detrended) > 0 else 0.0
    factor = 1 + 2 / n * sum((n - k) * r ** k for k in range(1, n)) if r > 1.96 / math.sqrt(n) else 1.0
    var *= factor
    z = 0.0 if var <= 0 or s == 0 else (s - math.copysign(1, s)) / math.sqrt(var)
    return {'n': n, 's': s, 'var': var, 'variance_factor': factor, 'lag1': r, 'z': z, 'p': float(2 * sps.norm.sf(abs(z))), 'tau': s / (n * (n - 1) / 2)}


def theil_sen(values, confidence=0.95):
    points = [(i, v) for i, v in enumerate(values) if v is not None and math.isfinite(v)]
    if len(points) < 4: return None
    x = np.array([p[0] for p in points], dtype=float); y = np.array([p[1] for p in points], dtype=float)
    slope, intercept, low, high = sps.theilslopes(y, x, alpha=confidence)
    return {'slope': float(slope), 'intercept': float(intercept), 'low': float(low), 'high': float(high)}


def fill_gaps(values):
    """Linear interpolation of missing periods (edges take the nearest value); used for STL only."""
    y = np.array([np.nan if v is None else v for v in values], dtype=float)
    index = np.arange(len(y)); ok = ~np.isnan(y)
    if ok.sum() < 2: return None
    return np.interp(index, index[ok], y[ok])


def stl(values, period, seed, permutations=99):
    """STL fit with seasonal strength, trend strength and a permutation p-value for the seasonal strength."""
    from statsmodels.tsa.seasonal import STL
    y = fill_gaps(values)
    if y is None or len(y) < 2 * period + 1 or np.std(y) == 0: return None
    fit = STL(y, period=period).fit()
    seasonal, trend, remainder = fit.seasonal, fit.trend, fit.resid

    def strength(part):
        total = np.var(part + remainder)
        return max(0.0, 1 - np.var(remainder) / total) if total > 0 else 0.0
    observed = strength(seasonal)
    detrended = y - trend
    rng = np.random.default_rng(seed); exceed = 0
    for _ in range(permutations):
        shuffled = rng.permutation(detrended)
        f = STL(shuffled + trend, period=period).fit()
        total = np.var(f.seasonal + f.resid)
        if total > 0 and max(0.0, 1 - np.var(f.resid) / total) >= observed: exceed += 1
    profile = [float(np.mean(seasonal[i::period])) for i in range(period)]
    return {'period': period, 'strength': float(observed), 'trend_strength': float(strength(trend)),
            'p': (exceed + 1) / (permutations + 1), 'seasonal': seasonal.tolist(), 'trend': trend.tolist(),
            'profile': profile, 'peak': int(np.argmax(profile)), 'trough': int(np.argmin(profile)),
            'amplitude': float(max(profile) - min(profile))}


def _linear_sse(y):
    n = len(y)
    if n < 3: return 0.0, (float(y.mean()) if n else 0.0), 0.0
    x = np.arange(n, dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    residual = y - (intercept + slope * x)
    return float(residual @ residual), float(intercept), float(slope)


def breaks(values, min_size=4, max_breaks=3, penalty=1.0):
    """Binary segmentation with piecewise-linear segments. Returns kept break indices with level and slope changes."""
    y = np.array([np.nan if v is None else v for v in values], dtype=float)
    ok = ~np.isnan(y)
    index = np.flatnonzero(ok); y = y[ok]; n = len(y)
    if n < 2 * min_size + 2: return []
    # Autocorrelated noise looks like level shifts; the penalty grows with the lag-1 autocorrelation of the
    # residuals, by the effective-sample-size factor (1 + ρ) / (1 − ρ), capped at 4. Residuals come from the best
    # two-segment fit, so a real break does not inflate ρ.
    def residuals(k):
        out = []
        for part in (y[:k], y[k:]):
            x = np.arange(len(part), dtype=float); out.append(part - np.polyval(np.polyfit(x, part, 1), x))
        return np.concatenate(out)
    split = min(range(min_size, n - min_size + 1), key=lambda k: _linear_sse(y[:k])[0] + _linear_sse(y[k:])[0])
    residual = residuals(split)
    rho = float(np.corrcoef(residual[:-1], residual[1:])[0, 1]) if np.std(residual) > 0 else 0.0
    inflation = min(4.0, max(1.0, (1 + rho) / (1 - rho))) if rho < 1 else 4.0
    threshold = penalty * inflation * 3 * math.log(n)
    found = []

    def best_split(lo, hi):
        segment = y[lo:hi]; total, _, _ = _linear_sse(segment)
        best = None
        for k in range(min_size, len(segment) - min_size + 1):
            left, _, _ = _linear_sse(segment[:k]); right, _, _ = _linear_sse(segment[k:])
            if best is None or left + right < best[1]: best = (k, left + right)
        if best is None or total <= 0: return None
        gain = len(segment) * math.log(total / max(best[1], 1e-12))
        return (lo + best[0], gain) if gain > threshold else None
    queue = [(0, n)]
    while queue and len(found) < max_breaks:
        candidates = [(s, (lo, hi)) for lo, hi in queue if (s := best_split(lo, hi))]
        if not candidates: break
        (position, gain), (lo, hi) = max(candidates, key=lambda c: c[0][1])
        found.append((position, gain)); queue.remove((lo, hi)); queue += [(lo, position), (position, hi)]
    result = []
    for position, gain in sorted(found):
        before_lo = max([p for p, _ in found if p < position], default=0)
        after_hi = min([p for p, _ in found if p > position], default=n)
        _, a0, a1 = _linear_sse(y[before_lo:position]); _, b0, b1 = _linear_sse(y[position:after_hi])
        before_end = a0 + a1 * (position - before_lo)          # left line extended to the break period
        before_mean, after_mean = float(y[before_lo:position].mean()), float(y[position:after_hi].mean())
        result.append({'index': int(index[position]), 'gain': float(gain), 'level_before': before_end, 'level_after': b0,
                       'jump': b0 - before_end, 'slope_before': a1, 'slope_after': b1,
                       'mean_before': before_mean, 'mean_after': after_mean})
    return result


def psi(expected, actual):
    """Population stability index of two count vectors over the same bins."""
    e = np.asarray(expected, dtype=float); a = np.asarray(actual, dtype=float)
    if e.sum() == 0 or a.sum() == 0: return None
    e = np.maximum(e / e.sum(), PSI_FLOOR); a = np.maximum(a / a.sum(), PSI_FLOOR)
    return float(np.sum((a - e) * np.log(a / e)))


def psi_label(value, moderate, major):
    if value is None: return ''
    return 'major shift' if value > major else 'moderate shift' if value > moderate else 'stable'


@dataclass
class SeriesResult:
    name: str
    unit: str                      # 'rows', 'rate' or 'mean'
    labels: list = field(default_factory=list)
    values: list = field(default_factory=list)
    used: list = field(default_factory=list)       # indices used for tests (complete periods with a value)
    trend: dict | None = None
    seasonality: list = field(default_factory=list)
    breaks: list = field(default_factory=list)
    note: str = ''
    grain: str = ''

    def to_dict(self):
        out = asdict(self)
        for s in out['seasonality']: s.pop('seasonal', None); s.pop('trend', None)
        return out


def analyse_series(name, unit, labels, values, complete, grain, cfg):
    """Trend, seasonality and breaks of one aggregated series. `complete` marks periods fully inside the data range."""
    result = SeriesResult(name, unit, labels, values)
    result.grain = grain
    used = [i for i, v in enumerate(values) if v is not None and complete[i]]
    result.used = used
    if len(used) < cfg.time_min_periods_tests:
        result.note = f'{len(used)} complete periods; at least {cfg.time_min_periods_tests} are needed for trend, seasonality and break tests.'
        return result
    lo, hi = used[0], used[-1] + 1
    window = [values[i] if (complete[i] and values[i] is not None) else None for i in range(lo, hi)]
    deseasonalised = window
    for period, meaning in SEASONS.get(grain, []):
        if len([v for v in window if v is not None]) < 2 * period + 1: continue
        if sum(v is None for v in window) > 0.1 * len(window): continue
        fit = stl(window, period, cfg.seed, cfg.seasonal_permutations)
        if not fit: continue
        fit['meaning'] = meaning
        result.seasonality.append(fit)
    strong = [s for s in result.seasonality if s['p'] <= cfg.seasonal_p_maximum and s['strength'] >= cfg.seasonal_strength_minimum]
    if strong:
        main = max(strong, key=lambda s: s['strength'])
        deseasonalised = [None if v is None else v - main['seasonal'][i] for i, v in enumerate(window)]
    mk = mann_kendall(deseasonalised); ts = theil_sen(deseasonalised, cfg.confidence)
    if mk and ts:
        present = [v for v in window if v is not None]
        level = float(np.mean(np.abs(present))) if present else 0.0
        change = ts['slope'] * (len(window) - 1)
        result.trend = {**mk, **ts, 'periods': len(present), 'change': change, 'level': level,
                        'relative_change': change / level if level else None, 'deseasonalised': bool(strong),
                        'first': labels[lo], 'last': labels[hi - 1]}
    found = breaks(deseasonalised, cfg.break_min_periods, cfg.break_max, cfg.break_penalty)
    for b in found:
        b['label'] = labels[lo + b['index']]
        noise = float(np.std(np.diff([v for v in deseasonalised if v is not None]))) / math.sqrt(2) or 1e-12
        b['jump_sd'] = b['jump'] / noise
    result.breaks = [b for b in found if abs(b['jump_sd']) >= cfg.break_min_jump_sd]
    return result


def spanned(start, end, grain):
    """Number of calendar periods from the period containing `start` to the one containing `end`."""
    if grain == 'hour': return int((end - start).total_seconds() // 3600) + 1
    days = (end - start).days if not isinstance(end, datetime) else (end.date() - start.date()).days
    if grain == 'day': return days + 1
    if grain == 'week': return days // 7 + 1
    months = (end.year - start.year) * 12 + end.month - start.month
    return {'month': months + 1, 'quarter': months // 3 + 1, 'year': end.year - start.year + 1}[grain]


def phase_label(value, meaning):
    """Readable position of a period within a seasonal cycle."""
    if meaning == 'hour of day': return f'{value.hour:02d}:00'
    if meaning == 'hour of week': return f'{value.strftime("%A")} {value.hour:02d}:00'
    if meaning == 'day of week': return value.strftime('%A')
    if meaning == 'day of year': return value.strftime('%d %B')
    if meaning == 'week of year': return f'ISO week {value.isocalendar()[1]}'
    if meaning == 'month of year': return value.strftime('%B')
    if meaning == 'quarter of year': return f'Q{(value.month - 1) // 3 + 1}'
    return str(value)


def as_datetime(value):
    return value if isinstance(value, datetime) else datetime(value.year, value.month, value.day)


def numeric_bin_labels(edges):
    if not edges: return ['all values']
    shown = [f'{e:,.4g}' for e in edges]
    return [f'≤ {shown[0]}'] + [f'{shown[i - 1]} to {shown[i]}' for i in range(1, len(edges))] + [f'> {shown[-1]}']
