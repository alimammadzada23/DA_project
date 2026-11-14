"""Format detection: file encoding and layout, and value formats inside text columns.

Nothing is guessed silently. Each detection returns what it found, how many values support it, and either a
conversion step (from fixed templates in profile.py, applied as a new table version with an error ledger) or a
decision left to the analyst (ambiguous separators, ambiguous day/month order, mixed currencies, two-digit years,
numbers that may be Excel serial dates or Unix timestamps).

File layout (opt-in at import): byte-order marks; UTF-8, else cp1250 or cp1252 chosen by characters typical of
each code page; the delimiter with the most consistent field count; leading rows before the table (titles, notes)
detected as rows whose field count differs from the table's.
"""
import csv
from datetime import datetime
import io
from pathlib import Path
import re

from ...profile import BOOLEAN_FALSE, BOOLEAN_TRUE, CURRENCY_SYMBOLS

ACCEPT_SHARE = 0.95          # share of rows (by count) a format must explain before a column is converted
DELIMITERS = (',', ';', '\t', '|')
CENTRAL = set('ąćęłńśźżĄĆĘŁŃŚŹŻčďěňřšťůžČĎĚŇŘŠŤŮŽľĺŕĽĹŔőűŐŰ')
SPECIFIC_1250 = set('ŚŤŹśťźĽľąĄ')          # letters present in cp1250 but not cp1252
EXCEL_DATE_HINTS = ('date', 'day', 'time', 'timestamp', 'created', 'updated', 'at', 'on', 'dt')


# ---------------- file layout ----------------
def detect_encoding(data):
    """(encoding, reason) for the first bytes of a file."""
    if data.startswith(b'\xef\xbb\xbf'): return 'utf-8-sig', 'UTF-8 byte-order mark'
    if data.startswith((b'\xff\xfe', b'\xfe\xff')): return 'utf-16', 'UTF-16 byte-order mark'
    try:
        data.decode('utf-8'); return 'utf-8', 'valid UTF-8'
    except UnicodeDecodeError as exc:
        if exc.start > len(data) - 4:      # a multi-byte character cut by the sample boundary
            try:
                data[:exc.start].decode('utf-8'); return 'utf-8', 'valid UTF-8'
            except UnicodeDecodeError:
                pass
    as1250 = data.decode('cp1250', errors='replace'); as1252 = data.decode('cp1252', errors='replace')
    score1250 = sum(ch in CENTRAL for ch in as1250) + 3 * sum(ch in SPECIFIC_1250 for ch in as1250)
    score1252 = sum(ch in 'àâæçèéêëîïôœùûüÿñáíóúãõäöß' for ch in as1252.lower())
    if score1250 > score1252: return 'cp1250', f'not UTF-8; {score1250} Central European letters under cp1250'
    return 'cp1252', 'not UTF-8; Western European code page (cp1252) assumed'


def detect_layout(text, delimiter=None):
    """Delimiter, the number of leading rows before the table, and the field count."""
    lines = text.splitlines()[:200]
    best = None
    for candidate in ([delimiter] if delimiter else DELIMITERS):
        counts = [len(row) for row in csv.reader(io.StringIO('\n'.join(lines)), delimiter=candidate)]
        if not counts: continue
        modal = max(set(counts), key=lambda c: (counts.count(c), c))
        if modal < 2: continue
        consistent = counts.count(modal) / len(counts)
        if best is None or (consistent, modal) > (best[1], best[2]): best = (candidate, consistent, modal, counts)
    if best is None: return {'delimiter': delimiter or ',', 'skip_rows': 0, 'fields': 1, 'consistency': 0.0}
    candidate, consistency, modal, counts = best
    skip = 0
    while skip < len(counts) and counts[skip] != modal: skip += 1
    return {'delimiter': candidate, 'skip_rows': skip, 'fields': modal, 'consistency': consistency}


def prepare_file(path, directory, delimiter=None):
    """A UTF-8 copy of a delimited file without leading non-table rows, plus a report of what was detected."""
    path = Path(path)
    raw = path.read_bytes()
    encoding, reason = detect_encoding(raw[:262_144])
    text = raw.decode(encoding, errors='strict')
    layout = detect_layout(text[:200_000], delimiter)
    lines = text.splitlines(keepends=True)
    skipped = [line.rstrip('\r\n') for line in lines[:layout['skip_rows']]]
    target = Path(directory) / (path.stem + '.utf8.csv')
    target.write_text(''.join(lines[layout['skip_rows']:]), encoding='utf-8', newline='')
    report = {'source': path.name, 'encoding': encoding, 'encoding_reason': reason, 'delimiter': layout['delimiter'],
              'skipped_rows': len(skipped), 'skipped_preview': [s[:120] for s in skipped[:5]], 'fields': layout['fields']}
    return target, report


