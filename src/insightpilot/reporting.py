"""Reproducible investigations and independent source reconciliation."""
from collections import Counter
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
import statistics

import duckdb
from defusedxml.ElementTree import iterparse

from .agent import Analyst, LIMITATIONS
from .analytics import QUESTIONS, discover
from .ingest import quality


def reconcile(source, database):
    """Independent Python counters/medians; no SQL or importer metric reuse."""
    source = Path(source)
    analyst = Analyst(database)
    with duckdb.connect(str(database),read_only=True) as con:
        source_hash, mapping = con.execute('SELECT sha256,mapping FROM provenance').fetchone()
    digest = hashlib.sha256()
    with source.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''): digest.update(chunk)
    if digest.hexdigest() != source_hash:
        raise ValueError('Source SHA-256 does not match database provenance')
    mapping = json.loads(mapping)
    counts = Counter(); activities=set(); resources=set(); documents=set()
    variants=Counter(); spans=[]; repeats=0; repeat_cases=0
    defaults={'trace':{},'event':{}}
    def kind(e): return e.tag.rsplit('}',1)[-1]
    def attrs(e): return {c.attrib['key']:c.attrib.get('value') for c in e if 'key' in c.attrib}
    opener=gzip.open if source.suffix=='.gz' else open
    with opener(source,'rb') as stream:
        root=None
        for action,element in iterparse(stream,events=('start','end')):
            if root is None: root=element
            if action!='end':continue
            if kind(element)=='global':defaults[element.attrib.get('scope')]=attrs(element)
            if kind(element)!='trace':continue
            case=defaults['trace']|attrs(element)
            doc=case.get(mapping['purchase_document'])
            if doc is not None:documents.add(doc)
            ordered=[]; times=[]; names=[]
            for event in element:
                if kind(event)!='event':continue
                a=defaults['event']|attrs(event)
                name=a.get('concept:name'); resource=a.get('org:resource'); time=a.get('time:timestamp')
                name=name if name and name.strip() else None
                resource=resource if resource and resource.strip() else None
                date=datetime.fromisoformat(time.replace('Z','+00:00')) if time else None
                if date:times.append(date)
                if name is not None:activities.add(name);names.append(name)
                if resource is not None:resources.add(resource)
                ordered.append((date is None,date,len(ordered),name))
                counts['events']+=1
            counts['cases']+=1
            excess=len(names)-len(set(names));repeats+=excess;repeat_cases+=int(excess>0)
            if times:spans.append((max(times)-min(times)).total_seconds()/86400)
            variant=' → '.join(row[3] if row[3] is not None else '[missing]' for row in sorted(ordered))
            variants[variant]+=1
            root.remove(element);element.clear()
    spans.sort()
    def percentile(values,q):
        if not values:return None
        position=(len(values)-1)*q;lower=int(position);upper=min(lower+1,len(values)-1)
        return values[lower]+(values[upper]-values[lower])*(position-lower)
    reference={
        'item_volume':counts['cases'],'event_volume':counts['events'],'document_volume':len(documents),
        'activity_volume':len(activities),'user_volume':len(resources),
        'median_span':statistics.median(spans) if spans else None,'p90_span':percentile(spans,.9),
        'repeat_cases':repeat_cases,'repeat_events':repeats,'variant_count':len(variants),
    }
    checks={}
    for key,value in reference.items():
        actual=analyst.investigate(key)['rows'][0][0]
        matches=(actual==value) if value is None or isinstance(value,int) else (actual is not None and abs(float(actual)-value)<=1e-8*max(1,abs(value)))
        checks[key]={'python_reference':value,'sql_actual':actual,'matches':matches}
    return {'source_sha256':source_hash,'method':'Independent streaming Python counters and ordered variants; interpolated percentile',
            'passed':all(c['matches'] for c in checks.values()),'checks':checks,
            'scope':'Reconciles source bytes to database; does not certify source authenticity or business semantics'}


def analysis_report(database, directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    analyst=Analyst(database)
    with duckdb.connect(str(database),read_only=True) as con:
        dq=quality(con)
        try: process=discover(con)
        except ValueError as exc: process={'unavailable':str(exc)}
    questions={key:analyst.investigate(key) for key in QUESTIONS}
    evidence={'quality':dq,'questions':questions,'process_discovery':process,
              'scope':'SYNTHETIC ONLY' if dq['dataset']=='synthetic fixture' else 'Imported source; verify official provenance and field semantics',
              'limitations':LIMITATIONS}
    (directory/'evidence.json').write_text(json.dumps(evidence,default=str,indent=2)+'\n')
    def scalar(key):return questions[key]['rows'][0][0]
    lines=['# InsightPilot analytical decision brief','',f"**Data scope: {evidence['scope']}**",'',
           f"Source SHA-256: `{dq['sha256']}`",'',
           '## Executed observations','',
           f"- Recorded items: {scalar('item_volume')}; recorded events: {scalar('event_volume')}; distinct non-null purchase documents: {scalar('document_volume')}.",
           f"- Median observed span: {scalar('median_span')} calendar days; P90: {scalar('p90_span')} calendar days.",
           f"- Items containing repeated recorded activities: {scalar('repeat_cases')}; excess occurrences: {scalar('repeat_events')}.",
           f"- Distinct ordered variants: {scalar('variant_count')}.", '',
           'These are executed calculations on the imported source, not proof of inefficiency, completed cycle time, or compliance failure.', '',
           '## Bottleneck candidates','',
           'The following transitions rank by median recorded interval. Review volume, P90, category, event semantics and censoring before prioritizing a pilot. Intervals combine unobserved waiting/service and calendar effects.','',
           '| From | To | Occurrences | Median hours | P90 hours |','|---|---|---:|---:|---:|']
    for row in questions['transitions']['rows'][:10]:
        lines.append('| '+' | '.join(str(v).replace('|','\\|').replace('\n',' ') for v in row)+' |')
    lines += ['', '## Recommendations and decision gates','',
              '1. Verify published totals and all data-quality exceptions before presenting results. Check `quality` in evidence.json.',
              '2. Investigate high-volume, long-interval transitions within matching categories; compare distributions, not just overall ranks. No causal claim or promised saving follows from this ranking.',
              '3. Review repeated receipts/invoices with the AP/process owner before calling them avoidable rework.',
              '4. Validate matching-flow applicability before interpreting sequence signals. Current classifications are review-only.',
              '5. Define completion and resource-type rules before completed-case or human/batch comparisons.', '',
              '## Traceability and limitations','',
              'Every reported number and candidate above is traceable to the named questions, SQL, result rows and source hash in evidence.json. No financial benefit or implementation outcome is claimed. Power BI DAX/visual checks require Windows Desktop. A real-data management recommendation requires contextual review beyond these descriptive rankings.']
    (directory/'decision-brief.md').write_text('\n'.join(lines)+'\n')
    return {'evidence':str(directory/'evidence.json'),'decision_brief':str(directory/'decision-brief.md'),'scope':evidence['scope']}
