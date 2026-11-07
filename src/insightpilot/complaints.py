"""Official CFPB period extracts and descriptive complaint intelligence.

No exposure-normalized complaint rate, cause, loss or response-time KPI is
inferred. 'Timely response?' is the publisher's categorical field, not a duration.
"""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import csv
import io
import json
from pathlib import Path
import uuid

import duckdb
import httpx
import pandas as pd

from .engine_settings import resources
from .datasets import ScopedAnalytics, TableFilters, digest, write_metadata

SOURCE = 'https://www.consumerfinance.gov/data-research/consumer-complaints/'
API = SOURCE + 'search/api/v1/'
DOCS = 'https://cfpb.github.io/ccdb5-api/'
LIMITATIONS = ('Reported complaints are a selected, changing subset of consumer experiences, not a representative survey. '
              'Counts do not measure company quality: customer exposure, market share and reporting behavior differ. '
              'No defensible exposure denominator is present. Timely response is the reported Yes/No category; '
              'missing values are excluded from that percentage. Recent complaints may be incomplete or still in progress. '
              'Observed concentrations and changes are investigation signals, not demonstrated causes.')

FIELDS = {'Complaint ID': 'complaint_id', 'Date received': 'record_date', 'Product': 'product',
          'Sub-product': 'sub_product', 'Issue': 'issue', 'Sub-issue': 'sub_issue', 'Company': 'company',
          'State': 'state', 'Submitted via': 'submitted_via', 'Date sent to company': 'date_sent',
          'Company response to consumer': 'response', 'Timely response?': 'timely',
          'Consumer disputed?': 'consumer_disputed'}
REQUIRED = {'Complaint ID', 'Date received', 'Product', 'Issue', 'Company',
            'Company response to consumer', 'Timely response?'}
DATE_PARSER_VERSION = 2
DATE_POLICY = ('ISO 8601 timestamps are converted to UTC calendar dates; offset-free timestamps '
               'are interpreted as UTC. YYYY-MM-DD and MM/DD/YYYY retain their calendar date. '
               'Invalid or missing dates remain NULL and are excluded only from date analyses. '
               'An import with no usable received dates is rejected.')


class UnusableComplaintDates(ValueError):
    """The source/snapshot cannot support any received-date analysis."""


def _date(value):
    if not value or not value.strip(): return None
    text = value.strip()
    try:
        # Do not take text[:10]: offset timestamps can cross a UTC day boundary.
        instant = datetime.fromisoformat(text[:-1] + '+00:00' if text.endswith(('Z', 'z')) else text)
        if instant.tzinfo is not None: instant = instant.astimezone(timezone.utc)
        return instant.date()
    except (ValueError, OverflowError): pass
    try: return datetime.strptime(text, '%m/%d/%Y').date()
    except ValueError: pass
    return None


