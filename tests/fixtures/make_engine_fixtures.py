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


def leakage(rng, n=3000):
    """#2: 'copy' is the target with 2% of values flipped; 'closed_on' is a date recorded only when the outcome happened."""
    x = rng.normal(size=n); outcome = rng.random(n) < 1 / (1 + np.exp(-(x - 0.5)))
    flipped = np.where(rng.random(n) < 0.02, ~outcome, outcome)
    days = rng.integers(0, 365, n)
    closed = [(pd.Timestamp('2024-01-01') + pd.Timedelta(days=int(d))).date().isoformat() if o else '' for d, o in zip(days, outcome)]
    return pd.DataFrame({'x': np.round(x, 4), 'copy': np.where(flipped, 'Y', 'N'), 'closed_on': closed,
                         'region': rng.choice(['north', 'south', 'east'], size=n), 'outcome': np.where(outcome, 'yes', 'no')})


def nonlinear(rng, n=4000):
    """#7: the positive rate is U-shaped in x (high at both ends, low in the middle)."""
    x = rng.uniform(-3, 3, n); p = 1 / (1 + np.exp(-(-2.5 + 0.6 * x * x)))
    return pd.DataFrame({'x': np.round(x, 4), 'noise': np.round(rng.normal(size=n), 4), 'outcome': np.where(rng.random(n) < p, 'yes', 'no')})


def imbalance(rng, n=20000):
    """#17: overall positive rate about 1%; group 'b' about 2%, group 'a' about 0.5%."""
    group = rng.choice(['a', 'b'], size=n); rate = np.where(group == 'b', 0.02, 0.005)
    return pd.DataFrame({'group': group, 'z': np.round(rng.normal(size=n), 4), 'outcome': np.where(rng.random(n) < rate, 'yes', 'no')})


def simpson(rng, n=4000):
    """#5: within each stratum the treatment lowers the rate by 10 points, but treated rows sit mostly in the high-rate stratum."""
    stratum = rng.choice(['A', 'B'], size=n)
    treated = rng.random(n) < np.where(stratum == 'A', 0.9, 0.1)
    rate = np.where(stratum == 'A', 0.8, 0.3) - np.where(treated, 0.1, 0.0)
    return pd.DataFrame({'treatment': np.where(treated, 'yes', 'no'), 'stratum': stratum, 'noise': np.round(rng.normal(size=n), 4),
                         'outcome': np.where(rng.random(n) < rate, 'yes', 'no')})


def interaction(rng, n=4000):
    """#6: the outcome depends on a XOR b (rate 80% when they differ, 20% when equal); neither has a main effect."""
    a = rng.integers(0, 2, n); b = rng.integers(0, 2, n)
    rate = np.where(a != b, 0.8, 0.2)
    return pd.DataFrame({'a': np.where(a == 1, 'on', 'off'), 'b': np.where(b == 1, 'up', 'down'), 'c': np.round(rng.normal(size=n), 4),
                         'outcome': np.where(rng.random(n) < rate, 'yes', 'no')})


def null(rng, n=3000):
    """#8: no field is related to the outcome."""
    return pd.DataFrame({'p': np.round(rng.normal(size=n), 4), 'q': np.round(rng.exponential(size=n), 4), 'r': rng.choice(['x', 'y', 'z'], size=n),
                         's': rng.choice(['k', 'l'], size=n), 't': rng.integers(0, 50, n), 'outcome': np.where(rng.random(n) < 0.3, 'yes', 'no')})