# ---------------- value formats ----------------
CURRENCY_CODES = ('USD', 'EUR', 'GBP', 'JPY', 'CNY', 'CHF', 'PLN', 'CZK', 'HUF', 'SEK', 'NOK', 'DKK', 'RUB', 'INR', 'BRL', 'CAD', 'AUD',
                  'NZD', 'MXN', 'TRY', 'ZAR', 'KRW', 'HKD', 'SGD', 'RON', 'BGN', 'UAH', 'ILS', 'AED', 'SAR', 'AZN', 'GEL', 'KZT')
CURRENCY = '|'.join(re.escape(c) for c in sorted(CURRENCY_SYMBOLS + CURRENCY_CODES, key=len, reverse=True))
TOKEN = re.compile(rf'^(?P<open>\()?\s*(?P<s1>[-+])?\s*(?P<c1>{CURRENCY})?\s*(?P<s2>[-+])?\s*(?P<num>\d[\d.,\'   ]*)\s*(?P<c2>{CURRENCY})?\s*(?P<pct>%)?\s*(?P<close>\))?$')
CONVENTIONS = {
    ('.', ','): re.compile(r'^(\d+|\d{1,3}(,\d{3})+)(\.\d+)?([eE][-+]?\d+)?$'),
    ('.', ' '): re.compile(r'^(\d+|\d{1,3}([   ]\d{3})+)(\.\d+)?$'),
    ('.', "'"): re.compile(r"^(\d+|\d{1,3}('\d{3})+)(\.\d+)?$"),
    (',', '.'): re.compile(r'^(\d+|\d{1,3}(\.\d{3})+)(,\d+)?$'),
    (',', ' '): re.compile(r'^(\d+|\d{1,3}([   ]\d{3})+)(,\d+)?$'),
}


def detect_number(values):
    """A number_format step, a decision, or None from [(value, rows)] of one text column."""
    total = sum(n for _, n in values)
    if not total: return None
    parsed = []; currencies = {}; percent = 0; accounting = 0
    for value, n in values:
        m = TOKEN.match(str(value).strip())
        if not m or (m['open'] is None) != (m['close'] is None): continue
        currency = m['c1'] or m['c2']
        if currency: currencies[currency] = currencies.get(currency, 0) + n
        if m['pct']: percent += n
        if m['open']: accounting += n
        parsed.append((m['num'].strip(), n))
    if sum(n for _, n in parsed) < ACCEPT_SHARE * total: return None
    support = {}
    for convention, pattern in CONVENTIONS.items():
        support[convention] = sum(n for num, n in parsed if pattern.match(num))
    plain = sum(n for num, n in parsed if re.fullmatch(r'\d+(\.\d+)?([eE][-+]?\d+)?', num))
    if plain == total and not currencies and not percent and not accounting: return None     # already plain numbers
    ranked = sorted(support.items(), key=lambda kv: (-kv[1], kv[0]))
    (best, best_rows), (second, second_rows) = ranked[0], ranked[1]
    if best_rows < ACCEPT_SHARE * total: return None
    if len(currencies) > 1:
        return {'decision': 'mixed currencies (' + ', '.join(sorted(currencies)) + '); not converted', 'rows': total}
    if 0 < percent < ACCEPT_SHARE * total:
        return {'decision': f'percent signs on {percent:,} of {total:,} rows; not converted', 'rows': total}
    if any(rows == best_rows and convention[0] != best[0] for convention, rows in ranked[1:]):
        unit = 'currency ' + next(iter(currencies)) if currencies else ''
        return {'decision': 'decimal and thousands separators are ambiguous (for example 1,234 or 1.234)' + (f'; {unit}' if unit else '')
                            + '; not converted', 'rows': total}
    step = {'kind': 'number_format', 'decimal': best[0], 'thousands': best[1] if any(sep in num for num, _ in parsed for sep in
                                                                                       ([best[1]] if best[1] != ' ' else [' ', ' ', ' '])) else None}
    if step['thousands'] == step['decimal']: step['thousands'] = None
    parts = [f'decimal {"comma" if best[0] == "," else "point"}']
    if step['thousands']: parts.append('thousands separator ' + {'.': 'point', ',': 'comma', ' ': 'space', "'": 'apostrophe'}[step['thousands']])
    if currencies: step['currency'] = next(iter(currencies)); parts.append(f'currency marker {step["currency"]} removed')
    if percent: step['percent'] = True; parts.append('percent values divided by 100')
    if accounting: step['accounting'] = True; parts.append('parentheses read as negative')
    return {'step': step, 'rows': best_rows, 'reason': 'formatted numbers stored as text (' + ', '.join(parts) + ')'}


