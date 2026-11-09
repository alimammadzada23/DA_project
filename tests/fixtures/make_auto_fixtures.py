"""Writes the synthetic automatic-analysis fixtures. Deterministic: rerunning gives identical files.

All values are invented. Planted effects and problems are documented next to each column,
so tests can assert exact numbers.
"""
import csv
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).parent
SENTENCES = [f'Synthetic note number {i} describing a fictional interaction in plain words.' for i in range(50)]


def binary_rows():
    rows = []
    for i in range(400):
        region = 'ABCD'[i % 4]
        # Planted: region B has a positive rate of exactly 60%, the others 20%.
        churn = 'Yes' if (region == 'B' and (i // 4) % 10 < 6) or (region != 'B' and (i // 4) % 10 < 2) else 'No'
        tenure = 0 if i % 67 == 0 else 1 + i % 60          # rows 0, 67, …, 335: six rows with tenure 0
        amount = str(100 + 10 * (i % 50) + (50 if churn == 'Yes' else 0))
        if i in (5, 105, 205, 305): amount = 'N/A'         # planted NA-like markers in a numeric column
        balance = '' if tenure == 0 else f'{tenure * 12.5:.2f}'   # planted: missing exactly when tenure = 0
        segment = ['Retail', 'retail', ' Retail', 'Wholesale'][i % 4]   # case and whitespace variants
        signup = (date(2023, 1, 1) + timedelta(days=i)).isoformat()      # ISO dates stored as text
        local = date(2024, 1, 1) + timedelta(days=i % 300)
        ambiguous = date(2024, 1 + i % 12, 1 + i % 12)
        rows.append({'id': f'C{i:04d}', 'region': region, 'segment': segment, 'tenure': str(tenure), 'amount': amount,
                     'balance': balance, 'signup': signup, 'local_date': local.strftime('%d/%m/%Y'),
                     'ambiguous_date': ambiguous.strftime('%d/%m/%Y'), 'notes': SENTENCES[i % 50], 'constant': 'x',
                     'score': f' {i % 7} ', 'churn': churn})
    rows += [dict(rows[1]), dict(rows[2])]                 # planted: two exact duplicate rows
    return rows


def multiclass_rows():
    rows = []
    for i in range(300):
        level = ['low', 'mid', 'high'][i % 3]
        # Planted: size rises with the class; colour is independent of it.
        size = {'low': 10, 'mid': 20, 'high': 30}[level] + (i % 10)
        rows.append({'size': str(size), 'colour': ['red', 'green', 'blue', 'grey'][(i // 3) % 4], 'level': level})
    return rows


def numeric_rows():
    rows = []
    for i in range(360):
        group = ['north', 'south', 'east'][i % 3]
        x = i % 90
        # Planted: y = 2x + group offset + a small repeating wobble.
        y = 2 * x + {'north': 0, 'south': 40, 'east': 80}[group] + (i % 7) - 3
        day = date(2022, 1, 15) + timedelta(days=2 * i)
        rows.append({'x': str(x), 'group': group, 'day': day.isoformat(), 'y': str(y)})
    return rows


def write(name, rows):
    with (HERE / name).open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


if __name__ == '__main__':
    write('SYNTHETIC-auto-binary.csv', binary_rows())
    write('SYNTHETIC-auto-multiclass.csv', multiclass_rows())
    write('SYNTHETIC-auto-numeric.csv', numeric_rows())
