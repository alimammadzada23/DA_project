# Case study: NYC airline operations (authentic public extract)

Objective: compare recorded arrival delays and investigate departure-volume
changes without confusing flight counts, missing outcomes and airline quality.

Source: nycflights13/Rdatasets, pinned CSV revisions and SHA-256 hashes in
`next-generation-public-cases.json`. Upstream package declares CC0. Attribution:
Hadley Wickham / nycflights13; RITA, Bureau of Transportation Statistics. This is
a reviewed package mirror, not a separately authenticated original BTS download.

## Method and executed results

- 336,776 NYC departure records, 2013; one scheduled-flight record per
  row. Airline names are joined many-to-one through a separate 16-row dimension.
- Literal `NA` is converted to NULL in a new immutable version. Departure date
  is explicitly assembled from local year/month/day, avoiding UTC day shifts.
- 327,346 finite arrival delays; 9,430
  unknown delays. Unknown outcomes are excluded from delay means/rates and disclosed.
- Mean recorded arrival delay: 6.895 minutes.
- Recorded arrivals delayed over 15 minutes: 23.715%.
  Definition: delayed finite observations / all finite arrival-delay observations.
- Origin means (minutes): {"EWR": 9.107054735458092, "LGA": 5.783488234130908, "JFK": 5.551481036679838}.
- January: 27,004 recorded departures; March: 28,834.
  Their equal 31-day windows differ by 1,830 departures.
  Carrier contributions reconcile exactly to that change.
- Separately, the January-to-March recorded-delay proportion changes by
  +0.111 percentage points: origin-mix accounting
  contributes -0.042 points and within-origin
  recorded-rate accounting contributes +0.154
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
