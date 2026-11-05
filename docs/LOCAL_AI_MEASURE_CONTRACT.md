# Qwen acceptance failure: measure contract and safe diagnostics

This page records the `7099841` repair, which was subsequently published. The
user's later real Mac acceptance improved to **13/16**. See the current
[request-constraint and follow-up repair](LOCAL_AI_REQUEST_CONSTRAINTS.md) for
the remaining three interpretation failures and the next Mac rerun. The receipts
below describe the earlier repair's evidence and verification at that time.

This focused repair starts at `3be7f67` on `codex/insightpilot-local-ai`.
It changes the existing planner, validation and acceptance tools. The model,
analytics engine, application structure and deployment are unchanged. This
revision is committed locally; no push is authorized.

## What the evidence establishes

The user reported **2/16 on real Qwen3.5 4B on a MacBook Air**: eleven measure
argument errors, an incorrect missing-values operation, an ignored unavailable
field, and a file-creation request replaced by a count. Source-row display and
the deletion safeguard passed. The old report did **not** record rejected JSON.
The exact properties emitted by that Qwen run therefore remain unknown.

The old producer schema admitted every known measure property for every
aggregation, but the semantic validator admitted different keys for each one.
For example, this **constructed reproducer, not captured Qwen output**, passed
the old JSON Schema and failed with the reported semantic error:

```json
{"aggregation": "records", "column": "job"}
```

[Reproduction receipt](local-ai-measure-reproduction.json). This proves a contract
defect and reproduces its error path. It does not prove that Qwen emitted this
particular object or that all eleven cases had the same extra properties. The
new diagnostic rerun is needed to inspect actual responses safely.

## Exact measure arguments

Each aggregation now has a disjoint closed object schema. All listed keys are
required; every other key is prohibited, including irrelevant defaults or nulls.
The same variants are sent to Ollama and used by runtime validation.

| Aggregation | Required and permitted keys |
| --- | --- |
| `records`, `share` | `aggregation` |
| `count`, `distinct`, `sum`, `avg`, `median`, `min`, `max` | `aggregation`, `column` |
| `quantile` | `aggregation`, `column`, `quantile` (0–1) |
| `saved` | `aggregation`, `metric` |
| `rate` | `aggregation`, `numerator`, `denominator` |

Rate denominator variants are also exact: `selected` has only `kind`;
`nonmissing` has `kind` and `column`; `condition` has `kind` and `condition`.
Numeric roles, known fields, approved relationships, shared filters, metric
definitions, row grain and human approval of new rates remain independently
checked. Invalid measure arguments are rejected with a precise safe error.
They are never silently stripped or converted into a different measure.

[Current schema](local-ai-plan-schema.json) ·
[Current schema and prompt hashes](local-ai-measure-contract.json).

## Intent and side-effect safeguards

The prompt explains the exact argument variants, missingness profiles versus
filtered remaining-row counts, unavailable fields and unsupported writes.
Explicit missing-value exclusions must preserve a `not_missing` filter on the
named field. A requested remaining-row count must use an aggregate record count.
A wrong operation or missing exclusion cannot reach calculation. It may receive
one bounded correction request to the same model with constant validation
feedback, original context and the remaining time budget. Missing-operation
repair and missing-scope repair share a maximum of **two total model attempts**.
Further failure returns an error. No arbitrary retry or alternate model is used.

Explicit unavailable `field`/`column` names receive clarification before inference.
File creation, writing, overwriting, appending, modification, renaming, exporting
and deletion requests receive refusal before inference or calculation. Path-based
writes and passive file-modification wording are covered. Read-only charts about
file metadata remain allowed. The legacy external-provider entry point also
checks the write safeguard before contacting a provider.

These are bounded deterministic checks, not a claim to recognize every possible
natural-language paraphrase. Accepted plans still use only the existing read-only
operation allowlist; the model has no file, SQL, command or tool execution channel.

The acceptance suite still has the same sixteen questions, expected statuses and
independent DuckDB queries. Three cases now use application safeguards: unavailable
field, create-file refusal and deletion refusal. Their report entries say
`handling: application_guard` and `model_attempts: 0`; they provide no credit for
Qwen understanding. The other thirteen cases require model planning.

## Real-Qwen rerun on the existing Mac clone

This local revision must first be made available to the Mac through a separately
authorized push or another explicitly chosen transfer. A pull cannot retrieve an
unpublished local commit. Keep the same branch, environment, installed Ollama and
**`qwen3.5:4b`**. Do not reinstall or download a different model for this check.

With Ollama running, use Terminal:

```bash
cd ~/DA_project
./"Check Local AI.command" --diagnostics
```

Or run the equivalent existing-environment command:

```bash
.venv/bin/python scripts/accept_local_ai.py --diagnostics
```

The runner creates a fresh isolated 70-row synthetic fixture. Existing user
workspaces are never selected or modified. It writes:

```text
outputs/local-ai-acceptance/<run>/report.json
outputs/local-ai-acceptance/<run>/rejected-plans.jsonl
```

`report.json` keeps failures visible and separates response receipt, valid
structured output, live plan validation, execution, independent numerical
reconciliation, charts, repairs and application guards. A completed real response
that fails schema validation still counts as a received model response, never as
a successful answer. Missing Ollama produces zero cases, not a model score.

Diagnostics are **off by default** and available only in this explicit synthetic
runner. There is no general application or environment-variable logging switch.
The observer verifies the retained fixture's exact SHA-256, 1,694-byte size,
70 rows, three fields, single unchanged version and synthetic provenance before
capture. Merely labeling a private dataset synthetic cannot enable it.

The JSONL contains bounded projections of received JSON **before rejection**,
including known measure keys actually present and unexpected known arguments.
It also records attempt numbers and validation decisions. Unknown keys, unknown
strings, free-form messages and out-of-range numbers are redacted; invalid JSON
and oversized responses are omitted. It does not store raw responses, prompts,
source rows, headers, filesystem paths or credentials. The companion report uses
the same plan redaction and omits model free text on failed attempts. The JSONL is
created with mode `0600`; an existing diagnostic file is never overwritten.

Inspect the first failed case's `model_response` and rejection events. For example,
`unexpected_known_keys: ["column"]` for `records` would demonstrate that actual
response's mismatch; an empty list must not be treated as evidence of extra keys.
Record `ollama --version` and retain both sanitized files for review. They stay in
ignored `outputs/` and are not automatically uploaded or committed.

All relevant cases must pass on actual Qwen, with numerical values reconciled
against DuckDB, before calling this a successful real-model repair. After that,
repeat the original question against the user's reviewed dataset and reconcile
it independently in SQL Lab. A schema-valid plan can still misunderstand intent.

## Verification status

[Validation receipt](local-ai-measure-validation.json) distinguishes automated
mock/deterministic results from the user's real result. HTTP mocks exercise all
sixteen cases through the adapter, validator and independent DuckDB calculations;
they are not Qwen evidence. The existing 51-case HTTP-mock benchmark remains
51/51, and Standard remains 48/51 with the same documented unsupported conditional
rates (`s18`, `s19`) and period-comparison wording (`s22`). No expected answers were
changed to remove those failures.

[The fresh cloud availability probe](local-ai-measure-actual-model.json) found no
reachable Ollama service and executed **zero model cases**. No new real Qwen
score or native Mac result is claimed. The latest real-model evidence remains
the user's **2/16** until the Mac rerun supplies a new report and actual sanitized
plan shapes. [Security review](local-ai-measure-security.json).
