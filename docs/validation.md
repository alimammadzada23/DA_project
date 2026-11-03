# Validation ledger

## Confirmed CFPB timestamp and donut defects — 2026-11-03

**182 tests passed, 0 failed, 0 skipped in 158.99 seconds**, including all previous 152 tests and 30 focused regressions. [JUnit](test-results.xml) records this full run. Twelve existing pandas/Plotly timezone-array deprecation warnings remain non-failing. Frozen offline sync checked the 60-package installation. No dependencies or procurement model were replaced.

The user reported a genuine **81,915-complaint / approximately 27 MB** CSV. **That source is unavailable here; no full-file or real-business finding verification is claimed.** The correction is validated with explicitly synthetic records using the exact source timestamp syntax and long CFPB product names. The earlier cloud source-access evidence remains historical; this repair did not depend on downloading another extract.

| Check | Actual result |
|---|---|
| Exact timestamp | `2026-09-02T19:36:07.000Z` → `2026-09-02` |
| Offset boundaries | `2026-09-02T00:30:00+02:00` → September 1 UTC; `2026-09-02T23:30:00-05:00` → September 3 UTC |
| Legacy formats | YYYY-MM-DD / MM/DD/YYYY retained; fractions, UTC offsets and offset-free ISO timestamps covered |
| Independent reference | Separate pandas date parser and Python counters agree with SQL; production `_date` is explicitly forbidden during the reconciliation regression |
| Hand-counted source | 12 raw rows → 10 retained valid unique IDs; 7 dated, 1 missing date, 2 invalid dates; daily counts 2/1/1/1/1/1 |
| Product/company checks | Product counts 5/5; company counts 5/5; exact company/product cells, timely denominators, inclusive dates and compound filters verified |
| Period comparisons | Explicit earlier/later issue counts and relative changes agree; UTC coverage, odd unmatched day and zero-baseline behavior covered |
| Entirely unusable dates | Clear atomic import failure; old all-NULL-date snapshot displays a visible reimport error, with import controls still accessible |
| Donut aggregation | 1,000 synthetic records → slices 600/180/90/60/50/20; below-3% categories combine into Other; selected-cohort shares recalculate |
| Rendered label geometry | Chromium at 1280×800 and 1440×900: six legend entries, zero overlapping/clipped labels, no horizontal page overflow |
| Real hover | The small Other slice visibly reports **20 selected complaints / 2.00%**. Native Plotly percentage calculation replaces an incorrectly resolved custom hover field |
| Browser filters | 1,000 → 500 after company filter → 250 with September 2–4; plotted daily counts 84/83/83 |
| Browser original-CSV reimport | Fresh snapshot selects automatically; old filters clear; 10 complaints / 7 dated / 1 missing / 2 invalid shown correctly |
| Preserved UI | All three existing browser scripts passed: procurement filters/reset/box selection/case lookup/guided SQL, all workspaces and isolation, CSV/Excel/Parquet and mapped-event uploads |
| Preserved Power BI | 22 existing JSON files pass schemas/static bindings; no Windows Desktop execution is claimed |

Evidence: [independent reference calculations](cfpb-date-reference.json), [date/donut browser checks](cfpb-date-browser-verification.json), [actual screenshots](screenshots/date-fix/), and the refreshed existing browser-check records. The new browser script uses visible calendar segments and Enter/blur to commit a date range; hidden native inputs are not treated as editable controls. Browser checks completed with zero uncaught errors.

The all-NULL dates already stored in the user's snapshot require **reimport of the original CSV**, followed by independent validation and regenerated exports. Exact Mac update/reimport commands are in [cfpb-date-fix.md](cfpb-date-fix.md). Native Mac execution and the full 81,915-row source remain unverified here. The tests establish the corrected parser, calculations and UI behavior on the documented references, not genuine-file acceptance.

## Earlier multi-dataset extension — 2026-11-03

**That stage passed 152 tests, 0 failed, 0 skipped in 95.38 seconds.** The original 96 tests were retained; 56 additional tests covered the dataset engine, complaint module and workspace UI on Linux / Python 3.12.14 / DuckDB 1.5.6. The current [JUnit artifact](test-results.xml) supersedes this run and includes all these tests. Twelve pandas/Plotly date-array deprecation warnings were non-failing. Frozen offline sync checked the 60-package installation; the lock includes Excel and flat Parquet readers.