def detect_boolean(values):
    """A boolean step when a column mixes several spellings of yes/no (more than two distinct values)."""
    spellings = {str(v).strip(): n for v, n in values}
    if len(spellings) <= 2: return None
    lower = {v.lower() for v in spellings}
    trues = sorted(lower & set(BOOLEAN_TRUE)); falses = sorted(lower & set(BOOLEAN_FALSE))
    if not trues or not falses or (set(trues) | set(falses)) != lower: return None
    if lower <= {'0', '1'}: return None
    return {'step': {'kind': 'boolean', 'true_values': trues, 'false_values': falses}, 'rows': sum(spellings.values()),
            'reason': f'{len(spellings)} spellings of yes and no ({", ".join(sorted(spellings))}) unified as true / false'}


AUTO_DATE_FORMATS = ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d.%m.%Y', '%d-%m-%Y', '%m-%d-%Y', '%Y/%m/%d', '%Y.%m.%d',
                     '%d %b %Y', '%d %B %Y', '%b %d, %Y', '%B %d, %Y', '%d-%b-%Y', '%Y-%m-%d %H:%M', '%Y-%m-%d %H:%M:%S',
                     '%d/%m/%Y %H:%M', '%m/%d/%Y %H:%M', '%d.%m.%Y %H:%M')
DAY_FIRST = {'%d/%m/%Y', '%d-%m-%Y', '%d/%m/%Y %H:%M'}
MONTH_FIRST = {'%m/%d/%Y', '%m-%d-%Y', '%m/%d/%Y %H:%M'}


def _parses(value, fmt):
    try:
        datetime.strptime(value, fmt); return True
    except ValueError:
        return False


def detect_dates(values):
    """A date step with the formats that explain the column, or a decision (ambiguous order, two-digit years)."""
    total = sum(n for _, n in values)
    if not total: return None
    texts = [(str(v).strip(), n) for v, n in values]
    head = texts[:200]
    if sum(1 for v, _ in head if len(v) <= 30 and re.search(r'\d', v) and re.search(r'[-/., ]', v)) < 0.5 * len(head): return None
    support = {f: [(v, n) for v, n in texts if _parses(v, f)] for f in AUTO_DATE_FORMATS}
    covered = set()
    for f, hits in support.items():
        for v, _ in hits: covered.add(v)
    rows_covered = sum(n for v, n in texts if v in covered)
    if rows_covered < ACCEPT_SHARE * total:
        if sum(n for v, n in texts if re.fullmatch(r'\d{1,2}[/.-]\d{1,2}[/.-]\d{2}', v)) >= ACCEPT_SHARE * total:
            return {'decision': 'dates with two-digit years; not converted (the century would be a guess)', 'rows': total}
        return None
    day = {v for f in DAY_FIRST for v, _ in support[f]}; month = {v for f in MONTH_FIRST for v, _ in support[f]}
    only_day, only_month = day - month, month - day
    if only_day and only_month:
        return {'decision': 'both day-first and month-first dates occur; not converted', 'rows': total}
    if (day & month) and not only_day and not only_month:
        return {'decision': 'dates with ambiguous day/month order (no day above 12); not converted', 'rows': total}
    banned = MONTH_FIRST if only_day else DAY_FIRST if only_month else set()
    chosen = []; remaining = {v: n for v, n in texts if v in covered}
    while remaining and len(chosen) < 6:
        f = max((f for f in AUTO_DATE_FORMATS if f not in banned and f not in chosen),
                key=lambda f: (sum(n for v, n in support[f] if v in remaining), -AUTO_DATE_FORMATS.index(f)))
        gained = [v for v, _ in support[f] if v in remaining]
        if not gained: break
        chosen.append(f)
        for v in gained: remaining.pop(v, None)
    if chosen == ['%Y-%m-%d']: return None      # plain ISO dates are handled by the existing date step
    rows = total - sum(remaining.values())
    if rows < ACCEPT_SHARE * total: return None
    return {'step': {'kind': 'date', 'formats': chosen}, 'rows': rows,
            'reason': 'dates stored as text (' + ', '.join(chosen) + ('; day-first' if only_day else '; month-first' if only_month else '') + ')'}


def numeric_time_hint(name, minimum, maximum, integers):
    """A decision when integer values look like Excel serial dates or Unix timestamps (reported, never converted)."""
    if not integers or minimum is None: return None
    words = set(re.sub(r'[^a-z0-9]+', ' ', re.sub(r'([a-z])([A-Z])', r'\1 \2', name).lower()).split())
    if 1e9 <= minimum and maximum <= 2.2e9: return 'values look like Unix timestamps in seconds (2001–2039); not converted'
    if 1e12 <= minimum and maximum <= 2.2e12: return 'values look like Unix timestamps in milliseconds; not converted'
    if words & set(EXCEL_DATE_HINTS) and 20_000 <= minimum and maximum <= 80_000:
        return 'values look like Excel serial dates (name hint and range 1954–2119); not converted'
    return None
