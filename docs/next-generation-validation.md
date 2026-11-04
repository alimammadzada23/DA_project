# Next-generation implementation and acceptance status

This records a working upgrade, not completion of every aspirational item in the
master assignment. Nothing was pushed, publicly deployed or published. Changes
are local on `codex/insightpilot-next`, extending the actual application from
`feature/insightpilot` at `3c19a2e6368ccc888a92c3912853c31d34160abe`.
The original `main` contained only a README.

## Verified outcomes

| Check | Actual result and boundary |
|---|---|
| Baseline before implementation | **182 passed**, 0 failures/skips; 12 existing pandas deprecation warnings |
| Expanded complete suite | **271 passed**, 0 failures/errors/skips; the same 12 inherited warnings |
| SQLite/DuckDB connector teardown follow-up | 2 passed after explicitly closing SQLite catalog connections |
| Browser acceptance | Actual Linux Chromium: real-data metrics/charts, source catalog, selection-to-record scope, guided calculation, HTML/PNG download, multiple file uploads, explicit synthetic labeling, saved metric and responsive 768px layout; current final run is recorded in `next-generation-browser.json` |
| Power BI static validator | 22 JSON files validated; no TMDL parser, DAX execution, refresh or Windows rendering acceptance implied |
| Package build | Source distribution and wheel built; new modules and packaged SQL present. This is package-structure verification, not a native Mac/standalone packaged UI acceptance claim |
| Authentic case reproduction | Pinned NYC flights/airlines and Palmer penguins source bytes retained; row counts, means, rates, groups and selected statistical calculations independently reconciled |
| Synthetic scale | 10k, 100k, 1m rows; arithmetic verified, independent fresh processes, actual times and RSS recorded |
| CFPB acquisition | **Blocked**: proxy HTTP 403; zero genuine CFPB records acquired; no substitute findings |
| Live provider/PostgreSQL | **Unrun**: no configured live source/credentials; provider protocol/safety and generic API pagination use controlled mocks |
| Native Mac / Power BI Desktop | **Unrun** in this Linux environment |

Detailed machine-readable evidence: [test/source fingerprints](next-generation-test-evidence.json),
[browser](next-generation-browser.json), [benchmarks](next-generation-benchmarks.json),
[authentic cases](next-generation-public-cases.json). Original baseline JUnit and
current-run JUnit are retained in `outputs/`; these are generated, Git-ignored.

The browser script waits for actual dataset counts, selected-metric titles/units
and matching record counts, rather than treating an open port as readiness.
Early test iterations caught selector changes and premature screenshot capture;
final evidence must have `status: passed`. Screenshots are from the actual
application; `SYNTHETIC-` files explicitly denote generated data. No new task
snapshot restoration or cloud publication was attempted.

## Before and after

| Area | Audited baseline | Implemented extension |
|---|---|---|
| General imports | CSV/XLSX/Parquet and fixed mapped roles | TSV/JSON/JSON Lines too, several tables, streaming native import paths, read-only database extracts and bounded configured APIs |
| Understanding | Name aliases, sample/schema and a fixed mapping | Full arbitrary-column catalog, exact duplicates, numeric quantiles/skew, missing markers, type/role/grain uncertainty and reviewed definitions |
| Preparation | Mapping into fixed records | Immutable versions, explicit locale/date handling, standard thousands-group validation, date parts, renaming/selection/trim/nullification/deduplication and conversion-error rows |
| Metrics | One generic mapped measure, domain queries | Reusable named units/definitions/formulas, finite and conditional denominators, unique counts, ratios and additive-expression classification |
| Relationships | Separate flat snapshots | Key hypotheses, cardinality/unmatched/multiplication receipts, previews, version checks and unique-right-key fact-to-dimension calculations |
| Exploration | Limited fixed group/trend/band views | Ranked comparisons, full-data histograms/box summaries, paired scatter, calendar gaps, complete bounded heatmaps, interactive record selection and plotted data exports |
| Investigation | Existing domain-specific comparisons | General equal-period contributions and exact symmetric proportion rate/mix accounting with undefined-rate rejection |
| Statistics | Domain descriptive aggregates | Mean/Welch intervals/effect sizes, complete-pair Pearson/OLS, categorical/Fisher tests and Holm utility |
| Advanced modules | Existing process/complaint methods | Gated baseline forecasts, rolling changes, IQR/MAD screens, RFM, censored cohorts, ordered funnels, text frequencies and internally validated exploratory clustering |
| AI | Guided queries plus optional legacy provider tools | Additional consent-controlled schema-only finite planning; local execution and templated numerical answers; unsupported/ambiguous questions clarified |
| Reporting | Domain summaries and evidence exports | Generic self-contained HTML, plotted CSV, paginated CSV/XLSX, escaped text, source/version/filter/metric manifests and retained-version replay |
| Validation | 182 observed passing tests at baseline | 271 observed passing tests, actual Chromium acceptance, two independently reconciled public cases and measured million-row synthetic scaling |