def _normalized(source, coverage=None):
    """Independent CSV normalization; keep raw source rows and count every exclusion."""
    with Path(source).open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        header = reader.fieldnames or []
        if len(set(header)) != len(header) or not REQUIRED <= set(header):
            raise ValueError('CFPB CSV schema mismatch; missing required fields: ' + ', '.join(sorted(REQUIRED - set(header))))
        rows, raw, seen = [], [], {}
        qa = {'source_rows': 0, 'missing_or_invalid_ids': 0, 'duplicate_ids': 0,
              'conflicting_duplicate_ids': 0, 'invalid_received_dates': 0,
              'invalid_sent_dates': 0, 'unrecognized_timely_values': 0, 'missing_timely': 0,
              'outside_requested_coverage': 0, 'missing_received_dates': 0, 'missing_sent_dates': 0}
        for number, source_row in enumerate(reader, 1):
            if number > 500_000: raise ValueError('Extract exceeds 500,000 rows; choose a shorter period.')
            if None in source_row or any(value is None for value in source_row.values()):
                raise ValueError(f'Malformed CSV row {number}; no partial import was retained.')
            qa['source_rows'] += 1
            raw.append({'source_row': number, 'attributes': json.dumps(source_row)})
            identifier = source_row['Complaint ID'].strip()
            if not identifier.isdigit() or int(identifier) <= 0:
                qa['missing_or_invalid_ids'] += 1; continue
            identifier = str(int(identifier))
            if identifier in seen:
                qa['duplicate_ids'] += 1
                if seen[identifier] != source_row: qa['conflicting_duplicate_ids'] += 1
                continue
            seen[identifier] = source_row
            row = {target: source_row.get(field, '').strip() or None for field, target in FIELDS.items()}
            row.update(record_id=number, complaint_id=identifier)
            for column, label in [('record_date', 'received'), ('date_sent', 'sent')]:
                value = row[column]; row[column] = _date(value)
                if value and row[column] is None: qa[f'invalid_{label}_dates'] += 1
                if value is None: qa[f'missing_{label}_dates'] += 1
            if coverage and row['record_date'] is not None and not (
                date.fromisoformat(coverage['start_inclusive']) <= row['record_date'] < date.fromisoformat(coverage['end_exclusive'])):
                qa['outside_requested_coverage'] += 1; continue
            value = row['timely']
            if value is None: qa['missing_timely'] += 1
            elif value.lower() not in ('yes', 'no'): qa['unrecognized_timely_values'] += 1
            row['timely'] = value.capitalize() if value and value.lower() in ('yes', 'no') else None
            rows.append(row)
    if not rows: raise ValueError('No valid complaint IDs in extract; nothing was imported.')
    if not any(row['record_date'] is not None for row in rows):
        raise UnusableComplaintDates(f'No usable received dates were parsed from {len(rows):,} retained complaints '
            f'({qa["missing_received_dates"]:,} missing, {qa["invalid_received_dates"]:,} invalid). '
            'Check Date received in the original CSV. Supported formats include 2026-09-02T19:36:07.000Z, '
            'ISO timestamps with offsets, YYYY-MM-DD and MM/DD/YYYY. No snapshot was created; reimport the original CSV after correcting its dates.')
    qa.update(imported_rows=len(rows), excluded_rows=qa['missing_or_invalid_ids'] + qa['duplicate_ids'] + qa['outside_requested_coverage'],
              future_received_dates=sum(row['record_date'] is not None and row['record_date'] > date.today() for row in rows),
              missing={column: sum(row[column] is None for row in rows) for column in rows[0]},
              duplicate_policy='First occurrence of each valid complaint ID retained; all raw rows preserved.',
              date_policy=DATE_POLICY)
    return rows, raw, header, qa


