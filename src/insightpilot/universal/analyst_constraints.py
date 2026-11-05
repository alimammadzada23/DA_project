"""Explicit request constraints, independent of model output and source rows.

This is a bounded intent check, not a second general language model. Only literal
limits, ordering, named measures/groups and clear references to prior results are
recognized. All repaired candidates still need schema and live validation.
"""
from copy import deepcopy
from dataclasses import dataclass, field
import re

from .analyst_plan import ClarificationRequired, PlanError, validate_structure

ONES = 'one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split()
TENS = 'twenty thirty forty fifty sixty seventy eighty ninety'.split()
NUMBERS = {word: i for i, word in enumerate(ONES, 1)} | {word: i * 10 for i, word in enumerate(TENS, 2)} | {'one hundred': 100, 'hundred': 100, 'zero': 0}
NUMBERS.update({ten + ' ' + one: NUMBERS[ten] + NUMBERS[one] for ten in TENS for one in ONES[:9]})
WORDS = '|'.join(re.escape(w) for w in sorted(NUMBERS, key=len, reverse=True) if 'hundred' not in w)
NUMBER = r'(?:[-+]?\d+(?:\.\d+)?|(?:(?:one|two|three|four|five|six|seven|eight|nine) )?hundred(?: (?:and )?(?:' + WORDS + r'))?|' + '|'.join(re.escape(w) for w in sorted(NUMBERS, key=len, reverse=True)) + r')'
MEASURES = {'average': 'avg', 'mean': 'avg', 'avg': 'avg', 'median': 'median', 'sum': 'sum', 'total': 'sum', 'minimum': 'min', 'maximum': 'max', 'min': 'min', 'max': 'max'}


def normalized(text):
    return re.sub(r'[^\w.+-]+', ' ', text.casefold()).replace('-', ' ').strip()


def explicit_limit(question, columns):
    # Preserve signed/decimal digits so an unsupported limit cannot become a
    # different supported integer. Written compounds may use spaces or hyphens.
    text = re.sub(r'(?<=[a-z])-(?=[a-z])', ' ', question.casefold())
    names = '|'.join(re.escape(c.casefold()) for c in columns)
    noun = r'(?:(?:' + names + r')\s+)?(?:groups?|categor(?:y|ies)|results?|rows?|records?|entries)'
    patterns = [r'\b(?:top|bottom|highest|lowest|largest|smallest)\s+(' + NUMBER + r')\b',
                r'\b(' + NUMBER + r')\s+(?:highest|lowest|largest|smallest|most|fewest)\b',
                r'\b(' + NUMBER + r')\s+' + noun + r'\b',
                r'\blimit(?:ed)?\s+(?:to\s+)?(' + NUMBER + r')\b']
    values = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            word = match[1]
            try: value = NUMBERS[word] if word in NUMBERS else int(word)
            except ValueError: raise ClarificationRequired('Use an integer result limit from 1 to 100; no different limit was substituted.')
            if not 1 <= value <= 100:
                raise ClarificationRequired('Use a result limit from 1 to 100; no different limit was substituted.')
            values.append(value)
    if len(set(values)) > 1:
        raise ClarificationRequired('The request contains conflicting result limits. Choose one limit; no calculation ran.')
    return values[0] if values else None


def named_fields(text, columns):
    # Longest names win overlapping matches (e.g. net value versus value).
    matches = []
    for column in sorted(columns, key=len, reverse=True):
        for match in re.finditer(r'(?<!\w)' + re.escape(normalized(column)) + r'(?!\w)', text):
            if not any(match.start() < end and match.end() > start for start, end, _ in matches):
                matches.append((match.start(), match.end(), column))
    return sorted(matches)


def record_count(text):
    return bool(re.search(r'\b(?:record|row|observation|entry) counts?\b|\bcounts? (?:the |of )?(?:records|rows|observations|entries)\b|\b(?:number of|how many|most|fewest|total) (?:records|rows|observations|entries)\b', text))


def conflict():
    raise PlanError('The proposed plan conflicts with explicit request constraints. No calculation ran.',
                    reason='request_constraints', repair_feedback='Preserve every explicit request constraint in the context: exact named grouping and measures, result limit, ordering and previous-result scope. Return a complete schema-valid plan. Do not add filters, replace a requested measure, or broaden a referenced selection.')


