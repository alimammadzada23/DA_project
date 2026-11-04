# Local AI implementation audit and plan

Base: `e3874eeef01275700852bef683eb06ccae056a74` on
`codex/unified-insightpilot`. Clean starting tree. Working branch:
`codex/insightpilot-local-ai`. Baseline: **294 passing tests** (73.01s), no failures.
No push, merge, pull request or deployment is authorized for this upgrade.

The actual entry remains `app.py → workspace_ui.main → universal.ui`.
`universal.assistant` accepts only summary/groups/trend patterns, with a separate
opt-in external provider choosing the same three operations. It has no durable
analytical conversation. Engine supplies filtered, version-bound SQL evidence;
SQLLab supplies parsed SELECT/CTE and join scopes; Metric compiles a finite safe
aggregate expression language. Plotly, reports and saved blocks are reusable.
The rate intent branch in `plan_question` incorrectly references an undefined
`metric`; this will be fixed with a regression test.

Ollama binary and loopback service are absent on this Linux executor. No inference
was run, no model downloaded and no software installation attempted. Actual Qwen
planning quality and Apple Silicon performance remain unverified. Engineering
verification will use explicitly labeled mocks and a separately runnable real
model benchmark.

Implementation sequence:

1. Define a small JSON Schema and typed plans; validate columns, roles, measures,
   filters, dates, denominators, limits and saved relationships against the active
   workspace. Keep model output separate from trusted SQL generation.
2. Add an optional client for Ollama's official loopback REST API: discovery,
   selected model metadata, local-only enforcement, structured output, bounded
   context/response/time budgets and explicit failure categories. No pull/install
   endpoint, automatic download, cloud fallback or arbitrary URL routing.
3. Execute validated multi-measure, grouped, distribution, rate and time plans
   through the existing engine and SQL safeguards. Produce deterministic numerical
   summaries, valid Plotly charts and report/evidence handoff.
4. Add bounded per-session conversation state keyed by workspace identity, table,
   immutable version, semantics, selected metric, date context and shared filters.
   Follow-ups inherit validated intent; incompatible context resets and repeated
   UI reruns do not trigger inference.
5. Improve the existing Analyst UI with clear modes, model status, example
   questions, results, follow-ups, evidence and graceful fallback. Preserve the
   external mode as an explicit opt-in and retain existing standard functionality.
6. Add additive workspace display-name metadata without moving directories or
   touching source/version identity; named creation/renaming, stable fallbacks and
   clearer repeated-import labels. Warn about likely CSV delimiter mismatches
   before import without automatically reinterpreting bytes.
7. Keep every existing regression assertion, add security/integration/context
   tests, a 30+ question independent-reference benchmark, real browser workflows,
   before/after captures, a Mac guide, package and secret-scan checks. Report mocked
   integration results separately from actual-model planning quality.
