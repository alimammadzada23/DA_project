# Local AI reliability repair and persistent Mac startup

This page records the earlier missing-operation/launcher repair. The user's
subsequent real Mac test was **2/16**. See the newer
[measure-contract repair and diagnostic procedure](LOCAL_AI_MEASURE_CONTRACT.md)
for the current changes and remaining real-Qwen acceptance. The receipts linked
below are historical; current schema/prompt hashes are in
[local-ai-measure-contract.json](local-ai-measure-contract.json).

Baseline: clean `codex/insightpilot-local-ai` at
`b8d562ee7b1517ec98e34971f72e3244db7b0150`. The existing branch is used; `main`
and other branches are untouched. This repair is for a local commit only, with
no authorized push, merge or deployment.

## Diagnosis and repair

The reported **“An analytical operation is required.”** was reproduced through
the real Ollama HTTP adapter with an explicitly labeled test double returning
an otherwise well-formed count plan without `operation`. The old schema accepted
that plan, while the later semantic validator rejected it. This establishes the
contract mismatch and error path; the original Mac response body was not
available and no actual Qwen response was generated here.

`PLAN_SCHEMA` now contains three disjoint closed object variants. Analysis
requires `version`, `action`, `table` and a supported `operation`, including
follow-ups. Clarification/refusal requires a message and permits no executable
arguments. Unknown keys/types/operations remain invalid. The same schema is sent
to Ollama and checked at runtime. The old permissive root was removed rather than
depending only on prompt advice. [Current schema](local-ai-plan-schema.json) and
[contract/prompt hashes](local-ai-reliability-contract.json).

For a structurally otherwise valid missing-operation response, the session may
make **one** additional request to the same local model. It includes the original
question/context plus constant minimal validation feedback, not the invalid
response, source rows or calculated numbers. It uses the remaining planning wait
and rechecks context before repair and before execution. The corrected complete
plan must pass schema and live semantic validation. A second failure returns an
explicit error and no result. Invalid JSON, unknown operations/fields, tool calls,
remote routing and network failures do not trigger an unlimited retry or a
different planner. The adapter rechecks the selected installed model on each call.

Other defects or limitations found during the investigation were addressed:

- Incomplete aggregate plans no longer silently substitute the selected metric
  when measures are absent. Measures must be explicit or inherited from validated
  intent. Numeric roles and business metric/rate approval still apply.
- Follow-up templates now include their operation. Their expected numeric results
  are unchanged. Re-sorting a previously displayed cohort preserves those exact
  groups instead of selecting a different smallest/largest population.
- The generic model instructions explain aggregate counts, limits, sort direction,
  rows versus aggregates, chart-only follow-ups and ambiguity. They contain no
  runtime mappings for particular dataset/column names or numerical answers.
- Standard mode recognizes the exact reported five-category wording and does not
  turn “highest to lowest” into an ascending ranking. It also handles explicit
  `is at least` conditions. Unparsed condition wording asks for clarification;
  it does not quietly calculate an unfiltered substitute.
- A finite `records` operation returns bounded, filtered original source rows.
  It uses trusted read-only SQL templates and cannot invent an aggregate in place
  of a request to display records. Source cell text remains source text.
- Plotly 6 numeric arrays can remain base64 typed arrays after a JSON round trip.
  The chart export helper previously iterated `dtype`/`bdata` keys. Bounded numeric
  decoding now exports actual plotted values and preserves heatmap shapes.

DuckDB and the existing guarded SQL/Metric machinery still calculate all results.
No model SQL/Python is executed. Active workspace, version, source, roles, filters,
units and denominator definitions remain authoritative. No external fallback or
automatic source-data transmission was added.

## Question widget warning

The old pattern supplied both a default argument and an explicitly assigned
Session State value for `analyst-question`. That pattern reproduces the installed
Streamlit warning in isolation. The input now initializes its session key once
and supplies **no widget default value**. Warning suppression is not used.
Suggested questions, edits, new-conversation clearing, mode changes and follow-up
input are tested; the real browser/server logs contain no duplication warning.

## Mac launcher and storage