@dataclass
class RequestConstraints:
    limit: int | None = None
    descending: bool | None = None
    groups: list | None = None
    measures: list | None = None
    operation: str | None = None
    sort_records: bool = False
    sort_by: str | None = None
    sort_measure: dict | None = None
    previous: dict | None = None
    preserve: bool = False
    reuse_inputs: bool = False
    new_analysis: bool = False
    sort_only: bool = False
    cohort: list = field(default_factory=list)

    def summary(self):
        return {k: v for k, v in {'limit': self.limit, 'descending': self.descending,
                'group_by': self.groups, 'measures': self.measures, 'operation': self.operation,
                'result_limit_policy': 'explicit' if self.limit is not None else 'preserve_previous_selection' if self.preserve or self.reuse_inputs else 'complete_grouped_aggregates',
                'preserve_previous_group_membership': self.preserve, 'sort_existing_result_only': self.sort_only}.items() if v is not None}

    def requires_complete_groups(self, data):
        return self.limit is None and not (self.preserve or self.reuse_inputs) and data['operation']=='aggregate' and bool(data.get('group_by'))

    def repair(self, raw):
        validate_structure(raw)  # Never normalize invalid schema/measure arguments.
        candidate = deepcopy(raw); repairs = []
        if candidate['action'] != 'analyze': return candidate, repairs
        def set_explicit(key, value):
            if candidate.get(key) != value:
                candidate[key] = deepcopy(value); repairs.append(key)
        if self.preserve or self.reuse_inputs:
            set_explicit('inherit', True)
            if self.sort_only:
                # Context belongs to the application, even when the model omits
                # inheritance. Explicit incompatible groups/inputs are rejected.
                set_explicit('operation', self.previous['operation'])
            if self.limit is None: set_explicit('limit', self.previous['limit'])
        elif self.new_analysis and candidate.get('inherit'):
            set_explicit('inherit', False)
        if self.limit is not None: set_explicit('limit', self.limit)
        if self.descending is not None: set_explicit('descending', self.descending)
        if self.operation is not None: set_explicit('operation', self.operation)
        effective = candidate | {'group_by': candidate.get('group_by', self.previous.get('group_by', []) if self.previous and candidate.get('inherit') else [])}
        if self.requires_complete_groups(effective): set_explicit('limit', None)
        if self.sort_by is not None: set_explicit('sort_by', self.sort_by)
        elif self.sort_records: set_explicit('sort_by', 'records')
        elif self.sort_measure:
            measures = candidate.get('measures', self.previous.get('measures', []) if self.previous else [])
            indices = [i for i, measure in enumerate(measures) if measure == self.sort_measure]
            if len(indices) == 1: set_explicit('sort_by', 'm' + str(indices[0]))
        return candidate, repairs

    def validate(self, validated):
        data = validated.data
        if data['action'] != 'analyze': return
        if self.limit is not None and data['limit'] != self.limit: conflict()
        if self.descending is not None and data['descending'] != self.descending: conflict()
        if self.operation and data['operation'] != self.operation: conflict()
        if self.requires_complete_groups(data) and data['limit'] is not None: conflict()
        if self.sort_by is not None and data['sort_by'] != self.sort_by: conflict()
        if self.sort_records and data['sort_by'] != 'records': conflict()
        if self.sort_measure:
            wanted = next((i for i, m in enumerate(data['measures']) if m == self.sort_measure), None)
            if wanted is None or data['sort_by'] != 'm' + str(wanted): conflict()
        if self.groups is not None and data['group_by'] != self.groups: conflict()
        if self.measures is not None:
            actual = [({'aggregation': 'records'} if m == {'aggregation': 'saved', 'metric': 'Records'} else m) for m in data['measures']]
            if len(actual) != len(self.measures) or any(m not in actual for m in self.measures): conflict()
        if self.preserve or self.reuse_inputs:
            if data['group_by'] != self.previous['group_by'] or data['relationship'] != self.previous['relationship']: conflict()
            if self.sort_only or self.reuse_inputs:
                for key in ('measures', 'filters', 'fields', 'date_basis', 'grain', 'periods'):
                    if data.get(key) != self.previous.get(key): conflict()
                if data['limit'] != (self.limit if self.limit is not None else self.previous['limit']): conflict()