| Capability | Executed evidence / outcome | Limit |
|---|---|---|
| Genuine CFPB access | Official website, API and hosted docs denied by proxy HTTP 403; failure timestamp retained in [source verification](cfpb-source-verification.json) | **0 genuine records**, no real findings or live schema/usage acceptance |
| Official documentation | CFPB-owned GitHub OpenAPI, export headers, serializer, date-filter builder and API metadata inspected at pinned revision | Documentation/code inspection is not a live deployed response |
| Official connector | Mocked HTTPS CSV/count protocol; required actual headers, exact count reconciliation, local half-open boundaries, failure receipts | Successful **live** download unverified |
| Complaint calculations | Independent CSV/Python counters versus SQL; 8 retained synthetic references, 4 Yes / 6 known responses, group/date distributions | These are software reference values, not CFPB observations |
| Reusable ingestion | CSV/.xlsx/Parquet readers, schema suggestions/approval, invalid dates/numbers, unsupported profiles, atomic failures, provenance | Bounded local extracts; native Mac performance unmeasured |
| Mapped profiles | Explicit revenue confirmation; customer representation; mapped complaints; CSV events reuse the existing process engine | No automatic profit, exposure, completion or causal KPI |
| Dataset isolation | Workspace/snapshot switching resets current answers; source ID/hash plus full filter values participate in cache keys; selected cohorts/drill-downs agree | Local files and manifests can be edited by their owner; provenance is not a publisher signature |
| AI | All six complaint business suggestions execute filtered SQL; no-consent blocks live transmission; mocked live tool remains inside the complaint catalog | No live provider call; arbitrary LLM prose needs evidence review |
| Procurement regression | All original tests and real Chromium filters/reset/box selection/case lookup/guided analysis passed | No genuine original BPI log; prior 4TU HTTP 503 not repeatedly retried |
| New workspace browser | Actual Chromium renders all complaint pages, donut/line/bar/stack/heatmap/distribution views; company filter 8→3, reset→8; issue-bar drill-down shows 3 records; no stale answers across complaints/custom/procurement | Explicitly synthetic QA sources; no public cloud preview |
| Actual browser uploads | CSV, Excel and Parquet upload → inspection → approved mapping → 4 records / confirmed synthetic revenue 35. Event CSV → 2 cases / 3 events; selecting Close retains 1 complete case / 2 events | [Upload record](upload-browser-verification.json); no private or genuine records |
| Power BI | Preserved procurement project: 22 JSON files pass schema/static bindings. New complaint Parquet/export, M/DAX candidates and semantic design prepared | No Windows Desktop, TMDL/DAX execution, refresh or visual acceptance |

The extension found and fixed a genuine pandas/Arrow bar-chart compatibility error. It also caught a live import/filter race: metadata and analytical connections used different DuckDB settings, causing a scan to omit the active dataset and fall back to another source. Readers now share the restricted configuration; exports stream Arrow batches through Python with the same read-only settings. A regression test holds an analytical connection open while metadata, query and export readers run. The selected-file guard surfaces a read failure instead of silently falling back to another dataset.

Chart selection updates the record dropdown through a scope-bound state change. Mapping, revenue/date/time-zone confirmation and synthetic flags are keyed by source hash, so different uploads do not inherit those approvals. Browser automation interacts with visible Streamlit labels/dropdown buttons and native box selection; it does not inject query results. Earlier browser-script locator/open-state failures were corrected and the complete scripts rerun successfully. Current records are [workspace checks](workspace-browser-verification.json), [upload checks](upload-browser-verification.json) and [procurement checks](browser-verification.json), with actual captures under [screenshots](screenshots/).

No API key was requested because the dashboard, guided analyst, imports and calculations run locally without one. Public CFPB access is a real network blocker; secrets and environment publication are not required for the Mac application. Exact download/import/launch commands and manual source fallback are in [multi-dataset-guide.md](multi-dataset-guide.md). The narrow CFPB network draft is saved for review, not applied.

The application remains **incomplete as a genuine-data interview case study** until an official extract has been obtained, independently reconciled and interpreted with its source limitations. Live AI, native Mac and Windows Power BI acceptance remain separate unexecuted checks.

## Earlier instance — 2026-11-02

**The earlier suite passed 96 tests, 0 failed, 0 skipped.** The current JUnit artifact supersedes that run and includes these tests. Validation used the frozen-install `.venv` (Python 3.12.14, DuckDB 1.5.6) and synthetic fixtures. No original-log checks were executed.

| Capability | Evidence / outcome |
|---|---|
| Locked installation | `uv sync --frozen --extra test` succeeded in a new repository-local .venv |
| Streaming ingestion | XES and gzip fixtures; atomic rejection of malformed/empty traces, duplicate IDs and offset-free dates |
| Quality | Counts, missing values, tied/reversed timestamps, duplicate signatures retained |
| Analytical references | All 20 catalog questions checked against hand-derived values, distributions or memberships |
| PM4Py | DFG-to-Petri-net discovery executed on fixture; edge totals checked |
| SQL restrictions | Mutations, multiple statements, PRAGMA, extension install, ATTACH, external file/network reads rejected |
| Optional LLM integration | Tool-calling protocol and audit tested with mocked transport; no-query narrative rejected |
| Streamlit | Missing-data and loaded-fixture AppTest interactions passed; real service started; `/` HTTP 200 and health `ok` |
| Power BI | 22 JSON files validate offline against 12 retained official schemas and static semantic bindings; 4 pages / 12 bound visuals |