def timeseries(rng, n=8000):
    """#11: daily rows over five years. sales = 100 + 0.1 per week (trend) + 15·sin(yearly) + 20 from 2021-07-05 (one break)
    + noise; 'channel' drifts from 20% to 70% online; 'region' and 'price' are stable; 'shipped_on' is always after 'order_date'."""
    days = pd.date_range('2019-01-01', '2023-12-31', freq='D')
    day = np.sort(rng.integers(0, len(days), n)); dates = days[day]
    week = (dates - days[0]).days / 7
    yearly = 15 * np.sin(2 * np.pi * dates.day_of_year.to_numpy() / 365.25)
    level = np.where(dates >= pd.Timestamp('2021-07-05'), 20.0, 0.0)
    sales = 100 + 0.1 * week + yearly + level + rng.normal(0, 20, n)
    online = rng.random(n) < 0.2 + 0.5 * day / (len(days) - 1)
    return pd.DataFrame({'order_date': dates.strftime('%Y-%m-%d'), 'shipped_on': (dates + pd.to_timedelta(rng.integers(1, 6, n), unit='D')).strftime('%Y-%m-%d'),
                         'channel': np.where(online, 'online', 'store'), 'region': rng.choice(['north', 'south', 'east', 'west'], size=n),
                         'price': np.round(rng.lognormal(3, 0.3, n), 2), 'sales': np.round(sales, 2)})


def unlabelled(rng, n=1500):
    """#12: three well-separated groups in two numbers and one category (90% of each group shares its colour), plus 1% extreme rows."""
    group = rng.integers(0, 3, n)
    centres = np.array([[0, 0], [8, 0], [4, 8]])
    xy = centres[group] + rng.normal(0, 1, (n, 2))
    colours = np.array(['red', 'blue', 'green'])
    colour = np.where(rng.random(n) < 0.9, colours[group], colours[rng.integers(0, 3, n)])
    odd = rng.choice(n, n // 100, replace=False)
    xy[odd] = rng.choice([-1, 1], (len(odd), 2)) * rng.uniform(25, 40, (len(odd), 2))
    return pd.DataFrame({'row_key': np.arange(1, n + 1), 'x': np.round(xy[:, 0], 3), 'y': np.round(xy[:, 1], 3), 'colour': colour,
                         'noise': np.round(rng.normal(size=n), 3)})


def messy(rng, n=600):
    """#9: Central European formats in cp1250 with a title row and semicolons: decimal comma with space thousands, euro amounts
    with accounting negatives, percentages, mixed date formats (day-first), ambiguous slash dates, mixed yes/no spellings and
    NA markers. Returns the file bytes; a UTF-8 copy with a byte-order mark is written next to it."""
    cities = np.array(['Kraków', 'Łódź', 'Brno', 'Ústí nad Labem', 'Košice', 'Žilina'])
    price = np.round(rng.uniform(5, 5000, n), 2)
    amount = np.round(rng.normal(800, 900, n), 2)
    discount = rng.choice([0, 5, 7.5, 10, 15], n)
    days = pd.to_datetime('2021-01-01') + pd.to_timedelta(rng.integers(0, 365, n), unit='D')
    style = rng.integers(0, 3, n)
    def number(v): return f'{v:,.2f}'.replace(',', ' ').replace('.', ',')
    def euro(v): text = f'{abs(v):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.') + ' €'; return f'({text})' if v < 0 else text
    rows = []
    for i in range(n):
        date = [days[i].strftime('%Y-%m-%d'), days[i].strftime('%d.%m.%Y'), f'{days[i].day} {days[i].strftime("%b %Y")}'][style[i]]
        flag = ['Y', 'N', 'yes', 'no'][rng.integers(0, 4)]
        rows.append([cities[i % len(cities)], number(price[i]) if i % 50 else 'N/A', euro(amount[i]), f'{discount[i]:g}%'.replace('.', ','), date,
                     f'{rng.integers(1, 13):02d}/{rng.integers(1, 13):02d}/2021', flag if i % 40 else '--', 'tak' if rng.random() < 0.3 + price[i] / 10000 else 'nie'])
    header = ['miasto', 'cena', 'kwota', 'rabat', 'data', 'termin', 'aktywny', 'kupił']
    body = 'Raport sprzedaży 2021 (wygenerowany)\n' + '\n'.join(';'.join(r) for r in [header] + rows) + '\n'
    (HERE / 'ENGINE-09-messy-bom.csv').write_bytes(b'\xef\xbb\xbf' + ('\n'.join(';'.join(r) for r in [header] + rows) + '\n').encode('utf-8'))
    return body.encode('cp1250')


