# Validation ledger

## Current instance — 2026-11-02

**73 tests passed, 0 failed, 0 skipped in the current suite; exact results are recorded in docs/test-results.xml.** Validation uses the frozen-install `.venv` (Python 3.12.14, DuckDB 1.5.6). These tests use synthetic fixtures. See the committed JUnit result and synthetic reference outputs for reproducible evidence. No official dataset checks have been executed.

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

The latest suite passes 73 tests. KPIs, category/variant/month/transition queries, process discovery, item timelines and guided answers use one selected-case predicate. Tests assert event/document/item counts, inclusive first-event dates with complete histories, null attributes, repeat/span filters, reset behavior, empty cohorts, missing-data warnings and evidence invalidation after filter changes. SQL tests cover raw-table access, dynamic table functions, qualified-name/CTE scope escapes and parameters. Mocked live tools cannot switch from scoped investigation to database-wide SQL. The offline guided analyst and CLI execute actual SQL without credentials; they are explicitly not an LLM.

A test caught alias rewriting that renamed the monthly chart count column; rewriting now only touches relation positions. Another test caught purchase-order items being routed to document counts; explicit item intents now take precedence. Both regressions were fixed before the final full suite. A separate first-open smoke check clicked the explicit synthetic-demo action from an empty configuration and verified the synthetic banner and four-item dashboard. The updated HTTP service was started and checked separately.

The official source description is recorded in docs/official-source-verification.json. Its metadata confirms matching-flow differences, legitimate multiple receipts/invoices, published counts and the NONE resource marker. The exact landing page returned by the DOI resolver is still unavailable from this instance. No genuine-log findings or license acceptance are implied by successful metadata retrieval.
