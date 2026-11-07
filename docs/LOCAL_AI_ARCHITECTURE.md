# Local AI Analyst architecture

**Current repair:** [LOCAL_AI_RELIABILITY.md](LOCAL_AI_RELIABILITY.md) documents
the strict action variants, one bounded missing-operation repair, source-record
operation, follow-up ordering and permanent launcher. Descriptions below of the
original no-retry planner are historical for the initial integration; network
failures and invalid/unsafe requests still do not retry automatically.

This upgrade starts at `e3874eeef01275700852bef683eb06ccae056a74` on
`codex/unified-insightpilot`; implementation is on `codex/insightpilot-local-ai`.
The existing Streamlit application, workspace catalog, Engine, Metric compiler,
SQL Lab, Plotly charts and report blocks remain the calculation system.

## Request and validation

`universal.ui.analyst → analyst_ui → analyst_session → analyst_planner →
local_ai → analyst_plan → analyst_execute → Engine/SQLLab`.

Standard mode uses finite deterministic patterns. Local mode sends the question,
field metadata and reviewed definitions to an explicitly selected local Ollama
model. The model proposes JSON intent. It never supplies authoritative numbers,
Python, SQL, tool calls, URLs or chart code. Answers and charts are assembled from
executed results. A valid plan can still misunderstand a question; users should
inspect the displayed measure definitions, filters and evidence.

The JSON Schema is `analyst_plan.PLAN_SCHEMA`. Its root contains `version: 1`,
`action: analyze|clarify|refuse` and the exact active `table`. Analysis arguments:

| Property | Supported scope |
| --- | --- |
| operation | aggregate, schema, missing, summary, distribution, group_distribution, scatter, trend, compare_periods, outliers |
| measures | Up to 4: records, nonmissing count, distinct, sum, avg, median, min, max, quantile, reviewed saved metric, selected-record share, explicit conditional rate |
| group_by | Up to 2 actual categorical fields, including approved dimension fields |
| filters | Up to 8 bound conditions: eq/ne, numeric gt/gte/lt/lte, missing/nonmissing, reviewed date from/through |
| fields | Up to 40 actual columns for metadata/statistical operations |
| date_basis / grain | Must agree with the shared reviewed basis and grain |
| periods | Exactly four explicit date boundaries for period comparison |
| sort_by / descending / limit | Existing result measure or records; 1–100 groups. Default comparisons use descending source-row count; explicit metric rankings and inherited order take precedence. |
| chart / bins | Validated auto/table/bar/line/histogram/box/scatter/pie; 5–80 bins |
| relationship | Exact existing approved relationship, revalidated against current versions |
| inherit | Inherit prior validated intent; never grants execution or approval rights |

Unknown properties, tables, columns, operations, duplicate JSON keys, invalid
types, nonfinite values, oversized responses, recursive/malformed JSON, code and
unused contradictory arguments are rejected. Clarifications/refusals cannot
include executable analysis arguments. Numerical fields require reviewed roles.
The model cannot approve semantics, rates or joins. New rates display the
proposed numerator, eligible denominator and missing-value policy and require
explicit UI approval bound to the current context. Saved metric definitions are
authoritative. Numerators intersect the eligible denominator; zero denominators
produce undefined values. A share means selected source records, not population
risk or an inferred business conversion.

Trusted finite templates compile metrics and quote known identifiers. Filter
values are parameters. Queries pass through SQL Lab's parsed SELECT/CTE and
function restrictions. Writes, external file readers, arbitrary joins, extensions,
network access and model SQL are unavailable. Only approved direct joins with a
unique right key are supported. Unmatched facts remain visible; version changes
invalidate join approval. DuckDB uses the shared limits in [LARGE_DATA.md](LARGE_DATA.md):
Engine deadline 20 seconds and SQL Lab default deadline 10 seconds, growing from 2 million rows. Statistical
results use the filtered scope. Sample skewness is descriptive, not causal proof.

## Ollama protocol and resources

The adapter uses official REST endpoints `/api/tags`, `/api/show` and `/api/chat`
through the existing `httpx` dependency. `jsonschema` is a direct dependency.
There is no model SDK, subprocess, installer or pull endpoint in the adapter.

Default: `http://127.0.0.1:11434`, `qwen3.5:4b`, temperature 0, seed 42, 8,192
context tokens, 1,024 output tokens, nonstreaming structured JSON output,
two-minute idle keep-alive and 60-second inference read timeout. The UI offers
4,096/8,192 context and 15/30/60/90/120-second waits. Configuration validation
allows at most 8,192 context and 1,536 output tokens. HTTP connect/health waits
are two seconds. There are no automatic retries or inference on ordinary reruns.
The spinner reports waiting/validation/calculation. The selected installed model
must advertise text completion. Thinking is disabled only when advertised.
Prompt truncation and context shifting are disabled rather than silently dropping
question/schema text. An old incompatible Ollama server fails explicitly.