An initial test revealed that DuckDB's parser normalizes a read-only PRAGMA to SELECT; a lexical SELECT/WITH gate was added and the regression test now passes. The first service start was blocked by the command sandbox's socket restrictions; approved execution outside that sandbox started normally. No TLS or signature checks were disabled.

## Phase status

1. **Data foundation:** importer, storage, quality, definitions and synthetic tests implemented. Original source/license/fields and scale validation incomplete.
2. **Process analysis:** descriptive discovery, intervals, variants, repetitions and review signals implemented. Real findings, matching-flow rules, resource classification and completion/censoring rules incomplete.
3. **Power BI:** generated project candidate and schema checks implemented. TMDL parsing, DAX execution, refresh, executive layout, trends, filters, drill-through and visual acceptance incomplete.
4. **Agent/application:** deterministic investigations, restricted SQL, mocked provider loop, audit, proposal-export approval and functional app checks implemented. Live API and narrative-grounding benchmark incomplete; no report-write tool.
5. **BA package:** hypothetical business case, BRD, traceability, backlog, UAT, proposed monitoring loop, interview package and decision brief drafted. A real current-state BPMN and evidence-backed recommendations are incomplete.
6. **Final validation:** fixture-level software validation executed; end-to-end genuine-data acceptance incomplete.

## Outstanding prerequisites and limitations

- Official TU/e description and DOI resolver now work. The resolver-confirmed 4TU file-repository landing page returns upstream HTTP 503 connection timeouts; exact download/run instructions are in docs/download-and-run.md. Required domains were saved in the environment draft: `research.tue.nl`, `doi.org`, `data.4tu.nl`, `learn.microsoft.com`. Saving a draft does not apply it. The current genuine-data dependency is the original artifact from the resolver-confirmed 4TU record, not environment publication.
- No original artifact, source license or actual-file schema has been verified. Attribute names, flow definitions and published totals are now confirmed from the official TU/e description, distinct from observed data findings. User-supplied provenance requires validation even if `--synthetic` is absent.
- Genuine full-volume performance and Apple Silicon operation remain unmeasured. The latest Linux-only synthetic check imported and prepared 100,000 events / 10,000 cases in 3.235 seconds with 259.7 MiB peak process RSS, then executed an overview in 0.030 seconds without an application-result cache (OS/storage caches were warm after ingestion); see docs/synthetic-scale-check.json. This is not a genuine-data or Mac benchmark.
- Human-versus-batch users and terminal conditions remain unknown. No universal matching rule is implemented. Recorded repeats and sequence signals do not certify compliance or avoidable work.
- Missing timestamps can understate observed intervals; missing activity values can understate repeats. Report coverage with these measures.
- Live LLM requires separately provisioned API credentials/access and explicit consent to send query results. The presence of a successful query does not prove every sentence or number in an LLM narrative; human evidence review remains necessary. Only deterministic catalog answers are directly rendered from query results.
- Windows Power BI Desktop and current authoring-documentation review are needed for TMDL/DAX/visual acceptance. Linux JSON validation is not a substitute.
- Automated tests are not stakeholder UAT, and there are no sign-offs, approvals, company findings, savings or implemented operational improvements.

Reusable install and start instructions were saved in the environment draft. The machine is prepared for software development and synthetic validation; this does **not** mean the real-data portfolio project is complete or that an environment snapshot has been published.

## Continuation verification

The project was extended in place. Vectorized case/event batches replaced per-row insertion; XES defaults are honored and nested attributes fail explicitly. Process discovery now retains untimed activity edges. Empty activity names become missing analytical values while raw attributes remain preserved.

Added independent source-byte reconciliation (ten quantities), source-labeled analytical reports, Parquet export manifests, parameterized category SQL, persistent application evidence/chat history, charts and downloads. The demo workflow, source reconciliation, report generation and Parquet export were executed with explicitly synthetic data. CLI live AI execution was attempted and stopped at missing credentials; no provider request or live-AI success is claimed.

A new query-provenance check initially failed because DuckDB 1.5.6 get_table_names binds some nested views incorrectly. Using DuckDB's parameterized SQL AST serializer avoids that binder path; relevant tests were rerun. Constant-only SELECT output cannot satisfy the AI's analytical-data evidence requirement. Arbitrary narrative correctness still requires review.