def high_cardinality(rng, n=20000):
    """#10: 'code' has 5,000 levels. Three frequent levels (K0001–K0003, about 600 rows each) have a 60% positive rate;
    every other level, most with a handful of rows, has 20%."""
    special = np.array(['K0001', 'K0002', 'K0003'])
    is_special = rng.random(n) < 0.09
    code = np.where(is_special, special[rng.integers(0, 3, n)], np.char.add('K', np.char.zfill(rng.integers(4, 5001, n).astype(str), 4)))
    rate = np.where(is_special, 0.6, 0.2)
    return pd.DataFrame({'code': code, 'noise': np.round(rng.normal(size=n), 3), 'outcome': np.where(rng.random(n) < rate, 'yes', 'no')})


def tiny(rng, n=40):
    """#13: 40 rows; 'group' a has a 70% positive rate and b 30%; 'size' and 'colour' are unrelated."""
    group = np.array(['a', 'b'] * (n // 2))
    return pd.DataFrame({'group': group, 'size': np.round(rng.normal(10, 2, n), 2), 'colour': rng.choice(['red', 'green', 'blue'], n),
                         'outcome': np.where(rng.random(n) < np.where(group == 'a', 0.7, 0.3), 'yes', 'no')})


def targets(rng, n=3000):
    """#18: 'grade' (A/B/C) rises with x1 and depends on c1; 'score' (numeric) = 2·x2 + 3 for c2 = hi + noise; x3, c3 are unrelated."""
    x1, x2, x3 = rng.normal(size=(3, n))
    c1 = rng.choice(['p', 'q', 'r'], n); c2 = rng.choice(['hi', 'lo'], n); c3 = rng.choice(['u', 'v', 'w'], n)
    latent = 1.5 * x1 + np.where(c1 == 'p', 1.0, 0.0) + rng.logistic(size=n) * 0.6
    grade = np.where(latent > 0.8, 'A', np.where(latent > -0.5, 'B', 'C'))
    score = 2 * x2 + np.where(c2 == 'hi', 3.0, 0.0) + rng.normal(0, 1, n)
    return pd.DataFrame({'x1': np.round(x1, 4), 'x2': np.round(x2, 4), 'x3': np.round(x3, 4), 'c1': c1, 'c2': c2, 'c3': c3,
                         'grade': grade, 'score': np.round(score, 3)})


FIXTURES = {'ENGINE-01-conditional.csv': conditional, 'ENGINE-03-derived.csv': derived,
            'ENGINE-04-redundancy.csv': redundancy, 'ENGINE-16-missingness.csv': missingness, 'ENGINE-02-leakage.csv': leakage,
            'ENGINE-07-nonlinear.csv': nonlinear, 'ENGINE-17-imbalance.csv': imbalance, 'ENGINE-05-simpson.csv': simpson,
            'ENGINE-06-interaction.csv': interaction, 'ENGINE-08-null.csv': null, 'ENGINE-11-timeseries.csv': timeseries,
            'ENGINE-12-unlabelled.csv': unlabelled, 'ENGINE-09-messy.csv': messy, 'ENGINE-10-high-cardinality.csv': high_cardinality,
            'ENGINE-13-tiny.csv': tiny, 'ENGINE-18-targets.csv': targets}


if __name__ == '__main__':
    for index, (name, make) in enumerate(FIXTURES.items()):
        made = make(np.random.default_rng(1000 + index))
        if isinstance(made, bytes): (HERE / name).write_bytes(made)
        else: made.to_csv(HERE / name, index=False)
