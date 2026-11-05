# InsightPilot business-analysis workflow

The reliability repair was committed first as
`716bd426051aabc2c63d16a9dd9dc0cccba8757b`. The UX upgrade is a separate local
commit. Neither change authorizes a push, merge or deployment.

## What changed

| Area | Implemented experience |
| --- | --- |
| Workspace Home | Central create/open/upload/continue/report actions; project cards show purpose, datasets, actual database activity, saved evidence/report counts and a stage based on saved objects. No completion percentages are invented. |
| Navigation | Six prominent central workflow stages, with Overview, AI Analyst, SQL Lab and Advanced in a secondary row. The sidebar holds project/dataset selection and shared analytical controls. |
| Data Hub | Separate upload and dataset-library views; automatic handoff to the imported dataset's profiled preview. Shows source hash, row/field counts, duplicates, detected types, missingness, warnings and ten actual source records. Explicit Explore/Quality/Investigate actions select that dataset. All original import mechanisms and reviewed relationships remain available. |
| Quality & Preparation | Prioritized review signals, optional improvements and usable fields; missing counts/percentages, duplicates, mixed parsing, date recognition, marker and identifier review. IQR screening is labeled a signal, not an error. Existing bulk review, conversion ledgers, reversible operations and undo remain. Explicitly choosing a shared date basis during review applies that choice once. |
| Explore | Type-supported chart entry cards, all seven existing exploration modes, an obvious filter action, visible scope and clearly disclosed category omissions. Ranked chart height adapts to label count. Scatter/box axes show reviewed or unconfirmed units. Save-chart and save-table actions retain executed evidence. |
| Investigate | Persistent business question, scope, observed facts, associations, hypotheses, uncertainties, actions and supporting evidence references. A separate comparison tab reuses existing period/group/relationship methods. Default period controls use actual date coverage. No causal engine or automatic explanation was added. |
| Reports | Saved drafts, ordered findings, executive summary, context, methodology, interpretation, recommendations/validation and limitations. Investigation notes can be copied explicitly, retaining their classifications; the executive summary remains analyst authored. Each report copies its evidence definitions and keeps each finding's own source/version/filter context. |

Report export reuses self-contained HTML and the existing Plotly/manifest format.
The calculation appendix uses the first selected saved context, not an unrelated
live sidebar selection. Evidence/source mismatches reject before calculation.
HTML commentary and embedded definitions are escaped. Limits remain bounded:
20 findings, 5,000 characters per narrative section and the existing 5 MB saved
object bound. A changed editor/scope invalidates an old preview until rebuilt.
Native PDF generation is not implemented; use browser printing where suitable.
Quick selection reports, legacy block assembly, manifest replay and Power BI
preparation exports remain in the Reports secondary expanders.

## Reused components and storage

The existing Streamlit shell, workspace naming/catalog, automatic full-table
profiling, Engine, DuckDB queries, Plotly figures, relationship checks, saved
views/blocks, `configuration`/`replay`, semantic review, preparation history,
HTML reporting and exports are reused. New code consists of workflow presentation
and investigation/report draft assembly. Drafts use namespaced existing layout
objects (`ux.investigation:` and `ux.report:`); no database migration, data rewrite,
frontend framework, dependency, model or AI architecture change is involved.

The calculation engine, SQL Lab validation, local AI client/planner/validator,
statistics and original acceptance oracles remain unchanged from the separate
reliability commit. See [original UI audit](UX_WORKFLOW_AUDIT.md) and the final
verification receipt for the exact checks and results.

## Verification evidence

Automated results, browser-run evidence, source hashes and scope-preservation
checks are recorded in [the verification receipt](bi-workflow-validation.json).
Selected screenshots are retained alongside this guide after visual inspection.
The final full suite passed **918 tests**, with no failures or skips. Mocked
integration reconciled **51/51** cases; Standard remained **48/51**, with exactly
the three baseline failures described below. The actual public-data browser
workflow and the separately labeled HTTP-mock AI browser regression both passed.
See [the security receipt](bi-workflow-security.json) for the staged-file review.

| Actual captured view | Screenshot |
| --- | --- |
| Project home at 1280×832 | [Workspace Home](screenshots/bi-workflow/workspace-home.png) |
| Profiled import and dataset library | [Data Hub](screenshots/bi-workflow/dataset-library.png) |
| Prioritized quality signals | [Quality](screenshots/bi-workflow/quality-priorities.png) |
| Independently reconciled filtered categories | [Explore](screenshots/bi-workflow/filtered-exploration.png) |
| Explicitly reviewed calendar meaning | [Daily trend](screenshots/bi-workflow/reviewed-time-trend.png) |
| Analyst question, evidence and hypotheses | [Investigation](screenshots/bi-workflow/investigation.png) |
| Saved findings and report narrative | [Report builder](screenshots/bi-workflow/report-builder.png) |
| Self-contained export rendered offline | [Exported report](screenshots/bi-workflow/standalone-report.png) |
| Different public schema | [Palmer penguins](screenshots/bi-workflow/different-public-schema.png) |

