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
    companies: tuple = ()
    activities: tuple = ()

    def predicate(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError('Start date must not follow end date')
        if self.min_span is not None and self.max_span is not None and self.min_span > self.max_span:
            raise ValueError('Minimum span must not exceed maximum span')
        parts, params = [], []
        for column, values in [('matching_category',self.categories),('vendor',self.vendors),('spend_classification',self.spend_classes),
                               ("json_extract_string(source_attributes,'$.Company')",self.companies)]:
            if not values: continue
            non_null = [value for value in values if value is not None]
            options = []
            if non_null:
                options.append(f"{column} IN ({','.join('?' for _ in non_null)})")
                params.extend(non_null)
            if None in values: options.append(f'{column} IS NULL')
            parts.append('('+' OR '.join(options)+')')
        if self.activities:
            parts.append(f"case_id IN (SELECT case_id FROM events WHERE activity IN ({','.join('?' for _ in self.activities)}))")
            params.extend(self.activities)
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
    catalog = QUESTIONS | {
        'management_summary':('Summarize selected process performance for management.',OVERVIEW_SQL),
        'repeat_activity_analysis':('Which recorded activities repeat most frequently?',''),
        'group_comparison':('Compare selected matching flows and their recorded performance.',''),
    }

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
        if question_id in ('management_summary','repeat_activity_analysis','group_comparison'):
            result={'management_summary':lambda:self.overview()[1],
                    'repeat_activity_analysis':self.repeated_activities,'group_comparison':self.group_comparison}[question_id]()
            result.update(question=question,mode='Filtered deterministic SQL evidence; no LLM used')
            return result
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
        for key,column in [('categories','matching_category'),('vendors','vendor'),('spend_classes','spend_classification'),
                           ('companies',"json_extract_string(source_attributes,'$.Company')")]:
            # Bound options avoid flooding the interface on unexpectedly high-cardinality data.
            query = self.analyst.query(f'SELECT DISTINCT {column} FROM cases ORDER BY {column} LIMIT 1001',limit=1000)
            result[key] = {'values':[row[0] for row in query['rows']], 'truncated':query['truncated']}
        result['dates'] = self.analyst.query('SELECT min(CAST(first_event AS DATE)),max(CAST(first_event AS DATE)),max(observed_span_days) FROM case_metrics')['rows'][0]
        activities=self.analyst.query('SELECT DISTINCT activity FROM events WHERE activity IS NOT NULL ORDER BY activity LIMIT 1001',limit=1000)
        result['activities']={'values':[row[0] for row in activities['rows']], 'truncated':activities['truncated']}
        return result

    def monthly_performance(self):
        return self.run("""SELECT date_trunc('month',first_event) AS month,count(*) AS items,
          median(observed_span_days) AS median_days,quantile_cont(observed_span_days,0.9) AS p90_days,
          count(*) FILTER(WHERE repeated_event_count>0) AS repeat_cases
          FROM selected_cases WHERE first_event IS NOT NULL GROUP BY month ORDER BY month""")

    def duration_distribution(self):
        return self.run("""SELECT CASE WHEN observed_span_days<1 THEN '<1 day' WHEN observed_span_days<3 THEN '1–<3 days'
          WHEN observed_span_days<7 THEN '3–<7 days' WHEN observed_span_days<14 THEN '7–<14 days'
          WHEN observed_span_days<30 THEN '14–<30 days' ELSE '30+ days' END AS duration_band,
          CASE WHEN observed_span_days<1 THEN 0 WHEN observed_span_days<3 THEN 1 WHEN observed_span_days<7 THEN 2
          WHEN observed_span_days<14 THEN 3 WHEN observed_span_days<30 THEN 4 ELSE 5 END AS band_order,
          count(*) AS items FROM selected_cases WHERE observed_span_days IS NOT NULL
          GROUP BY duration_band,band_order ORDER BY band_order""")

    GROUPS={'Process type':'matching_category','Vendor':'vendor','Spend classification':'spend_classification',
            'Company':"json_extract_string(source_attributes,'$.Company')"}

    def group_comparison(self,dimension='Process type',min_items=1):
        if dimension not in self.GROUPS:raise ValueError('Unsupported comparison dimension')
        if min_items<1:raise ValueError('Minimum items must be positive')
        expression=self.GROUPS[dimension]
        return self.run(f"""SELECT {expression} AS group_name,count(*) AS items,
          median(observed_span_days) AS median_days,quantile_cont(observed_span_days,0.9) AS p90_days,
          count(*) FILTER(WHERE repeated_event_count>0) AS repeat_cases,
          count(*) FILTER(WHERE repeated_event_count>0)*1.0/count(*) AS repeat_rate,
          count(*) FILTER(WHERE missing_timestamps>0) AS items_with_missing_times
          FROM selected_cases GROUP BY group_name HAVING count(*)>=?
          ORDER BY median_days DESC NULLS LAST,items DESC,group_name LIMIT 1001""",limit=1000,parameters=[min_items])

    def group_cases(self,dimension,value):
        if dimension not in self.GROUPS:raise ValueError('Unsupported comparison dimension')
        return self.run(f"""SELECT case_id,matching_category,vendor,observed_span_days,repeated_event_count,event_count,missing_timestamps
          FROM selected_cases WHERE {self.GROUPS[dimension]} IS NOT DISTINCT FROM ?
          ORDER BY observed_span_days DESC NULLS LAST,case_id LIMIT 100""",parameters=[value])

    def repeated_activities(self):
        return self.run("""SELECT activity,count(*) AS affected_items,sum(n-1) AS excess_occurrences,sum(n) AS total_occurrences
          FROM (SELECT case_id,activity,count(*) AS n FROM selected_events WHERE activity IS NOT NULL
          GROUP BY case_id,activity HAVING count(*)>1) GROUP BY activity ORDER BY excess_occurrences DESC,activity LIMIT 30""")

    def flow_transitions(self):
        return self.run("""SELECT previous_activity AS from_activity,activity AS to_activity,count(*) AS occurrences,
          median(epoch(event_time-previous_time)/3600.0) AS median_wait_hours
          FROM selected_ordered_events WHERE event_rank>1 AND previous_activity IS NOT NULL AND activity IS NOT NULL
          GROUP BY previous_activity,activity ORDER BY occurrences DESC,previous_activity,activity LIMIT 1000""",limit=1000)

    def review_breakdown(self):
        return self.run("""SELECT matching_category,count(*) AS review_items FROM selected_sequence_signals
          GROUP BY matching_category ORDER BY review_items DESC,matching_category""")

    def item_summary(self,case_id,dimension=None,group_value=None):
        clause='';parameters=[case_id]
        if dimension is not None:
            if dimension not in self.GROUPS:raise ValueError('Unsupported comparison dimension')
            clause=f' AND {self.GROUPS[dimension]} IS NOT DISTINCT FROM ?';parameters.append(group_value)
        return self.run('SELECT case_id,purchase_document,matching_category,observed_span_days,repeated_event_count,event_count,missing_timestamps FROM selected_cases WHERE case_id=?'+clause,parameters=parameters)

    def recommendations(self,dimension='Process type',min_items=1):
        if min_items<1:raise ValueError('Minimum items must be positive')
        baseline,evidence=self.overview()
        groups=self.group_comparison(dimension,min_items)
        cards=[]
        if baseline['median_span_days'] is None:return cards
        for name,n,median,p90,repeats,repeat_rate,missing in groups['rows']:
            if n<min_items or median is None or median<=baseline['median_span_days']:continue
            label='Not recorded' if name is None else name
            cards.append({
                'title':f'Investigate observed duration · {label}',
                'observation':f'{n:,} selected items have a median recorded span of {median:,.2f} days, compared with {baseline["median_span_days"]:,.2f} days across the selected cohort.',
                'metric':{'items':n,'median_days':median,'p90_days':p90,'repeat_cases':repeats,'repeat_rate':repeat_rate,'items_with_missing_times':missing},
                'interpretation':'This group is associated with longer recorded spans. Complexity, recurring orders, timing conventions or incomplete observation may explain the difference.',
                'action':'Ask the process owner to review a sample, confirm completion boundaries and distinguish legitimate repeated handling before proposing a targeted pilot.',
                'expected_direction':'If avoidable waiting is confirmed, seek lower median and P90 spans without weakening matching controls.',
                'measurement':'Track the same eligible cohort definition, median/P90 spans, timestamp coverage, repeat patterns and validated exception burden before and after a pilot.',
                'limitation':'Descriptive association, not a causal finding or quantified saving. Groups can differ in case mix; small samples and censoring limit interpretation.',
                'group':name,'dimension':dimension,'evidence':[evidence,groups],
            })
            if len(cards)==3:break
        return cards

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
        def response(answer,evidence,visual=None):
            return {'answer':answer,'evidence':evidence,'mode':'Guided Analyst · SQL, not an LLM',
                    'filters':asdict(self.filters),'visual':visual,
                    'limitation':'Observed spans are not completion times. Repetition may be legitimate; associations do not establish causes or savings.'}
        if re.search(r'\b(summarize|summary|overview)\b',text):
            o,evidence=self.overview();groups=self.group_comparison()
            duration='No usable observed spans are available.' if o['median_span_days'] is None else f"Median observed duration is **{o['median_span_days']:.2f} calendar days**, with a **P90 of {o['p90_span_days']:.2f} days**."
            return response(f"The current selection contains **{o['item_count']:,} purchase-order items**, **{o['document_count']:,} documents** and **{o['event_count']:,} recorded events**. {duration} **{o['repeat_cases']:,} items** contain repeated activities. Review differences by matching flow before assigning causes or proposing a pilot.",[groups,evidence],'groups')
        if re.search(r'\b(compare|comparison)\b',text):
            groups=self.group_comparison();rows=groups['rows'][:2]
            if len(rows)<2:return response('Select at least two available process types to compare their recorded performance.',[groups])
            descriptions=[f"**{name or 'Not recorded'}**: {n:,} items, "+('duration unavailable' if median is None else f'{median:.2f}-day median / {p90:.2f}-day P90')+f', {rate:.1%} repeat-item rate' for name,n,median,p90,repeats,rate,missing in rows]
            return response('Comparing the first two groups by median duration in the selected cohort:\n\n'+'\n\n'.join(descriptions)+'\n\nUse the process-type filters to choose a specific pair. Case mix, recurring orders and incomplete observation may explain differences; this is not causal proof.',[groups],'groups')
        if re.search(r'\b(investigate|prioriti\w*|manager)\b',text):
            cards=self.recommendations();groups=self.group_comparison()
            if not cards:return response('No above-median group can be prioritized from this selection. Review data coverage and broaden the cohort before proposing an intervention.',[groups])
            card=cards[0]
            return response(card['observation']+'\n\n**Next investigation:** '+card['action']+'\n\n**Measure success:** '+card['measurement']+'\n\n'+card['limitation'],[groups],'groups')
        if re.search(r'\b(repeat|repeated|repetition|rework)\b',text) and re.search(r'\b(activities|activity|frequen\w*|where)\b',text):
            result=self.repeated_activities()
            if not result['rows']:return response('No repeated non-null activities were recorded in the selected items.',[result])
            activity,affected,excess,total=result['rows'][0]
            return response(f"**{activity}** has the most excess occurrences in this selection: **{excess:,}**, across **{affected:,} items** ({total:,} total occurrences in those repeated item/activity pairs). Multiple receipts or invoices can be legitimate. Review event timelines and matching rules before calling this avoidable work.",[result],'repeats')
        if re.search(r'\b(wait|waiting|intervals?|bottleneck|transitions?|delays?)\b',text):
            result=self.transitions()
            if not result['rows']:return response('No consecutive timed transitions were recorded for this selection.',[result])
            start,end,n,median,p90=result['rows'][0]
            return response(f"**{start} → {end}** has the longest displayed median recorded interval: **{median:.2f} hours**, with **{n:,} occurrences** and a **P90 of {p90:.2f} hours**. Investigate volume, category and case context. The interval mixes waiting, service and calendar effects and does not explain its cause.",[result],'waiting')
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