def ingest_complaints(source, database, *, download=None, synthetic=False):
    source, database = Path(source), Path(database)
    if database.exists(): raise ValueError('Use a new database; existing complaint snapshots are immutable.')
    sha = digest(source)
    verified = bool(download and download.get('url', '').startswith(API + '?') and
                    download.get('sha256') == sha and download.get('transport') == 'official_https_connector')
    rows, raw, header, qa = _normalized(source, download['coverage'] if verified else None)
    # Only the connector passes a receipt, never an uploaded JSON manifest from the UI.
    dates = [row['record_date'] for row in rows if row['record_date']]
    info = {'dataset_id': str(uuid.uuid4()), 'title': 'CFPB Consumer Complaint Database', 'module': 'complaints',
            'origin': 'synthetic' if synthetic else 'official_cfpb' if verified else 'user_upload',
            'synthetic': synthetic, 'source_url': SOURCE, 'api_documentation': DOCS,
            'source_name': source.name, 'source_path': str(source.resolve()), 'source_sha256': sha,
            'downloaded_at': download.get('downloaded_at') if verified else None,
            'imported_at': datetime.now(timezone.utc).isoformat(), 'download_receipt': download if verified else None,
            'coverage': download.get('coverage') if verified else {'start_inclusive': str(min(dates)) if dates else None,
                'end_exclusive': str(max(dates) + timedelta(days=1)) if dates else None, 'basis': 'observed dates; completeness unverified'},
            'rows': len(rows), 'source_schema': header, 'quality': qa,
            'date_parser_version': DATE_PARSER_VERSION, 'date_policy': DATE_POLICY,
            'usage': 'Official publicly released consumer complaint records. See retained publisher source/usage documentation; no CFPB endorsement.',
            'usage_documents_retained': bool(verified and download.get('publisher_pages')),
            'usage_verified': False, 'usage_review': 'Review the retained publisher pages and current usage conditions; software CC0 is not a separate dataset license verification.', 'limitations': LIMITATIONS}
    database.parent.mkdir(parents=True, exist_ok=True)
    temporary = database.with_name(database.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        frame = pd.DataFrame(rows)
        with duckdb.connect(str(temporary), config=resources()) as con:
            con.register('_rows', frame)
            casts = ','.join(f'CAST({column} AS {"BIGINT" if column == "record_id" else "DATE" if column in ("record_date", "date_sent") else "VARCHAR"}) AS {column}' for column in frame.columns)
            con.execute('CREATE TABLE complaints AS SELECT ' + casts + ' FROM _rows')
            info['analytical_schema'] = [{'column': row[0], 'type': row[1]} for row in con.execute('DESCRIBE complaints').fetchall()]
            con.register('_raw', pd.DataFrame(raw)); con.execute('CREATE TABLE raw_complaints AS SELECT * FROM _raw')
            con.execute('CREATE TABLE provenance (sha256 VARCHAR, synthetic BOOLEAN)')
            con.execute('INSERT INTO provenance VALUES (?,?)', [sha, synthetic]); write_metadata(con, info)
        temporary.replace(database)
    except Exception:
        temporary.unlink(missing_ok=True); raise
    return info


def download_extract(directory, start, end, *, client=None):
    """Full CSV export for [start,end), not a paginated or ranked sample."""
    start, end = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    if not 1 <= (end - start).days <= 31: raise ValueError('Choose 1–31 days, with an exclusive end date.')
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()): raise ValueError('Choose a new, empty snapshot directory.')
    directory.mkdir(parents=True, exist_ok=True)
    owns = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(180, connect=25), follow_redirects=True)
    temporary = directory / 'download.csv.part'
    try:
        params = {'format': 'csv', 'date_received_min': start.isoformat(), 'date_received_max': end.isoformat()}
        request = client.build_request('GET', API, params=params)
        with client.stream('GET', API, params=params) as response:
            response.raise_for_status()
            total = 0
            with temporary.open('wb') as stream:
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > 200 * 1024 * 1024: raise ValueError('Export exceeds 200 MiB. Use a shorter period.')
                    stream.write(chunk)
            receipt = {'url': str(request.url), 'http_status': response.status_code, 'bytes': total,
                       'downloaded_at': datetime.now(timezone.utc).isoformat(), 'sha256': digest(temporary),
                       'transport': 'official_https_connector', 'etag': response.headers.get('etag'),
                       'coverage': {'start_inclusive': start.isoformat(), 'end_exclusive': end.isoformat(),
                                    'basis': 'Full API CSV, reconciled with API count; [start,end) enforced locally because deployed upper-bound semantics require verification'}}
        # Validate the actually returned schema BEFORE attributing anything to CFPB.
        rows, _, header, qa = _normalized(temporary)
        if any(row['record_date'] is None or not start <= row['record_date'] <= end for row in rows):
            raise ValueError('Returned dates do not match requested coverage; inspect upstream filtering before analysis.')
        receipt['boundary_date_records'] = sum(row['record_date'] == end for row in rows)
        receipt['returned_schema'] = header
        count_response = client.get(API, params={'date_received_min': start.isoformat(), 'date_received_max': end.isoformat(),
            'size': 1, 'no_aggs': 'true', 'no_highlight': 'true'})
        count_response.raise_for_status()
        payload = count_response.json()
        total = payload.get('hits', {}).get('total')
        if not isinstance(total, dict) or total.get('relation') != 'eq' or total.get('value') != qa['source_rows']:
            raise ValueError('CSV record count does not match the exact JSON API count. The source may have changed or returned an incomplete export; no complete snapshot is claimed.')
        receipt['expected_record_count'] = total['value']; receipt['source_meta'] = payload.get('_meta', payload.get('meta', {}))
        if receipt['source_meta'].get('has_data_issue'):
            raise ValueError('The publisher reports an indexing issue; inspect source readiness before claiming a complete snapshot.')
        receipt['publisher_pages'] = []
        receipt['publisher_page_errors'] = []
        for name, url in [('database', SOURCE), ('about', SOURCE + 'about/')]:
            try:
                page = client.get(url); page.raise_for_status()
                path = directory / (name + '.html'); path.write_bytes(page.content)
                receipt['publisher_pages'].append({'url': url, 'sha256': digest(path), 'file': path.name})
            except httpx.HTTPError as exc:
                receipt['publisher_page_errors'].append({'url': url, 'error': type(exc).__name__, 'detail': str(exc)})
        source = directory / 'complaints.csv'; temporary.replace(source)
        (directory / 'download-receipt.json').write_text(json.dumps(receipt, indent=2))
        result = ingest_complaints(source, directory / 'analytics.duckdb', download=receipt)
        (directory / 'manifest.json').write_text(json.dumps(result, default=str, indent=2))
        return result
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        (directory / 'download-failure.json').write_text(json.dumps({'source': API, 'start': str(start), 'end_exclusive': str(end),
            'attempted_at': datetime.now(timezone.utc).isoformat(), 'error': type(exc).__name__, 'detail': str(exc)}, indent=2))
        raise
    finally:
        if owns: client.close()


