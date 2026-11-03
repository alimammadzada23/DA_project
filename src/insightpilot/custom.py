"""Conservative analytical capabilities derived from approved upload mappings."""
from .datasets import ScopedAnalytics
from .dashboard import Dashboard
from .agent import Analyst, DATA_TABLES
from .analytics import QUESTIONS
from .datasets import metadata


class CustomAnalytics(ScopedAnalytics):
    catalog = {
        'summary': ('Summarize selected records', '''SELECT count(*) AS records, count(record_date) AS dated_records,
            count(measure) AS numeric_records, sum(measure) AS total_measure, avg(measure) AS average_measure,
            median(measure) AS median_measure, count(DISTINCT customer) AS customers FROM selected'''),
        'trend': ('How does recorded volume change over time?', '''SELECT date_trunc('month',record_date)::DATE AS month,
            count(*) AS records, count(measure) AS numeric_records, sum(measure) AS total_measure FROM selected
            WHERE record_date IS NOT NULL GROUP BY 1 ORDER BY 1 LIMIT 1000'''),
        'groups': ('Which mapped categories have the largest recorded volume?', '''SELECT coalesce(category,product,company) AS category,
            count(*) AS records, count(measure) AS numeric_records, sum(measure) AS total_measure
            FROM selected GROUP BY 1 ORDER BY records DESC,category NULLS LAST LIMIT 1000'''),
        'products': ('Compare mapped products', '''SELECT product,count(*) AS records,count(measure) AS numeric_records,
            sum(measure) AS total_measure FROM selected GROUP BY 1 ORDER BY records DESC,product NULLS LAST LIMIT 1000'''),
        'distribution': ('How is the mapped numeric measure distributed?', '''SELECT
            CASE WHEN measure<0 THEN 'Negative' WHEN measure=0 THEN 'Zero' WHEN measure<=100 THEN '0–100'
            WHEN measure<=1000 THEN '100–1,000' ELSE 'Above 1,000' END AS band, count(*) AS records,
            min(measure) AS band_order FROM selected WHERE measure IS NOT NULL GROUP BY 1 ORDER BY band_order'''),
        'customers': ('How are customers represented in the selection?', '''SELECT customer, count(*) AS records,
            count(measure) AS numeric_records, sum(measure) AS total_measure FROM selected WHERE customer IS NOT NULL
            GROUP BY 1 ORDER BY records DESC,customer LIMIT 1000'''),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.catalog = dict(type(self).catalog)
        capabilities = self.metadata['capabilities']
        for capability, key in [('trends', 'trend'), ('groups', 'groups'), ('numeric_distribution', 'distribution'), ('customer_segments', 'customers')]:
            if capability not in capabilities: self.catalog.pop(key, None)
        if 'product' not in self.metadata['mapping']: self.catalog.pop('products', None)
        grouping = next((field for field in ('category', 'product', 'company') if field in self.metadata['mapping']), 'category')
        if 'groups' in self.catalog:
            title, sql = self.catalog['groups']
            self.catalog['groups'] = title, sql.replace('coalesce(category,product,company)', grouping)

    def guided_answer(self, question):
        text = question.lower()
        if any(w in text for w in ('cause', 'roi', 'saving', 'profit')):
            return {'answer': 'The approved mapping supports descriptive analysis. Causes, profit, ROI and savings need additional business definitions and evidence.', 'evidence': [], 'mode': 'Guided SQL analyst; no LLM used', 'limitations': self.limitations}
        if any(w in text for w in ('revenue', 'sales')) and not self.metadata['revenue_confirmed']:
            return {'answer': 'Revenue is disabled. Confirm the amount semantics in field mapping before calculating it.', 'evidence': [], 'mode': 'Guided SQL analyst; no LLM used', 'limitations': self.limitations}
        if any(w in text for w in ('time', 'trend', 'month')): key = 'trend'
        elif 'customer' in text: key = 'customers'
        elif 'product' in text: key = 'products'
        elif any(w in text for w in ('category', 'categories', 'group')): key = 'groups'
        elif any(w in text for w in ('distribution', 'numeric')): key = 'distribution'
        elif any(w in text for w in ('summary', 'summarize', 'record', 'revenue', 'sales')): key = 'summary'
        else: key = None
        if key not in self.catalog:
            return {'answer': 'This question is not supported by the approved fields. Choose one of the available investigations.', 'evidence': [], 'mode': 'Guided SQL analyst; no LLM used', 'limitations': self.limitations}
        result = self.investigate(key)
        if key == 'summary':
            count, dated, numeric, total, average, median, customers = result['rows'][0]
            answer = f'{count:,} records match the filters; {dated:,} have usable mapped dates. '
            if 'numeric_distribution' in self.metadata['capabilities']:
                title = 'confirmed revenue' if self.metadata['revenue_confirmed'] else 'mapped numeric measure'
                answer += f'The {title} totals {total:,.2f} across {numeric:,} valid numeric records. ' if total is not None else 'No valid numeric measure is recorded. '
            answer += 'Review missing-value coverage and group concentrations before forming a business hypothesis.'
        elif not result['rows']: answer = 'No usable values match these filters. Broaden the selection or review the mapping.'
        else: answer = f'The investigation returned {len(result["rows"]):,} aggregated groups. The chart shows actual selected records. Review concentration, missing values and changes over comparable periods; these results establish associations only.'
        return {'answer': answer, 'evidence': [result], 'visual': key, 'mode': 'Guided SQL analyst; no LLM used', 'limitations': self.limitations}


class CustomProcessAnalytics(Dashboard):
    """Reuse the event engine without procurement matching assumptions."""
    table = 'cases'
    data_tables = DATA_TABLES
    limitations = 'Observed spans are not verified completion times; repeats and waiting intervals require operational context. Associations do not establish causes, compliance or savings.'
    catalog = {key: QUESTIONS[key] for key in ('event_volume', 'activity_volume', 'median_span', 'p90_span',
        'repeat_cases', 'variant_count', 'top_variants', 'transitions', 'activity_frequency', 'long_spans')}
    catalog['item_volume'] = ('How many cases are recorded?', QUESTIONS['item_volume'][1])
    catalog['repeat_activity_analysis'] = ('Which activities repeat most frequently?', '')

    def __init__(self, database, filters=None):
        super().__init__(database, filters)
        self.metadata = metadata(database)
        self.analyst = Analyst(database, domain='uploaded process', limitations=self.limitations)

    def run(self, *args, **kwargs):
        result = super().run(*args, **kwargs)
        return result | {'scope': 'Selected complete cases and their recorded events', 'dataset_id': self.metadata['dataset_id']}

    def guided_answer(self, question):
        text = question.lower()
        if any(word in text for word in ('cause', 'saving', 'roi', 'compliance', 'matching')):
            return {'answer': 'The uploaded event log supports descriptive process analysis. Causes, matching rules, compliance and savings require additional verified definitions.', 'evidence': [], 'limitations': self.limitations, 'mode': 'Guided SQL analyst; no LLM used'}
        if 'repeat' in text: key = 'repeat_activity_analysis'
        elif any(word in text for word in ('waiting', 'transition', 'bottleneck')): key = 'transitions'
        elif any(word in text for word in ('variant', 'path')): key = 'top_variants'
        elif any(word in text for word in ('duration', 'span')): key = 'median_span'
        elif 'activit' in text: key = 'activity_frequency'
        elif 'event' in text: key = 'event_volume'
        elif any(word in text for word in ('case', 'summary', 'summarize')): key = 'item_volume'
        else: return {'answer': 'Choose case or event counts, observed spans, process variants, repeated activities or waiting intervals.', 'evidence': [], 'limitations': self.limitations, 'mode': 'Guided SQL analyst; no LLM used'}
        result = self.investigate(key); rows = result['rows']
        if not rows: answer = 'No observations match this process selection.'
        elif key == 'item_volume': answer = f'{rows[0][0]:,} complete recorded cases match the filters. Case membership retains the full event history.'
        elif key == 'event_volume': answer = f'{rows[0][0]:,} recorded events belong to the selected cases.'
        elif key == 'median_span': answer = 'No usable observed spans are available.' if rows[0][0] is None else f'The median observed case span is {rows[0][0]:,.2f} days. This does not establish completed-case cycle time.'
        elif key == 'top_variants': answer = f'The most common recorded variant contains {rows[0][1]:,} selected cases. Repeated activities remain in the ordered sequence.'
        elif key == 'repeat_activity_analysis': answer = f'{rows[0][0] or "Not recorded"} repeats in {rows[0][1]:,} selected cases, with {rows[0][2]:,} excess recorded occurrences. Review the underlying cases before classifying repetition as avoidable work.'
        elif key == 'transitions': answer = f'The longest median recorded waiting interval is {rows[0][0]} → {rows[0][1]}: {rows[0][3]:,.2f} hours across {rows[0][2]:,} transitions. Investigate business calendars and event semantics before attributing delay.'
        else: answer = f'{rows[0][0] or "Not recorded"} is the most frequently recorded activity: {rows[0][1]:,} events in the selection.'
        return {'answer': answer, 'evidence': [result], 'visual': key, 'limitations': self.limitations, 'mode': 'Guided SQL analyst; no LLM used'}