Original ingestion, procurement, complaint, SQL, reporting and Power BI modules
were preserved. Only routing/landing and deployment-button presentation changed
in existing UI code; the generic services live in a new package. Original source
datasets and user changes were not reset or replaced.

## Module map and architectural choices

| Module in `src/insightpilot/universal` | Responsibility |
|---|---|
| `store.py` | Workspace catalog, source-byte/hash retention, atomic imports, immutable preparation/error versions, undo, serialized local connections and concurrent-change rejection |
| `profile.py` | Full-data SQL profiling, conservative identifier/role/type hypotheses, ambiguous markers, UTC/explicit date and locale-safe numeric expressions |
| `metrics.py` | Typed metric definition, safe AST grammar, explicit aggregate/conditional/finite counts, denominators and additive classification; no eval |
| `engine.py` | Shared typed filters, SQL aggregates, result limits/timeouts, source/version evidence IDs and paginated records |
| `relationships.py` | Conservative key suggestions, cardinality/coverage, previews, saved version receipts and double-counting protection |
| `statistics.py` | Mean/Welch/OLS/categorical inference with sample/variance/expected-cell gates; effect sizes and Holm correction |
| `advanced.py` | Time changes, baseline holdout forecasts, interpretable screens, RFM, cohorts, funnels and bounded text analysis |
| `segmentation.py` | Reviewed-feature standardization, fixed-seed bounded k-means, source-row assignments, centroids and internal silhouette validation |
| `investigation.py` | Exact observed proportion-change identity with rate/mix terms and reconciliation, no invented counterfactual rates |
| `insights.py` | Configurable bounded descriptive discovery with supporting calculations, context, limitations and next investigations |
| `connectors.py` | Read-only SQLite/DuckDB/PostgreSQL and bounded HTTPS page-number extraction; no saved credential values; original successful API pages/hash receipts retained |
| `assistant.py` | Local guided intents or optional schema-only provider plan; finite plan validation; deterministic numerical response |
| `report.py` | Escaped HTML, formula-safe spreadsheet exports, provenance manifests and nonmutating old-version replay |
| `visuals.py` | Purposeful accessible Plotly defaults, recommendations, calendar gaps and exact displayed-trace data export |
| `ui.py`, `ui_data.py` | Eight Streamlit pages, controls/state, import/preparation forms, cross-filtering, presentation and downloads |

DuckDB keeps analytical work local. SQL aggregates, column selection and paging
avoid full browser materialization. Reusable calculations stay outside Streamlit.
NumPy/SciPy were already present transitively and are now explicit dependencies.
PostgreSQL/browser tooling are optional extras. No microservices, distributed
infrastructure, automatic uploads, destructive schema rewrites or public service
deployment were introduced.

Read-only analytical connections disable external access. Import paths alone may
read selected local files. Generated model text never reaches SQL, Python or
shell in the new planner. Config replay validates source/hash/version and does
not fetch URLs. Old consent-controlled provider interfaces remain preserved;
their freeform narratives still require evidence review and are not claimed
to have the new planner's deterministic-answer guarantee.

## Real-data evidence

**NYC operations:** 336,776 records and a 16-row unique airline dimension. There
are 327,346 recorded arrival delays and 9,430 unknown outcomes. Mean recorded
delay is about 6.90 minutes; 23.715% of known arrivals exceed 15 minutes. January
and March each span 31 days, with a reconciled 1,830 departure-count difference.
The recorded delay-proportion difference is about +0.111 percentage points:
origin mix contributes −0.042 and within-origin recorded rates +0.154 points.
These accounting terms are not causal airport effects.