Prompts contain at most 60 selected field descriptions, 12 metric definitions,
four approved relationship descriptions, one prior canonical plan and a
2,000-character question. Metadata payload is capped at 20 KB, full messages at
28 KB, HTTP response at 256 KB and plan JSON at 16 KB. No source rows, sampled
category values or local paths are sent. Prior question filters can contain
explicit user-provided literals. Field names and definitions can themselves be
sensitive; review the selected field scope. Full-table calculations stay local.

`INSIGHTPILOT_OLLAMA_URL` and `INSIGHTPILOT_OLLAMA_MODEL` set initial UI defaults.
Only literal loopback endpoints are permitted: `127.0.0.1`, `localhost` normalized
to `127.0.0.1`, and `[::1]`, with an optional port. No remote host, credentials,
URL path/query/fragment, redirect, proxy or DNS-based destination is accepted.
HTTPS retains certificate verification. Tags and metadata indicating remote
inference, cloud names, and remote markers on completed responses are rejected.
The local daemon remains a trust boundary: run a trusted Ollama installation,
disable its cloud features, and use local weights. The application cannot inspect
an independently configured daemon's network traffic or prove its honesty.

Official API behavior was checked against Ollama's `api/types.go` and
`envconfig/config.go` on 2026-11-04 (including `format`, `truncate`, `shift`, model
capabilities, and `OLLAMA_NO_CLOUD`). References:
[API](https://docs.ollama.com/api),
[structured outputs](https://docs.ollama.com/capabilities/structured-outputs),
[source](https://github.com/ollama/ollama/blob/main/api/types.go),
[cloud configuration](https://github.com/ollama/ollama/blob/main/envconfig/config.go).
These are compatibility references, not evidence of actual model execution here.

## Context, saving and charts

Conversation state stays in the browser's Streamlit session, bounded to six turns.
Its signature binds the workspace path, table/version/source hash, roles, grain,
semantics, selected metric and saved definitions, shared filters/date/grain, and
related versions. Workspace/table/version/filter/definition/model/mode changes
reset incompatible context. Friendly name changes do not. Context is checked
again after inference before execution. Identical successful requests in unchanged
context reuse results; no additional model call occurs.

Follow-ups inherit validated intent and question filters. Changing the measure
after top-N grouping preserves those exact group tuples and their contributing
rows, rather than selecting a new top-N population. At most 100 tuples are bound
through safe parameters. The previous result's records and group labels are not
sent to the planner. Every follow-up still passes live validation.

No prompt or chat history is automatically logged to disk. Explicitly saving an
analysis writes plan, definitions, actual results, all charts, execution evidence,
version-bound manifest and cohort into the existing local Reports block system.
Question text is excluded unless the user checks its save option. Saved evidence
may contain source values; treat exports as data. Replay executes the saved
version and scope without a model; it does not change the active version.

Each measure uses its own labeled axis/unit. Pie requires complete small
count/share composition; truncated top-N is not rendered as 100%. Histograms,
quantiles and scatterplots use calculated data. Single-period time results are
not drawn as a trend. Rows, finite counts, denominators, grouping, date basis,
filters, caveats, query and provenance remain inspectable/exportable.

## Names, imports and fallback

`workspace_labels` adds atomic `.insightpilot-workspace.json` metadata. Stable
identity remains tied to the existing directory. Renaming only changes metadata,
never moves/merges directories or updates sources, catalog versions or saved
objects. New workspaces use separate UUID directories and user-entered names.
Legacy workspaces get a stable `Untitled workspace · …` fallback; advanced details
retain the technical path. Names are normalized, length/control validated and
case-insensitive duplicates are rejected. Invalid existing metadata is preserved
and reported. Descriptions render as text, not executable Markdown.

Dataset selectors retain stable internal table keys but show original filename,
row count and import occurrence. Repeated imports are retained separately.
CSV preview reads a bounded UTF-8 prefix with the explicitly chosen separator.
A one-column parse with a consistently plausible alternative separator shows a
warning and preview before import. Correction is manual; genuine quoted
one-column data remains valid. Imports retain original bytes and selected
delimiter; mismatches remain in quality warnings. No silent redetection occurs.

Standard analysis, SQL Lab, Explore and Reports work without Ollama. Connection,
empty/missing/incompatible model, timeout, malformed output, invalid plan and
unsupported intent have explicit messages. The legacy external planner remains
separately opt-in, with its existing consent and three-operation limit; it is
never a fallback for Local AI. Prompt-injection guards are defense in depth;
schema validation, bound parameters and read-only execution are the security
boundary, not a claim that a language model cannot be manipulated.
