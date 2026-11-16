"""Hartigan's dip statistic and test of unimodality (Hartigan & Hartigan 1985, algorithm AS 217).

The dip is the largest difference between the empirical distribution function and the closest unimodal
distribution function: D = inf_{G unimodal} sup_x |F_n(x) − G(x)|. It is computed with the greatest convex
minorant / least concave majorant algorithm of AS 217 (as in the R package diptest, Maechler 2016).
The p-value is the share of seeded samples from the uniform distribution (the least favourable unimodal
case) of the same size whose dip is at least as large.
"""
from functools import lru_cache

import numpy as np


def dip_statistic(values):
    """Dip of a sample (ties allowed); between 1/(2n) and 1/4."""
    x = np.sort(np.asarray(values, dtype=float))
    n = len(x)
    if n < 2 or x[0] == x[-1]: return 0.5 / max(n, 1)
    x = np.concatenate([[0.0], x])          # 1-based indexing as in AS 217
    low, high = 1, n
    dip = 1.0
    mn = np.zeros(n + 1, dtype=int); mj = np.zeros(n + 1, dtype=int)
    # Indices for the greatest convex minorant.
    mn[1] = 1
    for j in range(2, n + 1):
        mn[j] = j - 1
        while True:
            mnj = mn[j]; mnmnj = mn[mnj]
            if mnj == 1 or (x[j] - x[mnj]) * (mnj - mnmnj) < (x[mnj] - x[mnmnj]) * (j - mnj): break
            mn[j] = mnmnj
    # Indices for the least concave majorant.
    mj[n] = n
    for k in range(n - 1, 0, -1):
        mj[k] = k + 1
        while True:
            mjk = mj[k]; mjmjk = mj[mjk]
            if mjk == n or (x[k] - x[mjk]) * (mjk - mjmjk) < (x[mjk] - x[mjmjk]) * (k - mjk): break
            mj[k] = mjmjk
    gcm = np.zeros(n + 2, dtype=int); lcm = np.zeros(n + 2, dtype=int)
    while True:
        gcm[1] = high; ic = 1
        while gcm[ic] > low:
            gcm[ic + 1] = mn[gcm[ic]]; ic += 1
        l_gcm = ic
        lcm[1] = low; ic = 1
        while lcm[ic] < high:
            lcm[ic + 1] = mj[lcm[ic]]; ic += 1
        l_lcm = ic
        ig, ih, ix, iv = l_gcm, l_lcm, l_gcm - 1, 2
        d = 0.0
        if l_gcm != 2 or l_lcm != 2:
            while True:
                gcmix = gcm[ix]; lcmiv = lcm[iv]
                if gcmix > lcmiv:
                    gcmi1 = gcm[ix + 1]
                    dx = (lcmiv - gcmi1 + 1) - (x[lcmiv] - x[gcmi1]) * (gcmix - gcmi1) / (x[gcmix] - x[gcmi1])
                    iv += 1
                    if dx >= d: d = dx; ig = ix + 1; ih = iv - 1
                else:
                    lcmiv1 = lcm[iv - 1]
                    dx = (x[gcmix] - x[lcmiv1]) * (lcmiv - lcmiv1) / (x[lcmiv] - x[lcmiv1]) - (gcmix - lcmiv1 - 1)
                    ix -= 1
                    if dx >= d: d = dx; ig = ix + 1; ih = iv
                if ix < 1: ix = 1
                if iv > l_lcm: iv = l_lcm
                if gcm[ix] == lcm[iv]: break
        else:
            d = 1.0
        if d < dip: break
        dip_l = 0.0
        for j in range(ig, l_gcm):
            max_t = 1.0; jb, je = gcm[j + 1], gcm[j]
            if je - jb > 1 and x[je] != x[jb]:
                c = (je - jb) / (x[je] - x[jb])
                t = (np.arange(jb, je + 1) - jb + 1) - (x[jb:je + 1] - x[jb]) * c
                max_t = max(max_t, float(t.max()))
            dip_l = max(dip_l, max_t)
        dip_u = 0.0
        for j in range(ih, l_lcm):
            max_t = 1.0; jb, je = lcm[j], lcm[j + 1]
            if je - jb > 1 and x[je] != x[jb]:
                c = (je - jb) / (x[je] - x[jb])
                t = (x[jb:je + 1] - x[jb]) * c - (np.arange(jb, je + 1) - jb - 1)
                max_t = max(max_t, float(t.max()))
            dip_u = max(dip_u, max_t)
        dip = max(dip, dip_u, dip_l)
        if low == gcm[ig] and high == lcm[ih]: break
        low, high = gcm[ig], lcm[ih]
    return dip / (2 * n)


@lru_cache(maxsize=32)
def null_dips(n, simulations, seed):
    rng = np.random.default_rng(seed)
    return np.sort([dip_statistic(rng.random(n)) for _ in range(simulations)])


def dip_test(values, simulations=200, seed=42):
    """(dip, p-value) with the p-value simulated from uniform samples of the same size."""
    values = np.asarray(values, dtype=float)
    observed = dip_statistic(values)
    null = null_dips(len(values), simulations, seed)
    exceed = len(null) - np.searchsorted(null, observed - 1e-12, side='left')
    return observed, (exceed + 1) / (simulations + 1)