Actual Chromium workflows use two existing, checksum-verified public inputs:

- **6,433 NYC TLC trip sample records**, from the pinned seaborn-data mirror.
  The sample's collection/modification details and population coverage are not
  established. Original public-use/source terms still apply; raw data is not
  redistributed in this change.
- **344 Palmer penguin measurements**, from the pinned palmerpenguins source.
  Horst, Hill & Gorman (2020); Gorman, Williams & Fraser (2014), PLoS ONE
  9(3):e90081. The dataset is CC0; attribution and original source are recorded in
  the source acquisition scripts and verification receipt.

These are public data, not synthetic fixtures or evidence about a current
business. Browser test notes are explicitly sample descriptions. Numerical chart
checks compare the rendered Plotly values with independent Pandas references.
The browser checks use isolated workspaces under ignored `outputs/`; existing
personal projects and source bytes are not modified.

Cloud Chromium is tested at 1440×900 and 1280×832. Exported HTML is rendered from
its downloaded bytes in an offline browser context; managed Chromium's `file:`
navigation restriction is left intact. This proves neither native Finder use nor
Mac performance. No real Qwen inference is measured by these UX checks; any
HTTP-mock AI integration is labeled separately.

## Pending checks and known limits

- **UCI Bank Marketing is pending on the user's Mac.** The cloud proxy rejected
  the official archive host. No UCI source was fabricated, no restriction was
  bypassed and no replacement dataset was labeled UCI. The user confirmed the
  existing Mac archive at `~/DA_project/outputs/uci-bank-marketing.zip` and
  authorized continuing with the available public datasets.
- Real Qwen acceptance remains a separate Mac check; mocked tests do not establish
  natural-language reliability or Apple Silicon latency/memory performance.
- Standard analysis retains `s18`/`s19` conditional-rate wording and `s22`
  natural-language period-boundary limitations. They safely clarify and remain
  reported failures against unchanged references, as reproduced before the
  reliability commit. They were not relabeled as passes.
- Date meaning, business units, KPIs and outcome denominators still require review.
  Raw numeric descriptions and category counts work without this business setup.
  A single observed period is shown as its calculated table, with guidance on
  grain/window selection, rather than presented as a trend.
- Investigation notes are analyst supplied, not causal findings. Existing Advanced
  methods retain their suitability checks; no new predictive framework was added.
- Project search/cards are bounded to twelve displayed matches; the selector and
  search still access other projects. Source previews and record pages retain
  their explicit bounds. Preparation is explicit and reversible.

## Try the improved workflow on the existing Mac

After an authorized push, preserve local edits and update the existing
`codex/insightpilot-local-ai` clone. No dependency change, rebuild, new clone or
workspace migration is required. Continue using **Start InsightPilot.command**.
Existing workspaces, data and saved analyses remain in their current locations.

1. Start at **Workspace Home**. Open an existing project or create a named project
   with a business purpose, then choose **Upload dataset**.
2. In **Data Hub**, import a file with the correct delimiter. Review the automatic
   library preview and use **Explore data**, **Review data quality** or **Start
   analysis**. A preview selection alone does not switch analysis scope.
3. Use **Quality & Preparation** to prioritize actual issues. Basic numeric
   descriptions do not need role approval. Review timestamp meaning only when
   requesting time analysis; use preparation/undo for explicit data changes.
4. In **Explore**, choose a recommended view or any existing chart mode. Use
   **Adjust filters** and inspect the active scope. Box-select bars to inspect
   their source records. Save useful charts/tables to the report library.
5. In **Investigate**, define the question and save facts, associations and
   hypotheses separately. Use **Compare & break down** for supported comparisons,
   with appropriate definitions and coverage.
6. In **Reports**, choose findings, set their order, optionally copy investigation
   notes, and write the narrative. Save a draft, preview and export HTML. Restart
   and reopen the draft to verify retained evidence and sources.
7. Repeat with the real UCI archive on the Mac. Apply the semicolon delimiter to
   `bank-full.csv`, inspect all job groups and a top-five count chart, filter the
   data, save evidence and build a report. Preserve real-model failures separately.

For actual Qwen acceptance, run from the existing clone:

```bash
./"Check Local AI.command" --runs 3 --diagnostics --bank-data outputs/uci-bank-marketing.zip
```

Use the additional unreviewed descriptive workflow in
[the reliability guide](LOCAL_AI_DESCRIPTIVE_ANALYSIS.md). Record the exact commit,
Ollama/model version and every failed case; this UX verification does not replace it.
