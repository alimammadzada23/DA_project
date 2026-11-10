"""Planted-truth fixtures for the analyst engine (spec §9.2). Seeded: rerunning gives identical files.

All values are synthetic. Each generator documents the truth it plants.
"""
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent


def conditional(rng, n=3000):
    """#1: 'addon' has level 'No service' exactly when service = 'none'; among other rows it is unrelated to the target."""
    service = rng.choice(['none', 'basic', 'premium'], size=n, p=[0.3, 0.4, 0.3])
    addon = np.where(service == 'none', 'No service', rng.choice(['Yes', 'No'], size=n))
    rate = np.where(service == 'none', 0.10, 0.35)
    return pd.DataFrame({'service': service, 'addon': addon, 'region': rng.choice(['north', 'south', 'east'], size=n),
                         'outcome': np.where(rng.random(n) < rate, 'yes', 'no')})


def derived(rng, n=3000):
    """#3: c = a × b × (1 + 1% noise); the target depends on a only."""
    a = np.round(rng.uniform(1, 10, n), 2); b = np.round(rng.uniform(1, 50, n), 2)
    c = np.round(a * b * (1 + rng.normal(0, 0.01, n)), 2)
    outcome = rng.random(n) < 1 / (1 + np.exp(-(a - 5.5)))
    return pd.DataFrame({'a': a, 'b': b, 'c': c, 'd': np.round(rng.normal(size=n), 3), 'outcome': np.where(outcome, 'yes', 'no')})


def redundancy(rng, n=3000):
    """#4: x2 = x1 + noise (ρ ≈ 0.9); only x1 enters the target; x3 is unrelated."""
    x1 = rng.normal(size=n); x2 = x1 + rng.normal(0, 0.45, n); x3 = rng.normal(size=n)
    outcome = rng.random(n) < 1 / (1 + np.exp(-1.5 * x1))
    return pd.DataFrame({'x1': np.round(x1, 4), 'x2': np.round(x2, 4), 'x3': np.round(x3, 4), 'outcome': np.where(outcome, 'yes', 'no')})


def missingness(rng, n=2000):
    """#16: b is missing exactly when a = 0; c is missing at random in 5% of rows."""
    a = rng.integers(0, 4, n)
    b = np.where(a == 0, np.nan, np.round(rng.normal(50, 10, n), 2))
    c = np.where(rng.random(n) < 0.05, np.nan, np.round(rng.normal(size=n), 3))
    return pd.DataFrame({'a': a, 'b': b, 'c': c, 'group': rng.choice(['p', 'q', 'r'], size=n),
                         'outcome': np.where(rng.random(n) < 0.3, 'yes', 'no')})


FIXTURES = {'ENGINE-01-conditional.csv': conditional, 'ENGINE-03-derived.csv': derived,
            'ENGINE-04-redundancy.csv': redundancy, 'ENGINE-16-missingness.csv': missingness}


if __name__ == '__main__':
    for index, (name, make) in enumerate(FIXTURES.items()):
        make(np.random.default_rng(1000 + index)).to_csv(HERE / name, index=False)