**Environmental measurements:** 344 Palmer penguin records; 342 recorded masses.
One planned within-Adelie male/female comparison uses 73 observations per group,
an approximately 675g mean difference and a model-based 95% Welch interval of
573–776g. Within-Adelie flipper/mass Pearson correlation is about 0.468. Sampling,
composition, independence and source-study limitations accompany the results.

Source revisions, licenses/citations and SHA-256 values are recorded. The flights
file is a reviewed package mirror, not a separately authenticated raw BTS
download. Penguin data are CC0 with attribution/LTER data-use expectations.
No original data bytes are committed to Git. The inaccessible CFPB source is a
pending third case with [exact acquisition instructions](case-study-cfpb-pending.md),
not an authentic completed case. The energy release was inspected but not used
for portfolio results because its mixed third-party licensing needs more review.

## Measured limits

The current benchmark JSON is authoritative. On this Linux machine, the final
one-million-row synthetic run imported in about **1.29s**, profiled in **1.78s**,
queried the metric in **0.030–0.044s**, and computed count/group/trend overview in
**0.78s**, with about **323 MiB** peak process RSS. These are one measured workload,
not universal performance guarantees. File-system caches were not flushed; query
timings use new connections after import/profiling. Browser rendering is separately
measured on the 336,776-row flight case, not assumed equal to server SQL latency.

Native file limits: 512 MiB / 5m rows / 150 columns, with only the reported scales
benchmarked. Excel retains 100 MiB / 500k rows and the expanded-archive safeguard.
Immutable versions consume disk. Statistical, clustering, funnel, text and
forecast limits/assumptions are explicit in the [usage guide](universal-intelligence-guide.md).
Numerical analytics use double precision; exact-decimal ledger accounting is not
claimed. Observation completeness, units, currency and business grain are not
automatically certified.

## Acceptance matrix and honest continuation

| Criterion | Verified scope / limitation |
|---|---|
| A: generalization | Same engine handles unrelated airline, ecology and generated commerce-shaped tables without source edits |
| B: understanding | Full schema/quality/roles and opportunities; inferred meanings remain hypotheses with correction controls |
| C: correctness | Independent fixtures/references, explicit denominators, exact contribution/rate-mix identities, statistical gates |
| D: visualization | Purposeful bounded charts, actual browser screenshots/exports; no geographic basemap capability claimed |
| E: investigation | Group/time accounting and chart-to-record intersection with traceable source rows |
| F: reusability | Generic metrics/relationships/filters and modular services; original optional domain engines retained |
| G: AI reliability | New finite planner produces local deterministic numerical answers; mocked provider verification, live acceptance unrun; legacy narratives need review |
| H: performance | Reproducible 10k/100k/1m measured synthetic workloads; no native Mac or every-format-at-limit promise |
| I: QA | Original tests preserved and expanded; source/wheel/static Power BI/browser checks with stated boundaries |
| J: portfolio evidence | Two authentic cases completed; required CFPB acquisition blocked/disclosed with exact continuation |
| K: usability | Eight purpose-specific pages, search/review/reset controls, actual file import and responsive browser checks; no external user study/accessibility certification |
| L: reproducibility | Source hashes, original extracts, immutable versions, formulas, filters, SQL, manifests/replay and pinned public-case scripts |

Next priorities, ordered by unmet dependency/value:

1. Obtain the actual CFPB source or allow the documented official destination,
   verify current source terms/schema/counts and complete the third genuine case.
2. Run native Mac and configured real PostgreSQL/provider acceptance. Complete
   Windows Power BI Desktop refresh/DAX/TMDL/rendering checks separately.
3. Add a more expressive validated conversational plan graph for complex
   follow-up questions and safely retire/restrict unverified legacy narratives.
4. Add formal user-configurable domain-pack registration, composite relationships,
   duplicate-grain detection and compatible cross-dataset comparison contracts.
5. Add explicitly approved reliable geography/maps, evaluated text classification,
   more advanced forecasting/change-point models and calibrated uncertainties.
6. Extend report/preset persistence and native PDF if needed; perform keyboard,
   screen-reader and external usability studies, plus broad format/platform loads.

These are unfinished capabilities or external acceptance tasks, not concealed
passes. The code is left usable and tested while the exact remaining work is
documented. [Factual CV/interview notes](next-generation-portfolio.md) distinguish
implementation evidence from adoption, deployment and business impact.
