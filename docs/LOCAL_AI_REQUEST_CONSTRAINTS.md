# Explicit analytical constraints and follow-up scope

Focused repair on `codex/insightpilot-local-ai`, starting at `7099841`.
No model, analytics engine, deployment or workspace migration was changed.
This revision is local until a new push is authorized.

## Actual evidence and diagnosis

The user reported **13/16 on actual Qwen3.5 4B**, improved from 2/16, using
Ollama **0.40.14** on a MacBook Air at `7099841`. The three redacted plan shapes
below were provided by the user. They identify interpretation failures rather
than errors in DuckDB's median or count calculations.

[Reported plan properties](local-ai-constraint-reported-plans.json) ·
[Current constraint/prompt contract](local-ai-constraint-contract.json)

| Failure | Cause in the previous implementation | Focused repair |
| --- | --- | --- |
| Sort previous top-three results ascending; model supplied limit 10 and no direction | Defaults admitted a different ordering/limit, and membership preservation depended on the model's `inherit` flag | Bind referenced membership in application state, retain the previous selection size, enforce explicit direction and unchanged inputs |
| Median by a category; model used `group_distribution` with a median measure | Distribution-specific validation ran before checking the requested group-level aggregate | Require aggregate for an explicitly named grouped statistic, then run all existing semantic checks |
| Ten smallest groups; model omitted limit and direction | Default limit 15 and descending order contradicted explicit words in the question | Enforce the written limit and ascending count ranking |

The provided properties are actual user-reported redacted evidence. Automated
tests reconstruct complete protocol envelopes around those properties; they do
not constitute a new Qwen run or claim to recover fields omitted from the report.

## General checks

`analyst_constraints.py` derives constraints from the current question, actual
schema names and application-owned previous intent/results. It contains no
acceptance case IDs, dataset-specific field names, expected answers or source-row
samples. Recognized constraints include:

- Numeric and standard English written limits from 1 to 100, including hyphenated
  compounds, top/bottom requests, numbered smallest/largest groups and explicit
  `limit to` wording. Conflicting or recognized unsupported limits clarify.
- Ascending/descending, increasing/decreasing, lowest-to-highest and the reverse,
  smallest/largest/fewest/most and top/bottom ranking. Ordering a selected top-N
  differently is distinguished from selecting a new bottom-N population.
- Exact named group-level means, medians, sums, minima, maxima, record/nonmissing/
  distinct counts and explicit quantiles/percentiles. Multiple requested
  statistics must remain present. Unresolved statistics or input bindings ask for
  clarification rather than discarding part of a request.
- Clear previous-result references, sorting-only follow-ups, size-only follow-ups
  and measure-only follow-ups. Shrinking a selection keeps its displayed
  membership; a referenced selection cannot expand to new groups. A new complete
  ranking can select a new population even if the model incorrectly sets inherit.

Quoted category literals and explicit filter clauses are excluded from numerical
limit/measure extraction. Distribution, time, period-comparison and rate requests
retain their existing semantics. These are bounded explicit-language checks, not
a claim to parse arbitrary natural language or prove every model interpretation.

## Bounded repair and execution

Every model candidate first passes the unchanged closed structural schema. A
single deterministic reconciliation pass can change only `limit`, `descending`,
`sort_by`, `operation` and the application-bound inheritance flag, using explicit
request constraints. It does not invent a measure, field, filter, metric, result
or approval, strip invalid measure arguments, or convert clarification/refusal
into an analysis. Unknown properties and invalid operations remain rejected.

The repaired candidate then passes full schema and live semantic validation plus
request-consistency checks. A valid but different requested grouping/measure or
an altered sorting-only input can receive **one** correction request to the same
local model. Missing-operation, missing-value and request-constraint corrections
share **two total model attempts** and the existing remaining time budget.
Persistent conflicts produce no calculation.

Previous-result membership comes from the application's last executed response,
not model-provided values or an inferred fresh ranking. It is applied through the
existing parameterized DuckDB cohort restriction. Empty referenced selections
clarify instead of becoming unrestricted queries. Sorting retains measures,
grouping, filters, relationship and selection size unless a supported change was
explicitly requested. Shared context/version checks still precede execution.

Numeric role and row-grain review, relationship approval, new-rate approval,
read-only SQL scope and local-only Ollama checks remain in force. Saved canonical
plans and cohort evidence use their existing format and replay without inference.
No datasets or workspaces are rewritten.

The UI identifies corrected request constraints in plain language. Acceptance
reports expose `application_constraint_repairs` separately from model attempts
and correction requests. A repaired end-to-end pass is not evidence that the raw
model plan honored every constraint.

## Automated evidence and remaining real acceptance

[Validation receipt](local-ai-constraint-validation.json) records the final full
suite and independent benchmarks. Tests include both the reconstructed reported
shapes and variations using different fields, spaces/underscores in names,
different values, tied counts and a missing group label. Independent DuckDB
queries check numerical results and ordering. Save/replay, empty selections,
scope expansion, invented filters/grouping, unreviewed numbers and new-rate
approval are covered. No original acceptance questions, DuckDB reference queries,
comparison rules or existing benchmark reference tables were changed.

The 16-case acceptance passes with explicit HTTP mocks, including the three
reported shapes, with diagnostics both enabled and disabled. The existing
51-case integration and Standard results are recorded separately. Mocks test the
repair and execution pipeline; they do not establish Qwen language quality.

**No real Qwen acceptance was performed for this revision. The latest actual
result is the user's 13/16 at `7099841`.** Final acceptance must be repeated on
the Mac after this local commit is transferred through a separately authorized
push or other chosen transfer. Keep the same existing clone, environment,
Ollama and `qwen3.5:4b`; no reinstall, model change or app rebuild is needed.

```bash
cd ~/DA_project
./"Check Local AI.command" --diagnostics
```

Retain `report.json` and `rejected-plans.jsonl` from the new run under
`outputs/local-ai-acceptance/<run>/`. Record `ollama --version`. The opt-in
diagnostics still verify the exact synthetic fixture, redact unknown/free text,
and capture received plan projections before reconciliation or rejection. No
real workspace is selected and nothing is automatically uploaded.

For the three previously failing cases, verify the original projected plan,
the reported application corrections, the final canonical plan and the
independent numerical reference. The sorting case must return exactly the
previous three members in ascending order, median must use aggregate, and
smallest-groups must use limit 10 and ascending order. All sixteen end-to-end
checks must pass on actual Qwen before claiming successful real acceptance.
Application-guarded refusals and deterministic corrections remain attributed to
the application. The checker retains every failure; no expected result was
relaxed to improve the score.