**Start InsightPilot.command** finds the existing repository relative to itself
and calls its `.venv/bin/python`. It checks installed dependencies, starts the
same app from the correct working directory, binds loopback, chooses a free port
without stopping another application, checks project-specific readiness and opens
the correct URL. A kernel-held lock reuses this project's running server. Clean
shutdown terminates only the process group created by that launcher.

Normal startup performs no clone, pull, Python installation, dependency sync,
model pull, workspace recreation or migration. The existing installed Ollama app
may be opened on macOS when its default local endpoint is offline, after setting
the GUI launch environment to disable cloud and bind loopback. Existing services
are not duplicated or stopped. An unavailable/invalid local model does not prevent
Standard analysis. Metadata availability is not presented as completed inference.

Persistent assets already live in the workspace database, retained sources and
friendly-name metadata. No new storage system was created. Ordinary UI shared
defaults may be saved on first use; the full post-use catalog, source/version
metadata, reviewed semantics, names and saved analysis blocks are verified intact
across stopping and restarting the process. Saved chart replay works with the
model service offline. Unsaved conversation remains deliberately temporary;
explicit Reports blocks/saved views preserve work for tomorrow.

See [everyday startup and safe updates](MAC_EVERYDAY_USE.md). Updates use the
existing `~/DA_project` clone and `git pull --ff-only origin
codex/insightpilot-local-ai` after checking branch/status and preserving edits.
Only genuine dependency changes require one explicit `uv sync --locked`.

## Verification and remaining acceptance

- Baseline: **416 passing tests**, 0 failures, before modifications.
- New regressions cover strict action variants, one bounded repair, no execution
  of invalid plans, time/context limits, exact descending five-group counts,
  charts, filtering, follow-up cohorts, widget state, launcher behavior and
  persistence. Calculated references use separately executed DuckDB queries.
  The final full counts are in [the validation receipt](local-ai-reliability-validation.json).
- All 51 existing benchmark questions, expected statuses and reference tables are
  unchanged. Three mock follow-up plan fixtures now explicitly carry `operation`
  to meet the stronger contract; no numerical oracle or tolerance changed.
  HTTP-mock integration still reconciles **51/51**; Standard still reconciles
  **48/51**, with its three disclosed conditional-rate/date-language capability
  gaps retained as failures.
- The new 16-case acceptance command passes in the explicit HTTP mock test. It
  checks each stage and figures against independent DuckDB. This verifies its
  engineering; it does not measure Qwen's understanding.
- Real Linux launcher/Streamlit/Chromium tests verify the original request, one
  repair, five exact counts **13, 11, 9, 8, 7**, calculated bar data and downloaded
  chart CSV, inference deduplication, top-three follow-up, occupied-port safety,
  duplicate launch reuse, clean shutdown/restart, persistent saved data and
  chart replay without the model. [Browser receipt](local-ai-reliability-browser.json).

Unmodified browser captures use synthetic data and show the explicit
`test-double:latest` model:
[question/status](screenshots/local-ai-reliability/question-and-repair-status.png),
[verified result/chart](screenshots/local-ai-reliability/verified-top-five.png),
[saved replay after restart](screenshots/local-ai-reliability/replayed-after-restart.png).
These screenshots are not real Qwen or native Finder evidence.

**Ollama is absent/unreachable in this Linux executor. No model was installed,
downloaded or executed. Native macOS Finder, `open` and `launchctl` were tested
through mocks, not on a Mac.**
[Actual-model availability](local-ai-reliability-actual-model.json) records zero
executed model cases. The user independently running a READY prompt on their Mac
establishes basic model availability, not this repaired structured-planning flow.

Double-click **Check Local AI.command** on the existing Mac installation after
the repair is published and pulled. It exercises the real installed Qwen through
the real planner on isolated synthetic data, including the original question,
counts/filtering/follow-ups/numeric summaries/clarification/refusal. Its report
records model attempts, valid output, plan validation, execution, numbers, charts
and repair use; application-guarded requests are not credited as model responses.
Unavailable service exits 2 with zero cases, and failed reconciliations exit 1.
The original request against the user's own reviewed dataset and native launcher
behavior still need personal Mac acceptance. A valid plan may misunderstand a
question; inspect definitions, filters and evidence and report the first failing
plan rather than claiming universal natural-language reliability.
