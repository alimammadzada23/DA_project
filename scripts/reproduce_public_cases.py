"""Acquire pinned, licensed public extracts, reconcile independently and write cases.

Run with --download for first acquisition. Existing source bytes are verified,
never replaced. Each analysis run gets its own workspace; --write-docs explicitly
refreshes the generated documentation from executed results.
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

import httpx
import pandas as pd
import plotly.express as px

from insightpilot.universal import Workspace
from insightpilot.universal.engine import Engine, Filter, frame_of
from insightpilot.universal.metrics import Metric, save_metric
from insightpilot.universal.relationships import inspect_relationship, related_groups, save_relationship
from insightpilot.universal.report import configuration, html_report, replay
from insightpilot.universal.statistics import regression, welch_test
from insightpilot.universal import visuals
from insightpilot.universal.investigation import proportion_change

SOURCES={
    'flights.csv':{
        'url':'https://raw.githubusercontent.com/vincentarelbundock/Rdatasets/1dcc2bf5f955cc1224a3e1307256e1fe86b68dae/csv/nycflights13/flights.csv',
        'sha256':'b791a2590c9167aa0bd3cb45af3f64101180878cf088c183eae4011e3a654f42',
        'rows':336776,'license':'CC0, per upstream nycflights13 DESCRIPTION',
        'documentation':'https://raw.githubusercontent.com/tidyverse/nycflights13/df98ef215aa8216fe0838a0b8ac5bada646d814c/DESCRIPTION',
        'citation':'Wickham, nycflights13; source RITA/Bureau of Transportation Statistics, NYC departures in 2013. Rdatasets mirror, not an independently acquired BTS extract.'},
    'airlines.csv':{
        'url':'https://raw.githubusercontent.com/vincentarelbundock/Rdatasets/1dcc2bf5f955cc1224a3e1307256e1fe86b68dae/csv/nycflights13/airlines.csv',
        'sha256':'5c9f0c947dca4a2b7e009c9dc86b96fc637e45bf39a034bffb413e84b98b08f6',
        'rows':16,'license':'CC0, nycflights13','documentation':'https://nycflights13.tidyverse.org',
        'citation':'nycflights13 airline metadata, retained as a related dimension table.'},
    'penguins.csv':{
        'url':'https://raw.githubusercontent.com/allisonhorst/palmerpenguins/8957207b78d6ccd1b4654a9dd9c9041b657478ab/inst/extdata/penguins.csv',
        'sha256':'f204db2c753b0937caac3cb35258562c14f073e4bbc76be24b4c51ce22767a93',
        'rows':344,'license':'CC0; preserve attribution and LTER data-use expectations',
        'documentation':'https://github.com/allisonhorst/palmerpenguins#license',
        'citation':'Horst, Hill & Gorman (2020), palmerpenguins; Gorman, Williams & Fraser (2014), PLoS ONE 9(3):e90081. Measurements from Palmer Station LTER, 2007–2009.'}}


def acquire(root,download):
    root.mkdir(parents=True,exist_ok=True)
    for filename,info in SOURCES.items():
        target=root/filename
        if not target.exists():
            if not download: raise ValueError(f'Missing {target}; rerun with --download to acquire its pinned public source.')
            temporary=target.with_suffix(target.suffix+'.partial')
            with httpx.stream('GET',info['url'],timeout=60,follow_redirects=False) as response:
                response.raise_for_status()
                with temporary.open('xb') as stream:
                    size=0
                    for chunk in response.iter_bytes():
                        size+=len(chunk)
                        if size>100*1024*1024: raise ValueError('Public source exceeds 100 MiB.')
                        stream.write(chunk)
            if hashlib.sha256(temporary.read_bytes()).hexdigest()!=info['sha256']:
                raise ValueError('Pinned public source checksum mismatch; rejected bytes preserved for diagnosis.')
            temporary.replace(target)
        if hashlib.sha256(target.read_bytes()).hexdigest()!=info['sha256']:
            raise ValueError(f'{target} differs from the pinned public extract; existing bytes were preserved.')


def flight_case(root,run):
    w=Workspace.create(run/'flights-workspace')
    for filename,table in [('flights.csv','flights'),('airlines.csv','airlines')]:
        w.add_file(root/filename,table,provenance={'origin':'verified_public_source_extract',**SOURCES[filename]})
    w.transform('flights',{'kind':'nullify_many','markers':['NA']})
    w.transform('flights',{'kind':'date_parts','year':'year','month':'month','day':'day','new_name':'departure_date'})
    w.approve_roles('flights',{'carrier':'company','origin':'geography','dest':'geography','departure_date':'date',
        'arr_delay':'duration','dep_delay':'duration','distance':'measure','rownames':'identifier','tailnum':'identifier'},
        'One scheduled flight departure record in the NYC-2013 package; flight number alone is not a unique flight identity.')
    e=Engine(w,'flights')
    metric=Metric('Mean recorded arrival delay','avg("arr_delay")','Mean across finite recorded arrival delays; unknown delays excluded.','minutes',
        'Missing arrival delays are not zeros and are not classified as cancellations without additional evidence. Airlines, routes and airports have different mixes.')
    rate=Metric('Recorded arrivals delayed over 15 minutes','100 * count_if("arr_delay", ">", 15) / valid("arr_delay")',
        'Finite arrival delays over 15 minutes divided by all finite recorded arrival delays.','percent',
        'Conditional on recorded arrival delay. Not a cancellation-inclusive on-time-performance statistic.')
    save_metric(w,'flights',metric); save_metric(w,'flights',rate)
    delayed_count=Metric('Delayed recorded arrivals','count_if("arr_delay", ">", 15)','Known arrival delays exceeding 15 minutes','arrivals')
    known_count=Metric('Known recorded arrivals','valid("arr_delay")','Finite recorded arrival-delay denominator','arrivals')
    save_metric(w,'flights',delayed_count); save_metric(w,'flights',known_count)
    rel=inspect_relationship(w,'flights','carrier','airlines','carrier'); save_relationship(w,'carrier_names',rel)
    result=e.metric(metric); delayed=e.metric(rate); groups=e.groups('origin',metric)
    airlines=related_groups(e,rel,'name',metric)
    contributions=e.compare_periods('carrier',Metric('Recorded departures','count()'),'departure_date',
        ('2013-01-01','2013-01-31'),('2013-03-01','2013-03-31'))
    rate_mix=proportion_change(e,'origin','departure_date',delayed_count,known_count,
        ('2013-01-01','2013-01-31'),('2013-03-01','2013-03-31'))
    # Independent source-byte reconciliation uses pandas, not the engine's normalization.
    ref=pd.read_csv(root/'flights.csv')
    assert len(ref)==336776==e.count()
    assert abs(result['rows'][0][0]-float(ref.arr_delay.mean()))<1e-10
    assert abs(delayed['rows'][0][0]-100*float((ref.arr_delay.dropna()>15).mean()))<1e-10
    assert dict((r[0],r[1]) for r in groups['rows'])==ref.groupby('origin').arr_delay.mean().to_dict()
    assert rel['right_duplicate_keys']==0 and rel['left_unmatched_rows']==0 and rel['left_join_rows']==len(ref)
    expected_march=int((ref.month==3).sum()); expected_january=int((ref.month==1).sum())
    assert sum(r[3] for r in contributions['rows'])==expected_march-expected_january
    first=ref[ref.month==1].dropna(subset=['arr_delay']); second=ref[ref.month==3].dropna(subset=['arr_delay'])
    expected_rate_change=float((second.arr_delay>15).mean()-(first.arr_delay>15).mean())
    assert abs(rate_mix['rate_change']-expected_rate_change)<1e-12
    assert abs(rate_mix['mix_contribution']+rate_mix['rate_contribution']-expected_rate_change)<1e-12
    config=configuration(e,metric); restored,definition=replay(w,config)
    assert restored.metric(definition)['rows']==result['rows']
    figures=[visuals.ranked(groups,'How do recorded arrival delays differ by NYC origin?','minutes'),
        visuals.trend(e.trend('departure_date',metric),'Recorded arrival delay by month','minutes'),
        visuals.contribution(contributions,'January → March departure-count contribution','records')]
    report=html_report(e,metric,evidence=[delayed,groups,airlines,contributions,rate_mix['evidence']],figures=figures,title='NYC flight operations — recorded delay and volume')
    (run/'flights-report.html').write_text(report)
    (run/'flights-manifest.json').write_text(json.dumps(config,default=str,indent=2))
    facts={'rows':len(ref),'recorded_arrival_delays':int(ref.arr_delay.notna().sum()),'missing_arrival_delays':int(ref.arr_delay.isna().sum()),
        'mean_arrival_delay_minutes':result['rows'][0][0],'delayed_over_15_minutes_percent':delayed['rows'][0][0],
        'origin_mean_delay_minutes':dict((r[0],r[1]) for r in groups['rows']),
        'january_departures':expected_january,'march_departures':expected_march,
        'january_to_march_departure_change':expected_march-expected_january,'relationship':rel,
        'delay_rate_mix_percentage_points':{'observed_change':100*rate_mix['rate_change'],
            'origin_mix':100*rate_mix['mix_contribution'],'within_origin_rate':100*rate_mix['rate_contribution']},
        'workspace':str(w.directory),'reference_checks_passed':True}
    evidence_json={'facts':facts,'metric':result,'delay_rate':delayed,'groups':groups,'related_groups':airlines,'contribution':contributions,
        'rate_mix':{k:v.to_dict('records') if isinstance(v,pd.DataFrame) else v for k,v in rate_mix.items()}}
    (run/'flights-evidence.json').write_text(json.dumps(evidence_json,default=str,indent=2))
    text=f'''# Case study: NYC airline operations (authentic public extract)

Objective: compare recorded arrival delays and investigate departure-volume
changes without confusing flight counts, missing outcomes and airline quality.

Source: nycflights13/Rdatasets, pinned CSV revisions and SHA-256 hashes in
`next-generation-public-cases.json`. Upstream package declares CC0. Attribution:
Hadley Wickham / nycflights13; RITA, Bureau of Transportation Statistics. This is
a reviewed package mirror, not a separately authenticated original BTS download.

## Method and executed results

- {facts['rows']:,} NYC departure records, 2013; one scheduled-flight record per
  row. Airline names are joined many-to-one through a separate 16-row dimension.
- Literal `NA` is converted to NULL in a new immutable version. Departure date
  is explicitly assembled from local year/month/day, avoiding UTC day shifts.
- {facts['recorded_arrival_delays']:,} finite arrival delays; {facts['missing_arrival_delays']:,}
  unknown delays. Unknown outcomes are excluded from delay means/rates and disclosed.
- Mean recorded arrival delay: {facts['mean_arrival_delay_minutes']:.3f} minutes.
- Recorded arrivals delayed over 15 minutes: {facts['delayed_over_15_minutes_percent']:.3f}%.
  Definition: delayed finite observations / all finite arrival-delay observations.
- Origin means (minutes): {json.dumps(facts['origin_mean_delay_minutes'])}.
- January: {expected_january:,} recorded departures; March: {expected_march:,}.
  Their equal 31-day windows differ by {expected_march-expected_january:,} departures.
  Carrier contributions reconcile exactly to that change.
- Separately, the January-to-March recorded-delay proportion changes by
  {rate_mix['rate_change']*100:+.3f} percentage points: origin-mix accounting
  contributes {rate_mix['mix_contribution']*100:+.3f} points and within-origin
  recorded-rate accounting contributes {rate_mix['rate_contribution']*100:+.3f}
  points. These terms reconcile exactly, using known-arrival denominators; they
  are not estimates of causal airport effects.
- Independent pandas calculations from the original CSV reconcile counts,
  mean delay, delay percentage, airport means and period changes. The metric
  manifest also replays successfully without changing the active source version.

## Deeper investigation and visual evidence

The generated standalone report includes ranked airport mean-delay bars, monthly
delay trends, airline-name comparisons and a carrier contribution waterfall.
The source, filters, metric formula, units, full SQL and source hashes accompany
the results. Raw observations paginate in the application; they are not loaded
into the report's charts.

Potential implication: prioritize investigation of route mix, schedules, weather
and missing-outcome coverage. These descriptive differences do not establish
an airline/airport causal effect or the effectiveness of an intervention.

## Limitations and reproduction

Only NYC departures in 2013, not current performance or all US aviation. Records
are not independent repeated samples of aircraft or routes. Missing arrival
delays do not by themselves identify cancellation reasons. Mean comparisons are
unadjusted for route, season, weather and scheduling. Package completeness does
not authenticate the complete original BTS pipeline.

Run `uv run --frozen python scripts/reproduce_public_cases.py --download`.
Open Universal Intelligence with `INSIGHTPILOT_UNIVERSAL_WORKSPACE` set to the
generated flights workspace. Reports, manifests and executed evidence are saved
in the printed run directory. No source bytes are committed to Git.
'''
    return facts,text


def penguin_case(root,run):
    w=Workspace.create(run/'penguins-workspace')
    w.add_file(root/'penguins.csv','penguins',provenance={'origin':'verified_public_source_extract',**SOURCES['penguins.csv']})
    w.transform('penguins',{'kind':'nullify_many','markers':['NA']})
    w.approve_roles('penguins',{'species':'category','island':'geography','sex':'category','body_mass_g':'measure',
        'flipper_length_mm':'measure','bill_length_mm':'measure','bill_depth_mm':'measure','year':'date'},
        'One penguin measurement record in the simplified Palmer LTER dataset, 2007–2009; sampling scope requires the original study context.')
    e=Engine(w,'penguins')
    metric=Metric('Mean recorded body mass','avg("body_mass_g")','Mean finite body mass, not a population-weighted estimate.','grams',
        'Species, sex, island and sampling year affect the observed composition. Missing measurements are excluded.')
    save_metric(w,'penguins',metric)
    groups=e.groups('species',metric)
    adelie=Engine(w,'penguins',[Filter('species','eq','Adelie')])
    test=welch_test(adelie,'body_mass_g','sex','male','female')
    fit=regression(adelie,'flipper_length_mm','body_mass_g')
    ref=pd.read_csv(root/'penguins.csv'); reference=ref[ref.species=='Adelie']
    assert e.count()==len(ref)==344
    assert abs(e.metric(metric)['rows'][0][0]-float(ref.body_mass_g.mean()))<1e-10
    expected=reference[reference.sex=='male'].body_mass_g.mean()-reference[reference.sex=='female'].body_mass_g.mean()
    assert abs(test['mean_difference']-float(expected))<1e-10
    paired=reference[['flipper_length_mm','body_mass_g']].dropna()
    assert abs(fit['pearson_r']-float(paired.corr().iloc[0,1]))<1e-10
    scatter=e.scatter('flipper_length_mm','body_mass_g'); data=frame_of(scatter)
    figures=[visuals.ranked(groups,'How does recorded body mass differ by species?','grams'),
        visuals.style(px.scatter(data,x='x',y='y',opacity=.65),'Body mass and flipper length: observed association','Flipper length (mm)','Body mass (g)')]
    (run/'penguins-report.html').write_text(html_report(e,metric,evidence=[groups,test['evidence'],fit['evidence']],figures=figures,
        title='Palmer penguins — composition-aware environmental analysis'))
    facts={'rows':len(ref),'recorded_body_mass':int(ref.body_mass_g.notna().sum()),'missing_body_mass':int(ref.body_mass_g.isna().sum()),
        'adelie_male_n':test['n_first'],'adelie_female_n':test['n_second'],
        'species_mean_body_mass_g':dict((r[0],r[1]) for r in groups['rows']),
        'adelie_male_minus_female_mean_g':test['mean_difference'],'difference_ci95_g':test['difference_ci95'],
        'welch_p_value':test['p_value'],'hedges_g':test['hedges_g'],
        'within_adelie_flipper_mass_pearson_r':fit['pearson_r'],'workspace':str(w.directory),'reference_checks_passed':True}
    (run/'penguins-evidence.json').write_text(json.dumps({'facts':facts,'comparison':test,'regression':fit},default=str,indent=2))
    text=f'''# Case study: environmental field measurements (authentic public data)

Objective: understand biometrics while distinguishing species composition,
within-species associations, missing measurements and inferential assumptions.

Source: Palmer Station LTER, distributed by Horst, Hill & Gorman (2020),
palmerpenguins. Original research: Gorman, Williams & Fraser (2014), PLoS ONE
9(3):e90081. The publisher explicitly states CC0 and requests attribution and
respect for LTER data-use/collaboration expectations. This portfolio analysis is
not represented as new ecological research or an official scientific finding.

## Method and executed results

- {len(ref)} source records from 2007–2009. Original CSV retained and SHA-256
  checked against a pinned publisher revision.
- Literal `NA` is explicitly converted to NULL in a reversible version; no
  records are discarded. {facts['recorded_body_mass']} recorded masses and
  {facts['missing_body_mass']} unknown masses.
- Species mean recorded mass (g, rounded): {json.dumps({k:round(v) for k,v in facts['species_mean_body_mass_g'].items()})}.
- One planned within-Adelie comparison: male minus female mean mass is
  approximately {test['mean_difference']:.0f} g ({test['n_first']} male,
  {test['n_second']} female observations), model-based 95% Welch interval
  [{test['difference_ci95'][0]:.0f}, {test['difference_ci95'][1]:.0f}] g;
  two-sided p={test['p_value']:.2g}, Hedges g={test['hedges_g']:.2f}.
- Within-Adelie flipper-length/body-mass Pearson r={fit['pearson_r']:.3f}.
  This within-species calculation avoids interpreting a between-species mixture
  as evidence for the same relationship within every species.
- Independent pandas calculations reconcile row/missing counts, mean mass,
  the sex mean difference and complete-pair correlation.

## Investigation and interpretation

The standalone report shows ranked species means and a labeled biometrics
scatterplot, along with the SQL and selected Adélie evidence. The difference in
means is descriptive; the model-based interval depends on independence and
sampling assumptions. Statistical evidence does not establish mechanism or
population-wide representativeness. The pooled-species scatter is labeled as an
association and is not used for the within-species inferential result.

Practical next investigation: inspect measurement coverage and stratify by
sampling year/island before generalizing. No ecological intervention, business
impact or conservation outcome is claimed.

## Limitations and reproduction

A small observational field dataset, not a random sample of all penguins.
Species/island/year composition, sex-label missingness, repeated-measurement
uncertainty and selection can affect inference. No population incidence,
causality, prediction deployment or commercial outcome is established.

Run `uv run --frozen python scripts/reproduce_public_cases.py --download` and
open its penguins workspace with `INSIGHTPILOT_UNIVERSAL_WORKSPACE`. Complete
calculations, assumptions and source metadata are saved with the generated report.
'''
    return facts,text


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--download',action='store_true')
    parser.add_argument('--source-directory',type=Path,default=Path('data/real-cases'))
    parser.add_argument('--write-docs',action='store_true')
    args=parser.parse_args(); acquire(args.source_directory,args.download)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:6]
    run=Path('outputs/public-cases')/stamp; run.mkdir(parents=True)
    flights,flight_doc=flight_case(args.source_directory,run)
    penguins,penguin_doc=penguin_case(args.source_directory,run)
    report={'generated_at':datetime.now(timezone.utc).isoformat(),'sources':SOURCES,
        'flights':flights,'penguins':penguins,'report_directory':str(run),
        'cfpb':{'status':'blocked, not a completed genuine-data case',
                'source':'https://www.consumerfinance.gov/data-research/consumer-complaints/search/api/v1/',
                'observed_error':'ProxyError: 403 Forbidden on 2026-11-04; no genuine records acquired',
                'procedure':'uv run --frozen insightpilot cfpb-download data/cfpb/real-week --start 2025-01-01 --end 2025-01-08',
                'existing_report':'docs/complaints-case-study.md'},
        'claims':'Two authentic cross-domain cases verified; CFPB third case blocked and disclosed. No synthetic record is described as authentic.'}
    (run/'validation.json').write_text(json.dumps(report,indent=2)+'\n')
    if args.write_docs:
        Path('docs/case-study-flight-operations.md').write_text(flight_doc)
        Path('docs/case-study-penguin-measurements.md').write_text(penguin_doc)
        Path('docs/next-generation-public-cases.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