class Complaints(ScopedAnalytics):
    table = 'complaints'
    dimensions = {'companies': 'company', 'products': 'product', 'issues': 'issue', 'states': 'state',
                  'responses': 'response', 'timely': 'timely'}
    limitations = LIMITATIONS
    catalog = {
        'summary': ('Summarize the selected complaints', '''SELECT count(*) AS complaints, count(DISTINCT company) AS companies,
            count(timely) AS known_timely, count(*) FILTER(WHERE timely='Yes') AS timely_yes,
            count(*) FILTER(WHERE timely='No') AS timely_no,
            count(*) FILTER(WHERE record_date IS NULL) AS missing_dates,
            count(*) FILTER(WHERE timely='Yes')::DOUBLE / nullif(count(timely),0) AS timely_share FROM selected'''),
        'companies': ('Which companies have the highest reported complaint volumes?', '''SELECT company, count(*) AS complaints,
            count(timely) AS known_timely, count(*) FILTER(WHERE timely='Yes') AS timely_yes,
            count(*) FILTER(WHERE timely='No') AS timely_no,
            count(*) FILTER(WHERE timely='Yes')::DOUBLE/nullif(count(timely),0) AS timely_share
            FROM selected GROUP BY company ORDER BY complaints DESC, company NULLS LAST LIMIT 1000'''),
        'products': ('Which products account for the largest share?', '''SELECT product, count(*) AS complaints,
            count(*)::DOUBLE / nullif(sum(count(*)) OVER(),0) AS share FROM selected GROUP BY product ORDER BY complaints DESC, product NULLS LAST LIMIT 1000'''),
        'issues': ('Which issues are most concentrated?', '''SELECT issue, count(*) AS complaints,
            count(*)::DOUBLE / nullif(sum(count(*)) OVER(),0) AS share FROM selected GROUP BY issue ORDER BY complaints DESC, issue NULLS LAST LIMIT 1000'''),
        'trend': ('How have reported complaints changed over time?', '''SELECT record_date, count(*) AS complaints,
            company FROM selected WHERE record_date IS NOT NULL GROUP BY record_date, company
            ORDER BY record_date, company NULLS LAST LIMIT 1000'''),
        'daily': ('What is the daily reported complaint volume?', '''SELECT record_date, count(*) AS complaints FROM selected
            WHERE record_date IS NOT NULL GROUP BY record_date ORDER BY record_date LIMIT 1000'''),
        'company_product': ('Compare complaint categories of selected companies', '''SELECT company, product, count(*) AS complaints,
            count(timely) AS known_timely, count(*) FILTER(WHERE timely='Yes')::DOUBLE/nullif(count(timely),0) AS timely_share
            FROM selected GROUP BY company, product ORDER BY complaints DESC, company NULLS LAST, product NULLS LAST LIMIT 1000'''),
        'response': ('How are reported company responses distributed?', '''SELECT response, timely, count(*) AS complaints FROM selected
            GROUP BY response,timely ORDER BY complaints DESC, response NULLS LAST, timely NULLS LAST LIMIT 1000'''),
        'donut': ('Product mix across all selected complaints', '''WITH groups AS (SELECT product, count(*) AS complaints FROM selected GROUP BY product),
            ranked AS (SELECT *,row_number() OVER(ORDER BY complaints DESC,product NULLS LAST) AS position FROM groups)
            SELECT CASE WHEN position<=5 AND complaints::DOUBLE/nullif((SELECT sum(complaints) FROM groups),0)>=0.03
                THEN coalesce(product,'Not recorded') ELSE 'Other products (combined)' END AS category,
            sum(complaints) AS complaints FROM ranked GROUP BY 1 ORDER BY complaints DESC,category'''),
        'company_distribution': ('Distribution of reported complaint counts across represented companies', '''WITH groups AS
            (SELECT company,count(*) AS complaints FROM selected WHERE company IS NOT NULL GROUP BY company)
            SELECT CASE WHEN complaints=1 THEN '1' WHEN complaints<10 THEN '2–9' WHEN complaints<100 THEN '10–99'
                WHEN complaints<1000 THEN '100–999' ELSE '1,000+' END AS volume_band,
                count(*) AS companies, min(complaints) AS band_order FROM groups GROUP BY 1 ORDER BY band_order'''),
        'heatmap': ('Concentrations across the most represented companies and products', '''WITH companies AS
            (SELECT company FROM selected GROUP BY company ORDER BY count(*) DESC,company NULLS LAST LIMIT 8),
            products AS (SELECT product FROM selected GROUP BY product ORDER BY count(*) DESC,product NULLS LAST LIMIT 8)
            SELECT s.company,s.product,count(*) AS complaints FROM selected s
            JOIN companies c ON s.company IS NOT DISTINCT FROM c.company JOIN products p ON s.product IS NOT DISTINCT FROM p.product
            GROUP BY s.company,s.product ORDER BY complaints DESC,s.company NULLS LAST,s.product NULLS LAST'''),
        'stacked_mix': ('Product mix for up to eight represented companies', '''WITH companies AS
            (SELECT company FROM selected GROUP BY company ORDER BY count(*) DESC,company NULLS LAST LIMIT 8),
            base AS (SELECT s.* FROM selected s JOIN companies c ON s.company IS NOT DISTINCT FROM c.company),
            products AS (SELECT product FROM base GROUP BY product ORDER BY count(*) DESC,product NULLS LAST LIMIT 5)
            SELECT b.company, CASE WHEN EXISTS(SELECT 1 FROM products p WHERE p.product IS NOT DISTINCT FROM b.product)
                THEN coalesce(b.product,'Not recorded') ELSE 'Other categories (combined)' END AS product, count(*) AS complaints
            FROM base b GROUP BY 1,2 ORDER BY complaints DESC,company NULLS LAST,product'''),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qa = self.metadata['quality']
        qa = qa.get('complaint_validation', qa)
        if self.metadata['rows'] and qa.get('missing', {}).get('record_date') == self.metadata['rows']:
            raise UnusableComplaintDates('This imported snapshot has no usable received dates. '
                'Its stored dates cannot be repaired by restarting the dashboard. '
                'Reimport your original CSV into a new snapshot using the updated application, then select that snapshot. '
                'Keep the original CSV; do not reuse the invalid database or its exports.')

    def overview(self):
        result = self.investigate('summary')
        return dict(zip(result['columns'], result['rows'][0])), result

    def issue_changes(self):
        coverage = self.metadata['coverage']
        start = self.filters.start_date or coverage['start_inclusive']
        end = self.filters.end_date
        if not start or not coverage['end_exclusive']:
            return self.run('SELECT issue, 0 AS earlier, 0 AS later, NULL::DOUBLE AS change_share FROM selected WHERE FALSE') | {'comparison_periods': {
                'earlier_start': None, 'later_start': None, 'end_exclusive': None, 'days_per_period': 0, 'excluded_tail_days': 0}}
        start = date.fromisoformat(str(start))
        end = date.fromisoformat(str(end)) + timedelta(days=1) if end else date.fromisoformat(coverage['end_exclusive'])
        # Clamp to source coverage; never treat an uncovered date as zero complaints.
        start = max(start, date.fromisoformat(coverage['start_inclusive']))
        end = min(end, date.fromisoformat(coverage['end_exclusive']))
        days = max(0, (end - start).days // 2)
        midpoint, stop = start + timedelta(days=days), start + timedelta(days=2 * days)
        result = self.run('''SELECT issue, count(*) FILTER(WHERE record_date < CAST(? AS DATE)) AS earlier,
            count(*) FILTER(WHERE record_date >= CAST(? AS DATE)) AS later,
            (later-earlier)::DOUBLE/nullif(earlier,0) AS change_share FROM selected
            WHERE record_date >= CAST(? AS DATE) AND record_date < CAST(? AS DATE)
            GROUP BY issue ORDER BY later-earlier DESC, issue NULLS LAST LIMIT 1000''', [midpoint, midpoint, start, stop])
        result['comparison_periods'] = {'earlier_start': start, 'later_start': midpoint, 'end_exclusive': stop,
                                        'days_per_period': days, 'excluded_tail_days': max(0, (end - stop).days)}
        return result

    def guided_answer(self, question):
        text = question.lower()
        if any(word in text for word in ('increas', 'emerging', 'growing')):
            result = self.issue_changes(); periods = result['comparison_periods']
            positive = [r for r in result['rows'] if r[2] > r[1]]
            if positive:
                issue, earlier, later, change = positive[0]
                answer = f'The largest observed increase is {issue or "an unrecorded issue"}: {earlier:,} → {later:,} complaints across equal {periods["days_per_period"]}-day periods. '
                answer += 'The relative change is undefined because the earlier count is zero. ' if change is None else f'That is {change:.1%} relative growth. '
                answer += 'Check reporting lag, product mix and complaint handling before treating this as a persistent operational problem.'
            else: answer = 'No issue has a positive count change in the two equal observation periods. A short or empty window cannot establish a trend.'
            return {'answer': answer, 'evidence': [result], 'mode': 'Guided SQL analyst; no LLM used', 'limitations': LIMITATIONS, 'visual': 'changes'}
        if any(word in text for word in ('worse', 'worst', 'cause', 'roi', 'savings')):
            return {'answer': 'These data cannot establish company quality, causes, ROI or savings. Ask about reported volumes, product mix, response categories or observed changes.', 'evidence': [], 'mode': 'Guided SQL analyst; no LLM used', 'limitations': LIMITATIONS}
        if any(word in text for word in ('compare', 'comparison')): key = 'company_product'
        elif any(word in text for word in ('manager', 'investigate next', 'summary', 'summarize')): key = 'summary'
        elif any(word in text for word in ('companies', 'company', 'highest')): key = 'companies'
        elif any(word in text for word in ('product', 'share')): key = 'products'
        elif any(word in text for word in ('time', 'trend', 'changed')): key = 'daily'
        elif any(word in text for word in ('response', 'timely')): key = 'response'
        elif 'issue' in text: key = 'issues'
        else: return {'answer': 'Choose a suggested investigation: company volumes, product shares, trends, company/product comparisons, increasing issues or management priorities.', 'evidence': [], 'mode': 'Guided SQL analyst; no LLM used', 'limitations': LIMITATIONS}
        result = self.investigate(key); rows = result['rows']
        if not rows: answer = 'No records match this selection. Broaden the filters before drawing conclusions.'
        elif key == 'summary':
            values = dict(zip(result['columns'], rows[0]))
            pct = 'Unavailable' if values['timely_share'] is None else f'{values["timely_share"]:.1%}'
            answer = f'{values["complaints"]:,} reported complaints represent {values["companies"]:,} named companies. Timely-response share: {pct}, using {values["known_timely"]:,} known Yes/No records. Prioritize concentrated issues, then compare equal periods and review response exceptions. Confirm exposure and reporting completeness before allocating resources.'
        elif key in ('companies', 'products', 'issues'):
            answer = f'{rows[0][0] or "Not recorded"} has the largest reported volume in this selection: {rows[0][1]:,} complaints. '
            if key in ('products', 'issues'): answer += f'It accounts for {rows[0][2]:.1%} of selected complaints. '
            answer += 'Review the supporting categories and source records; volume alone does not establish service quality or causation.'
        elif key == 'daily': answer = f'The query returned {len(rows):,} observed dates with {sum(row[1] for row in rows):,} complaints. Compare like-for-like periods and account for publication lag before interpreting movement.'
        elif key == 'company_product': answer = f'The largest company–product combination is {rows[0][0] or "Not recorded"} / {rows[0][1] or "Not recorded"}, with {rows[0][2]:,} complaints. Compare product mix and the known-response denominator, then inspect the issue concentration. Differences are descriptive.'
        else: answer = f'The largest reported response/timeliness combination is {rows[0][0] or "Not recorded"} / {rows[0][1] or "Not recorded"}: {rows[0][2]:,} complaints. Review unknown and late responses separately; the dataset does not provide a verified response-duration measure.'
        return {'answer': answer, 'evidence': [result], 'mode': 'Guided SQL analyst; no LLM used', 'limitations': LIMITATIONS, 'visual': key}


def reconcile_complaints(source, database):
    """A second implementation using csv + Python counters, independent of ingestion normalization."""
    from .datasets import metadata
    info = metadata(database); counters = Counter({k: 0 for k in ('complaints', 'known_timely', 'timely_yes', 'timely_no', 'missing_dates')})
    companies = Counter(); products = Counter(); days = Counter(); issues = Counter(); company_products = Counter(); seen = set()
    with Path(source).open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            identifier = row['Complaint ID'].strip()
            if not identifier.isdigit() or int(identifier) <= 0 or int(identifier) in seen: continue
            seen.add(int(identifier))
            value = row['Date received'].strip()
            # Independent reference implementation: pandas ISO parser, not _date
            # or datetime.fromisoformat. Explicit legacy fallback, no locale guess.
            reference = pd.to_datetime(value, format='ISO8601', errors='coerce', utc=True)
            if pd.isna(reference): reference = pd.to_datetime(value, format='%m/%d/%Y', errors='coerce', utc=True)
            parsed = None if pd.isna(reference) else reference.date()
            if info.get('download_receipt') and parsed:
                first = date.fromisoformat(info['coverage']['start_inclusive']); stop = date.fromisoformat(info['coverage']['end_exclusive'])
                if parsed < first or parsed >= stop: continue
            counters['complaints'] += 1
            if row['Company'].strip(): companies[row['Company'].strip()] += 1
            products[row['Product'].strip() or None] += 1; issues[row['Issue'].strip() or None] += 1
            company_products[(row['Company'].strip() or None, row['Product'].strip() or None)] += 1
            timely = row['Timely response?'].strip().lower()
            counters['known_timely'] += timely in ('yes', 'no'); counters['timely_yes'] += timely == 'yes'; counters['timely_no'] += timely == 'no'
            if parsed: days[parsed] += 1
            else: counters['missing_dates'] += 1
    counters['companies'] = len(companies)
    service = Complaints(database); actual, evidence = service.overview()
    checks = {key: actual[key] == counters[key] for key in counters}
    checks['source_sha256'] = info['source_sha256'] == digest(source)
    checks['product_distribution'] = dict((r[0], r[1]) for r in service.investigate('products')['rows']) == dict(products)
    checks['issue_distribution'] = dict((r[0], r[1]) for r in service.investigate('issues')['rows']) == dict(issues)
    checks['daily_distribution'] = dict(service.investigate('daily')['rows']) == dict(days)
    # Reconciliation covers every company, including a long tail beyond the
    # dashboard's bounded ranking. Only aggregates are read into Python.
    from .agent import SQL_CONFIG
    with duckdb.connect(str(Path(database).resolve()), read_only=True, config=SQL_CONFIG) as con:
        actual_companies = dict(con.execute('SELECT company,count(*) FROM complaints WHERE company IS NOT NULL GROUP BY company').fetchall())
        actual_pairs = {(company, product): count for company, product, count in con.execute('SELECT company,product,count(*) FROM complaints GROUP BY company,product').fetchall()}
    checks['company_distribution'] = actual_companies == dict(companies)
    checks['company_product_distribution'] = actual_pairs == dict(company_products)
    checks['timely_share'] = actual['timely_share'] == (counters['timely_yes'] / counters['known_timely'] if counters['known_timely'] else None)
    return {'passed': all(checks.values()), 'checks': checks, 'independent_counts': dict(counters),
            'independent_daily_counts': {str(day): count for day, count in sorted(days.items())},
            'date_reference': 'Independent pandas ISO8601/US-date parser, normalized to UTC', 'source_sha256': digest(source),
            'origin': info['origin'], 'evidence': evidence, 'note': 'Reconciles source bytes and calculations; does not authenticate an uploaded source.'}


def management_summary(service):
    values, evidence = service.overview(); products = service.investigate('products'); companies = service.investigate('companies'); changes = service.issue_changes()
    pct = 'unavailable' if values['timely_share'] is None else f'{values["timely_share"]:.1%}'
    selection = []
    for name, value in evidence['filters'].items():
        if value is None or value == (): continue
        selection.append(name.replace('_', ' ').title() + ': ' + (', '.join('Not recorded' if v is None else str(v) for v in value) if isinstance(value, (tuple, list)) else str(value)))
    origin = {'official_cfpb': 'Official CFPB connector snapshot', 'synthetic': 'SYNTHETIC software reference', 'user_upload': 'Unverified user upload'}[service.metadata['origin']]
    lines = ['# Customer Experience & Risk Intelligence — management brief', '',
             f'Source: {service.metadata["title"]} · {origin} · snapshot {service.metadata["dataset_id"][:8]}',
             'Selection: ' + ('; '.join(selection) if selection else 'All imported records'), '',
             f'{values["complaints"]:,} reported complaints; {values["companies"]:,} named companies. Timely-response share {pct} '
             f'({values["timely_yes"]:,}/{values["known_timely"]:,} known Yes/No records; {values["complaints"]-values["known_timely"]:,} unknown).', '',
             '## Observations and actions', '']
    if products['rows']:
        name, count, share = products['rows'][0]
        lines += [f'- Largest reported product: {name or "Not recorded"}, {count:,} complaints ({share:.1%} of the selection). Investigate the dominant issues and validate reporting completeness.']
    if companies['rows']:
        row = companies['rows'][0]
        lines += [f'- Highest reported company volume: {row[0] or "Not recorded"}, {row[1]:,} complaints. Obtain a defensible customer-exposure denominator before any quality comparison.']
    increases = [r for r in changes['rows'] if r[2] > r[1]]
    if increases:
        issue, old, new, _ = increases[0]
        lines += [f'- Largest observed issue increase: {issue or "Not recorded"}, {old:,} → {new:,} across equal {changes["comparison_periods"]["days_per_period"]}-day periods. Review publication lag and product mix; no causal explanation is established.']
    lines += ['', '## Proposed monitoring', '', 'Track issue counts, known-response coverage and late-response share across equal periods. Validate data maturity and exposure before choosing targets. No savings or operational improvement has been demonstrated.', '', '## Limitations', '', LIMITATIONS, '', 'Evidence: executed read-only SQL, fixed source hash, active filters, independent CSV-counter reconciliation. Query evidence accompanies the exported analytical package.']
    return '\n'.join(lines)


def export_complaints(database, directory):
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    service = Complaints(database)
    from .agent import SQL_CONFIG
    import pyarrow.parquet as parquet
    with duckdb.connect(str(Path(database).resolve()), read_only=True, config=SQL_CONFIG) as con:
        # Python writes a local export, while DuckDB keeps identical read-only
        # restrictions for concurrent UI readers. No SQL file-access capability.
        batches = con.sql('SELECT * FROM complaints').to_arrow_reader(batch_size=50000)
        with parquet.ParquetWriter(directory / 'Complaints.parquet', batches.schema) as writer:
            for batch in batches: writer.write_batch(batch)
    (directory / 'manifest.json').write_text(json.dumps(service.metadata | {'export_sha256': digest(directory / 'Complaints.parquet')}, default=str, indent=2))
    evidence = {key: service.investigate(key) for key in service.catalog}
    evidence['issue_changes'] = service.issue_changes()
    (directory / 'query-evidence.json').write_text(json.dumps(evidence, default=str, indent=2))
    (directory / 'management-summary.md').write_text(management_summary(service))
    source = Path(service.metadata.get('source_path', 'missing-source'))
    validation = reconcile_complaints(source, database) if source.is_file() else {'passed': False, 'status': 'Original source unavailable; independent reconciliation not executed'}
    (directory / 'source-validation.json').write_text(json.dumps(validation, default=str, indent=2))
    case = ['# Complaint intelligence — analytical case study', '',
        '## Business context', 'Investigate publicly reported consumer complaint patterns and response categories. This is a portfolio decision-support scenario, not a verified client engagement.', '',
        '## Stakeholders', 'Customer-experience leaders, complaint operations, product owners and data-quality stewards. No stakeholder sign-off is claimed.', '',
        '## Business questions', 'Which reported issues and products warrant review? How does response coverage differ? Which equal-period changes merit investigation?', '',
        '## KPI definitions', 'Complaints = distinct valid source IDs after the declared duplicate policy. Represented companies = distinct nonempty company names. Timely share = Yes / (Yes + No); unknowns excluded and reported. Product share = product complaints / all selected complaints. No customer-exposure complaint rate is calculated.', '',
        '## Findings and recommendations', management_summary(service), '',
        '## Validation', f'Independent source/SQL reconciliation passed: {validation["passed"]}. See source-validation.json and query-evidence.json. Source origin: {service.metadata["origin"]}. A matching hash authenticates bytes, not an uploaded publisher claim.', '',
        '## Limitations', LIMITATIONS, 'Windows Power BI and live-provider narrative acceptance remain separate, unexecuted checks unless recorded elsewhere.']
    (directory / 'case-study.md').write_text('\n'.join(case))
    return {'directory': str(directory), 'rows': service.metadata['rows'], 'origin': service.metadata['origin']}
