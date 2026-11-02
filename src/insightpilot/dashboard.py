"""One parameterized case cohort for every dashboard metric and investigation."""
from dataclasses import asdict, dataclass
from datetime import date
import re
import duckdb
import json

from .agent import Analyst
from .analytics import QUESTIONS, petri_summary


@dataclass(frozen=True)
class Filters:
    categories: tuple = ()
    vendors: tuple = ()
    spend_classes: tuple = ()
    start_date: date | None = None
    end_date: date | None = None
    repeats: bool | None = None
    min_span: float | None = None
    max_span: float | None = None

    def predicate(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError('Start date must not follow end date')
        if self.min_span is not None and self.max_span is not None and self.min_span > self.max_span:
            raise ValueError('Minimum span must not exceed maximum span')
        parts, params = [], []
        for column, values in [('matching_category',self.categories),('vendor',self.vendors),('spend_classification',self.spend_classes)]:
            if not values: continue
            non_null = [value for value in values if value is not None]
            options = []
            if non_null:
                options.append(f"{column} IN ({','.join('?' for _ in non_null)})")
                params.extend(non_null)
            if None in values: options.append(f'{column} IS NULL')
            parts.append('('+' OR '.join(options)+')')
        for value, expression in [(self.start_date,'CAST(first_event AS DATE) >= ?'),(self.end_date,'CAST(first_event AS DATE) <= ?'),
                                  (self.min_span,'observed_span_days >= ?'),(self.max_span,'observed_span_days <= ?')]:
            if value is not None: parts.append(expression); params.append(value)
        if self.repeats is not None:
            parts.append('repeated_event_count > 0' if self.repeats else 'repeated_event_count = 0')
        return ' AND '.join(parts) or 'TRUE', params


# Whole-case selection: date filters do not trim the events inside selected items.
COHORT_CTES = '''WITH selected_cases AS (SELECT * FROM case_metrics WHERE {predicate}),
selected_events AS (SELECT e.* FROM events e JOIN selected_cases c USING(case_id)),
selected_ordered_events AS (SELECT e.* FROM ordered_events e JOIN selected_cases c USING(case_id)),
selected_transitions AS (SELECT t.* FROM transitions t JOIN selected_cases c USING(case_id)),
selected_variants AS (SELECT variant,count(*) AS case_count FROM selected_cases GROUP BY variant),
selected_category_performance AS (
 SELECT matching_category,count(*) AS case_count,median(observed_span_days) AS median_span_days,
 quantile_cont(observed_span_days,0.9) AS p90_span_days,
 count(*) FILTER(WHERE repeated_event_count>0) AS cases_with_repeats
 FROM selected_cases GROUP BY matching_category),
selected_transition_performance AS (
 SELECT from_activity,to_activity,count(*) AS occurrences,median(waiting_hours) AS median_wait_hours,
 quantile_cont(waiting_hours,0.9) AS p90_wait_hours FROM selected_transitions GROUP BY from_activity,to_activity),
selected_sequence_signals AS (SELECT s.* FROM sequence_signals s JOIN selected_cases c USING(case_id))
'''

OVERVIEW_SQL = '''SELECT count(*) AS item_count,count(DISTINCT purchase_document) AS document_count,
 coalesce(sum(event_count),0) AS event_count,median(observed_span_days) AS median_span_days,
 quantile_cont(observed_span_days,0.9) AS p90_span_days,
 count(*) FILTER(WHERE repeated_event_count>0) AS repeat_cases,
 count(*) FILTER(WHERE repeated_event_count>0)*1.0/nullif(count(*),0) AS repeat_rate,
 count(*) FILTER(WHERE missing_timestamps>0) AS items_with_missing_times,
 coalesce(sum(missing_activities),0) AS missing_activities,
 count(*) FILTER(WHERE observed_span_days IS NOT NULL) AS span_coverage
 FROM selected_cases'''


class Dashboard:
    catalog = QUESTIONS

    def __init__(self,database,filters=None):
        self.analyst = Analyst(database)
        self.filters = filters or Filters()

    def run(self,sql,limit=200,parameters=()):
        predicate,params = self.filters.predicate()
        result = self.analyst.query(COHORT_CTES.format(predicate=predicate)+sql,limit=limit,parameters=params+list(parameters))
        result['filters'] = asdict(self.filters)
        result['scope'] = 'Selected purchase-order items and all their recorded events'
        return result

    def investigate(self,question_id):
        question, sql = self.catalog[question_id]
        replacements = {name:'selected_'+name for name in [
            'events','ordered_events','transitions','variants','transition_performance','category_performance','sequence_signals']}
        replacements.update({'cases':'selected_cases','case_metrics':'selected_cases'})
        # Rewrite relation positions only; metric aliases such as "AS cases" stay unchanged.
        sql = re.sub(r'\b(FROM|JOIN)\s+('+ '|'.join(replacements)+r')\b',
                     lambda match:match[1]+' '+replacements[match[2]],sql,flags=re.IGNORECASE)
        result = self.run(sql)
        result.update(question=question,mode='Filtered deterministic SQL evidence; no LLM used')
        return result

    def custom_sql(self,sql):
        """Enforce named cohort relations for the filtered SQL editor."""
        if len(sql)>20000:raise ValueError('Query limits exceeded')
        with duckdb.connect(config={'enable_external_access':'false'}) as con:
            statements=con.extract_statements(sql)
            if len(statements)!=1 or statements[0].type!=duckdb.StatementType.SELECT:
                raise ValueError('Exactly one SELECT is permitted')
            ast=json.loads(con.execute('SELECT json_serialize_sql(?)',[sql]).fetchone()[0])
        allowed={'selected_cases','selected_events','selected_ordered_events','selected_transitions',
                 'selected_variants','selected_transition_performance','selected_category_performance','selected_sequence_signals'}
        message='Filtered SQL must use selected_cases / selected_events or another selected_* relation. Choose Whole database explicitly to query raw tables.'
        def validate(value,visible=frozenset()):
            if isinstance(value,list):
                for child in value:validate(child,visible)
            elif isinstance(value,dict):
                if value.get('type')=='TABLE_FUNCTION':raise ValueError('Table functions are unavailable in filtered SQL. '+message)
                if value.get('type')=='BASE_TABLE':
                    if value.get('schema_name') or value.get('catalog_name') or value['table_name'].lower() not in allowed|visible:
                        raise ValueError(message)
                scope=set(visible)
                for entry in value.get('cte_map',{}).get('map',[]):
                    name=entry['key'].lower()
                    if name in allowed:raise ValueError('Do not shadow selected_* cohort relations')
                    validate(entry['value'],scope)
                    scope.add(name)
                for key,child in value.items():
                    if key!='cte_map':validate(child,scope)
        if ast.get('error'):raise ValueError('Cannot parse filtered SQL')
        validate(ast)
        sql=sql.strip().removesuffix(';')
        return self.run(f'SELECT * FROM ({sql}) AS user_query')

    def overview(self):
        result = self.run(OVERVIEW_SQL)
        return dict(zip(result['columns'],result['rows'][0])), result

    def options(self):
        result = {}
        for key,column in [('categories','matching_category'),('vendors','vendor'),('spend_classes','spend_classification')]:
            # Bound options avoid flooding the interface on unexpectedly high-cardinality data.
            query = self.analyst.query(f'SELECT DISTINCT {column} FROM cases ORDER BY {column} LIMIT 1001',limit=1000)
            result[key] = {'values':[row[0] for row in query['rows']], 'truncated':query['truncated']}
        result['dates'] = self.analyst.query('SELECT min(CAST(first_event AS DATE)),max(CAST(first_event AS DATE)),max(observed_span_days) FROM case_metrics')['rows'][0]
        return result

    def transitions(self,min_occurrences=1):
        if min_occurrences<1: raise ValueError('Minimum occurrences must be positive')
        return self.run('SELECT * FROM selected_transition_performance WHERE occurrences>=? ORDER BY median_wait_hours DESC,from_activity,to_activity LIMIT 50',parameters=[min_occurrences])

    def distribution(self):
        return self.run("""SELECT CASE WHEN waiting_hours<1 THEN '<1 hour' WHEN waiting_hours<24 THEN '1–24 hours'
             WHEN waiting_hours<168 THEN '1–7 days' ELSE '7+ days' END AS waiting_band,
             CASE WHEN waiting_hours<1 THEN 0 WHEN waiting_hours<24 THEN 1 WHEN waiting_hours<168 THEN 2 ELSE 3 END AS band_order,
             count(*) AS transitions FROM selected_transitions GROUP BY waiting_band,band_order ORDER BY band_order""")

    def case_events(self,case_id):
        return self.run('SELECT case_id,event_rank,source_order,activity,event_time,resource,lifecycle FROM selected_ordered_events WHERE case_id=? ORDER BY event_rank',parameters=[case_id],limit=1000)

    def discover(self):
        missing = self.run('SELECT count(*) FROM selected_events WHERE activity IS NULL')['rows'][0][0]
        if missing: raise ValueError('Selected items contain missing activities; inspect data quality before discovery')
        edges = self.run('SELECT previous_activity,activity,count(*) AS occurrences FROM selected_ordered_events WHERE event_rank>1 GROUP BY previous_activity,activity ORDER BY occurrences DESC,previous_activity,activity',limit=1000)
        if edges['truncated']: raise ValueError('Selected process exceeds 1000 edges; narrow the cohort before discovery')
        starts = self.run('SELECT activity,count(*) FROM selected_ordered_events WHERE event_rank=1 GROUP BY activity',limit=1000)
        ends = self.run('SELECT activity,count(*) FROM (SELECT *,row_number() OVER(PARTITION BY case_id ORDER BY event_rank DESC) AS r FROM selected_ordered_events) WHERE r=1 GROUP BY activity',limit=1000)
        if not starts['rows']: raise ValueError('No items match the active filters')
        process = petri_summary({(a,b):n for a,b,n in edges['rows']},dict(starts['rows']),dict(ends['rows']))
        process['filters'] = asdict(self.filters)
        process['evidence'] = [edges,starts,ends]
        return process

    def guided_answer(self,question):
        """Transparent local routing over the query catalog, not a simulated LLM."""
        text = question.lower()
        if re.search(r'\b(cause|causes|why|explain)\b',text):
            return {'answer':'The log can show differences, but does not establish their cause. Compare matching categories and inspect case timelines before forming a hypothesis.',
                    'evidence':[self.investigate('categories')],'mode':'Guided SQL analyst (no LLM)', 'filters':asdict(self.filters)}
        if re.search(r'\b(human|batch|automat|saving|euro|compliant|violation)',text):
            return {'answer':'This investigation requires verified resource mappings, financial assumptions or category-specific controls. The available data cannot certify that conclusion. Review the recorded signals instead.',
                    'evidence':[self.investigate('sequence_signals')],'mode':'Guided SQL analyst (no LLM)','filters':asdict(self.filters)}
        routes = [
            ('variants',r'\b(variants?|paths?)\b','top_variants'),
            ('items containing repeated activities',r'\b(repeat|repeated|repetition|rework)\b','repeat_cases'),
            ('transition intervals',r'\b(wait|waiting|bottleneck|transitions?|delay)\b','transitions'),
            ('review signals',r'\b(exception|exceptions|compliance|invoice)\b','sequence_signals'),
            ('category performance',r'\b(flows?|categor|longest|slowest|cycle|throughput|span)', 'categories'),
            ('monthly case starts',r'\b(trend|monthly|month)\b','monthly_cases'),
            ('purchase-order items',r'\b(items?|cases?)\b','item_volume'),
            ('purchase documents',r'\b(documents?|orders?)\b','document_volume'),
            ('recorded events',r'\b(events?)\b','event_volume'),
            ('recorded resources',r'\b(resources?|users?)\b','user_volume'),
            ('purchase-order items',r'\b(items?|cases?|volume|count|many)\b','item_volume'),
        ]
        for subject,pattern,key in routes:
            if not re.search(pattern,text): continue
            evidence = self.investigate(key)
            if key in ('item_volume','event_volume','document_volume','repeat_cases','user_volume'):
                answer = f"The selected cohort has {evidence['rows'][0][0]} {subject}."
            elif not evidence['rows']:
                answer = f'No {subject} were found for the selected cohort.'
            elif key=='categories':
                first = evidence['rows'][0]
                answer = (f"Among selected categories, {first[0] or '(missing category)'} has the highest non-null median observed span: {first[2]} calendar days across {first[1]} items. "
                          'Observed spans are not verified completion times.') if first[2] is not None else 'Selected items have no usable observed spans.'
            else:
                answer = f'Executed a {subject} investigation for the selected cohort. Review the returned rows and SQL below.'
            answer += ' Repetition and sequence signals require business review; these results do not prove avoidable work or noncompliance.'
            return {'answer':answer,'evidence':[evidence],'mode':'Guided SQL analyst (no LLM)','filters':asdict(self.filters)}
        return {'answer':'Choose a supported investigation: item/document/event volumes, category spans, variants, waiting intervals, repeats, trends or review signals. No query was run for this question.',
                'evidence':[],'mode':'Guided SQL analyst (no LLM)','filters':asdict(self.filters)}