Current Microsoft authoring documentation is now accessible and was reviewed. It exposed a missing required PBIR version metadata file, now added. The candidate also includes monthly trend/slicer bindings and blank-safe count measures. Windows TMDL/refresh/DAX/rendering acceptance is still incomplete.

This continuation focuses on project functionality; it does not depend on publishing a Codex environment.

## Shared-cohort dashboard validation

The latest suite passes 96 tests. KPIs, category/variant/month/transition queries, process discovery, item timelines and guided answers use one selected-case predicate. Tests assert event/document/item counts, inclusive first-event dates with complete histories, null attributes, repeat/span filters, reset behavior, empty cohorts, missing-data warnings and evidence invalidation after filter changes. SQL tests cover raw-table access, dynamic table functions, qualified-name/CTE scope escapes and parameters. Mocked live tools cannot switch from scoped investigation to database-wide SQL. The offline guided analyst and CLI execute actual SQL without credentials; they are explicitly not an LLM.

A test caught alias rewriting that renamed the monthly chart count column; rewriting now only touches relation positions. Another test caught purchase-order items being routed to document counts; explicit item intents now take precedence. Both regressions were fixed before the final full suite. A separate first-open smoke check clicked the explicit synthetic-demo action from an empty configuration and verified the synthetic banner and four-item dashboard. The updated HTTP service was started and checked separately.

The official source description is recorded in docs/official-source-verification.json. Its metadata confirms matching-flow differences, legitimate multiple receipts/invoices, published counts and the NONE resource marker. The exact landing page returned by the DOI resolver is still unavailable from this instance. No genuine-log findings or license acceptance are implied by successful metadata retrieval.

## Professional dashboard upgrade

The existing ingestion, SQL security, analytical model, reference tests and Power BI artifacts are retained. The Streamlit presentation is organized into Executive Overview, Process Explorer, Root-Cause Investigation and AI Business Analyst with a tracked dark theme. Technical SQL/DAX/JSON, provenance, diagnostics and approval controls remain in collapsed Advanced / Developer Tools.

New bounded SQL aggregations supply monthly performance, duration bands, activity/repetition charts, group comparisons and evidence-backed investigation cards. Company/activity selection uses the same whole-item cohort as existing filters. No visualization reloads the entire raw event log into Python. Process-link volumes retain untimed edges without inventing waiting intervals. Recommendations apply minimum group volume before ranking and disclose association, censoring and case-mix limits.

A seeded portfolio fixture provides 1,200 explicitly synthetic items, 400 documents and 4,750 events; all timing and organizational patterns are invented. Documents retain a consistent company/vendor across items. The original four-item fixture remains available. The larger fixture independently reconciles ten source/SQL quantities and is not a replacement for the genuine BPI log.

Tests cover the original functionality plus chart-data references, new filters, group-scope drill-downs, all four pages, supported business questions, empty/error states and archived versus current AI answers. A resource-limit failure produces a business-readable error and leaves raw diagnostics in the developer section. A stale group selector found during filtering was fixed; root chart selections carry cohort/dimension identities so old selections cannot drive current details. Native Plotly selection is used for chart drill-down.

Original file acquisition was retried at the authoritative 4TU landing/API endpoints; both still return upstream HTTP 503. This TU/e retry returned HTTP 403. Previously retrieved official metadata remains recorded, but no original-data finding or source-license acceptance is claimed. Native Mac and Windows Power BI acceptance remain unverified; default guided analysis needs no API key.

## Final browser acceptance for the business interface

`scripts/browser_smoke.py` ran against the live Streamlit server in Chromium with the explicitly synthetic portfolio-v2 fixture. HTTP 200, all four pages, rendered Plotly charts, process-type filtering (1,200 to 298 items), reset, native box selection into the corresponding group's cases, item lookup, guided summary, hidden technical output by default and clearing stale answers all passed. No uncaught browser errors were recorded. See `docs/browser-verification.json` and `docs/screenshots/`. These are actual application captures, not generated mockups or a public cloud preview.

The combined browser workflow found a reset bug not reproduced by simple rerun tests: deleting multiselect widget keys could allow a later frontend event to restore old values. Reset now assigns explicit default selections. Chart payloads also carry cohort/dimension identity; older selections cannot drive current details. Browser regression includes reset followed by box selection and an actual item lookup.

The full suite passed **96 tests, 0 failed, 0 skipped** in the final run; `docs/test-results.xml` preserves that run. Original pipeline, security, reference, app and Power BI checks remain. The new resource-error state hides SQL diagnostics under developer tools; programming errors are not silently converted into passing tests.

Optional browser-regression reproduction requires Playwright and Chromium separately from normal app dependencies. Create the default portfolio-v2 fixture and start the dashboard with its path selected; then run the script with the local server URL and, if needed, `--chromium` pointing to a system browser. The script parses the explicitly synthetic source for reference counts and refuses non-fixture input.