def request_constraints(engine, question, previous=None, response=None):
    old = previous.data if hasattr(previous, 'data') else previous
    # Quoted filter values are data, not ranking/measure instructions. Bound
    # extraction to the analytical clause before an explicit filter clause.
    main = re.split(r'\b(?:where|whose|having)\b', question, maxsplit=1, flags=re.I)[0]
    names = '|'.join(re.escape(c) for c in sorted(engine.info['columns'], key=len, reverse=True))
    main = re.sub(r'\b(?:for|only|with)\s+(?:' + names + r')\s+(?:is\s+)?(["\x27])[^"\x27]*\1', ' ', main, flags=re.I)
    known = {normalized(c) for c in engine.info['columns']}
    main = re.sub(r'(["\x27`])([^"\x27`]*?)\1', lambda m: m[2] if normalized(m[2]) in known else ' ', main)
    text = normalized(main); fields = named_fields(text, engine.info['columns'])
    constraints = RequestConstraints(limit=explicit_limit(main, engine.info['columns']), previous=old)
    reference = bool(re.search(r'\b(?:those|these|their|previous|prior|same|displayed|shown|them)\b|\b(?:that|the|this) results?\b|\b(?:visuali[sz]e|plot|chart) (?:that|it)\b', text))
    follow = bool(old) and bool(reference or re.match(r'(?:now|and|also|instead|then|next)\b', text))
    sorting = bool(re.search(r'\b(?:sort|reorder|order)\b', text))
    if reference and sorting and not old:
        raise ClarificationRequired('There is no previous validated result to sort. Ask a complete analysis question first.')
    constraints.sort_only = follow and sorting and not re.search(r'\b(?:calculate|compute|compare|average|mean|median|sum|minimum|maximum)\b', re.split(r'\bby\b', text, maxsplit=1)[0])
    if constraints.sort_only and (old.get('operation') != 'aggregate' or not old.get('group_by')):
        raise ClarificationRequired('Reordering previous results currently requires a grouped aggregate. No different calculation ran.')
    constraints.preserve = bool(old and old.get('operation') == 'aggregate' and old.get('group_by') and (reference or constraints.sort_only))
    if constraints.preserve:
        constraints.previous = old; constraints.cohort = deepcopy(response.get('cohort', [])) if response else []
        if not constraints.cohort:
            raise ClarificationRequired('The previous result contains no displayed groups to reuse. Ask a new complete question.')
        if constraints.limit is not None and constraints.limit > len(constraints.cohort):
            raise ClarificationRequired('A referenced selection cannot be expanded to additional groups. Ask a new complete ranking to select a larger population.')

    ascending = r'\b(?:ascending|increasing|low(?:est)? to high(?:est)?|smallest to largest|fewest to most)\b'
    descending = r'\b(?:descending|decreasing|high(?:est)? to low(?:est)?|largest to smallest|most to fewest)\b'
    asc, desc = bool(re.search(ascending, text)), bool(re.search(descending, text))
    remaining = re.sub(ascending + '|' + descending, ' ', text)
    low = bool(re.search(r'\b(?:lowest|smallest|fewest|bottom)\b', remaining))
    high = bool(re.search(r'\b(?:highest|largest|most)\b', remaining))
    if asc and desc or low and high:
        raise ClarificationRequired('The request contains conflicting ranking directions. Choose one ordering; no calculation ran.')
    if asc or desc:
        if not constraints.preserve and (asc and high or desc and low or asc and re.search(r'\btop\b', remaining)):
            raise ClarificationRequired('Select the ranked groups first, then ask to re-sort those results. Selection and display ordering differ here.')
        constraints.descending = desc
    elif low or high: constraints.descending = high
    elif re.search(r'\btop\b', text): constraints.descending = old['descending'] if follow else True
    if constraints.sort_only and constraints.descending is None:
        raise ClarificationRequired('Specify ascending or descending order for the previous results.')

    groups = []
    for start, end, name in fields:
        before, after = text[:start], text[end:]
        if re.search(r'\b(?:by|per|across|for each|for every|within each|in each)\s+(?:(?:the|each|every)\s+)?$', before) or re.match(r'\s+(?:groups?|categor(?:y|ies))\b', after):
            groups.append(name)
        elif constraints.descending is not None and re.search(r'\bwhich\s*$', before) and re.match(r'\s+(?:has|have|is|are)\b', after):
            groups.append(name)
        elif groups and re.search(r'\band\s*$', before) and name not in groups:
            groups.append(name)
    if groups: constraints.groups = list(dict.fromkeys(groups))
    if constraints.sort_only:
        constraints.groups = old['group_by']
        constraints.sort_records = record_count(text)
        constraints.sort_by = 'records' if constraints.sort_records else old['sort_by']
        targets = [m for m in old['measures'] if m.get('column') in [name for _, _, name in fields] and any(MEASURES.get(word) == m['aggregation'] for word in text.split())]
        if len(targets) == 1:
            constraints.sort_by = 'm' + str(old['measures'].index(targets[0]))
        elif len(targets) > 1:
            raise ClarificationRequired('Choose one existing measure for the requested ordering.')
        elif not constraints.sort_records and any(word in MEASURES for word in text.split()):
            raise ClarificationRequired('The requested sort measure is not uniquely identified in the previous result. Choose an existing measure.')
        return constraints

    # Distribution/time/rate questions have other reviewed semantics. Do not
    # reinterpret an explicit request for one of those as a grouped aggregate.
    other = re.search(r'\b(?:distribution|histogram|box|quartiles?|scatter|outliers?|trend|monthly|weekly|daily|periods?|rate|proportion|percent(?:age)?|share)\b|\bover time\b', text)
    if other or re.search(r'\bbetween\b.+\band\b', text): return constraints
    measures = []
    count_inputs = [name for start, _, name in fields if name not in groups and re.search(r'\bcounts?\s+(?:of\s+)?(?:(?:nonmissing|nonblank|non null)\s+)?$', text[:start])]
    if record_count(text) or re.search(r'\bcounts?\s+(?:by|per|for each)\b', text) or groups and re.search(r'\bcounts\b', text) and not count_inputs: measures.append({'aggregation': 'records'})
    inputs = list(dict.fromkeys(name for _, _, name in fields if name not in groups))
    for match in re.finditer(r'\b(' + '|'.join(MEASURES) + r')\b', text):
        if any(start <= match.start() < end for start, end, _ in fields): continue
        if match[1] == 'total' and re.match(r'\s+(?:records|rows|observations|entries)\b', text[match.end():]): continue
        nearby = [name for start, _, name in fields if name not in groups and start >= match.end() and re.fullmatch(r'\s*(?:(?:of|the)\s+)*', text[match.end():start])]
        column = nearby[0] if nearby else inputs[0] if len(inputs) == 1 else None
        if column:
            measure = {'aggregation': MEASURES[match[1]], 'column': column}
            if measure not in measures: measures.append(measure)
        else:
            raise ClarificationRequired('Name the exact field for every requested aggregate. No requested statistic was discarded or replaced with a record count.')
    if re.search(r'\b(?:distinct|unique)\b', text) and len(inputs) == 1:
        measures.append({'aggregation': 'distinct', 'column': inputs[0]})
    for name in dict.fromkeys(count_inputs): measures.append({'aggregation': 'count', 'column': name})
    probability = re.search(r'\b(\d+(?:\.\d+)?)(?:st|nd|rd|th)?\s+percentile\b', text)
    quantile = re.search(r'\bquantile\s+(\d+(?:\.\d+)?)\b|\b(\d+(?:\.\d+)?)\s+quantile\b', text)
    if probability or quantile:
        value = float(probability[1]) / 100 if probability else float(quantile[1] or quantile[2])
        if not 0 <= value <= 1 or len(inputs) != 1:
            raise ClarificationRequired('Specify one numeric field and a quantile probability from zero to one, or a percentile from zero to one hundred.')
        measures.append({'aggregation': 'quantile', 'column': inputs[0], 'quantile': value})
    if any(m.get('column') for m in measures) and any(name not in {m.get('column') for m in measures} for name in inputs):
        raise ClarificationRequired('Specify the aggregation for each named input field; no field was silently omitted.')
    if measures:
        constraints.measures = measures
        if groups or constraints.preserve: constraints.operation = 'aggregate'
    ranking = constraints.descending is not None or constraints.limit is not None
    constraints.sort_records = ranking and bool(record_count(text))
    if ranking and not constraints.sort_records and len(measures) == 1:
        constraints.sort_measure = measures[0]
    # "Now top N" changes only selection size. Bind its other inputs even if
    # the model omits inherit; shrinking is restricted to displayed membership.
    if follow and constraints.limit is not None and not groups and not measures and old.get('operation') == 'aggregate' and old.get('group_by'):
        constraints.reuse_inputs = True
        constraints.groups = old['group_by']; constraints.sort_by = old['sort_by']
        if response and response.get('cohort') and constraints.limit <= len(response['cohort']):
            constraints.preserve = True; constraints.cohort = deepcopy(response['cohort'])
    elif follow and measures and not groups and old.get('operation') == 'aggregate' and old.get('group_by'):
        constraints.preserve = True; constraints.groups = old['group_by']; constraints.operation = 'aggregate'
        constraints.cohort = deepcopy(response.get('cohort', [])) if response else []
        if not constraints.cohort:
            raise ClarificationRequired('The previous result contains no displayed groups to reuse. Ask a new complete question.')
    constraints.new_analysis = bool(groups and measures and not constraints.preserve and not constraints.reuse_inputs)
    return constraints
