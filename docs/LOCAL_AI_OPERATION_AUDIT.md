# Operation-aware Local AI contract audit

Baseline: `c2cfd89` on `codex/insightpilot-local-ai`. The user reported **14/16
with actual Qwen3.5 4B and Ollama 0.40.14 on a 16 GB MacBook Air**. Total count
and filtered source rows both carried `limit: null` and were rejected. This
revision is a local commit only; no push, merge, deployment or model change.

## Underlying defect

The code had one broad plan schema but separately maintained defaults, operation
rules, prompt instructions and execution caps. The complete-group repair made
null available in the schema while semantic validation still rejected it for
every other operation. Python `setdefault` did not resolve an explicit null.
Qwen could therefore produce schema-valid count/row plans that were guaranteed
to fail. Earlier operation/argument, chart, limit and follow-up failures exposed
other boundaries of the same design.

The repair introduces a single static registry in `analyst_contract.py` for all
eleven operations. Schema generation, defaults, prompt rules and execution bounds
read that registry. **Null or omission always means automatic operation policy,
never unbounded access.** Inheritance is resolved before operation defaults.

| Operation | Grouping / measures / fields | Automatic limit and result meaning |
| --- | --- | --- |
| Aggregate, no grouping | 1–4 explicit/inherited measures; no fields | One scalar result row |
| Grouped aggregate | 1–2 grouping fields, 1–4 measures | Complete groups within 100; overflow fails with no partial answer |
| Records | No grouping/measures; optional source fields | Explicitly labeled preview of at most 100 matching source rows |
| Schema / missing | No grouping/measures; optional fields | Full requested metadata/missingness calculation; no top-N parameter |
| Summary | One reviewed numeric field | Full-scope statistics; no top-N parameter |
| Distribution | One reviewed numeric field, validated bins | Full-scope histogram/statistics; no top-N parameter |
| Group distribution | One grouping and one reviewed numeric field | Complete groups within 100, or an explicitly requested supported top-N |
| Scatter | Two reviewed numeric fields | Clearly labeled stable-source-order preview, at most 2,000 rows |
| Outliers | One reviewed numeric field | Full-data IQR fences; clearly labeled flagged-row preview, at most 1,000 rows |
| Trend | One measure, shared reviewed calendar basis/grain | Complete chronological series within 1,000 periods; overflow fails |
| Period comparison | One grouping/measure and four validated dates | Complete additive contributions under the existing 10,000-intermediate-row bound; no top-N parameter |

Explicit supported limits remain exact. The original 100-group/cohort bound,
100-row source-preview bound, 2,000-point scatter bound, 1,000-outlier and
1,000-period bounds are not increased. A top-N subset is labeled separately from
a complete result. Preview overflow is flagged; a preview is never called the
complete dataset. Source-row inspection is not converted into a count.

## Pipeline audit and corrections

| Boundary | Finding | Correction |
| --- | --- | --- |
| `analyst_plan.py` | Universal operation properties admitted incompatible argument combinations | Closed, disjoint operation/inheritance variants enforce grouping, field/measure arity, chart/sort choices, relationships and period arguments |
| Limits/defaults | Explicit null differed from omission; defaults 15/100/1 and executor caps were separate | Shared automatic policy resolves both; count becomes 1 and source preview 100 without guessing business meaning |
| Filter arguments | Missing values were admitted for comparisons that require an operand | Closed operator-specific condition variants; missing tests accept no value or null, numeric comparisons require numbers, dates require ISO-shaped strings |
| `analyst_planner.py` | Null-only-for-group instructions contradicted schema | Automatic-policy explanation and operation rules generated from the same registry |
| `analyst_constraints.py` | Known explicit constraints could differ from model defaults | Existing written/numeric limits, direction, grouped statistics and previous selections retained; explicit charts and literal numeric/category filters also checked; invented filters cannot narrow an unfiltered count |
| Rate eligibility | Outcome/denominator wording could be mistaken for filters | Only explicit eligibility filters are extracted; numerator/eligible-denominator approval remains separate |
| `analyst_session.py` | Many safe operation-shape errors had no bounded correction path | Precise static operation feedback shares the same two-total-attempt and remaining-time budget as missing-operation/scope repair |
| `local_ai.py` | Wire validity and final plan validity were conflated | Separate wire-schema, typed-admission, canonical-plan and execution stages; raw invalid operation output is not credited as valid model output after an application correction |
| `analyst_execute.py` | Some charts/sorts/limits were accepted but not used; group distributions/long trends could be partial | Incompatible combinations reject before execution; supported ordering/preview limits are honored; complete overflows return no result |
| Scatter / outliers | Fixed preview sizes were separate from plan limits; scatter's inner LIMIT hid overflow | Canonical bounded previews, one overflow row and explicit labels; IQR thresholds still use full data |
| Periods | Invalid calendar windows reached execution | Calendar validity, equal inclusive lengths and nonoverlap checked before calculation |
| `accept_local_ai.py` | One run and no contract fingerprint made recurrent/stale failures harder to compare | Fresh multi-run protocol, schema/prompt hashes, commit/dirty marker, separate application corrections and original failure retention |

Common version-one context metadata remains compatible with saved canonical
plans; only applicable operations use its date/grain/bin settings. Live checks
still decide actual field identity, reviewed roles/units, date purpose, metric
meaning, relationship cardinality and rate approval. Static JSON shape does not
establish those facts. Explicit unsupported chart/order/operation combinations
must clarify or fail rather than become a different calculation.

## Admission, validation and bounded repair

[Operation schema](local-ai-plan-schema.json) ·
[Typed admission envelope](local-ai-envelope-schema.json) ·
[Contract/prompt hashes](local-ai-operation-contract.json)

The stronger operation schema is sent to Ollama and checked on the reconciled,
canonical candidate. A separate closed typed envelope admits only allowlisted
intent data for repair. It never authorizes execution. It still rejects unknown
keys/operations, code/SQL/tool arguments, malformed/oversized/duplicate-key JSON,
nonfinite values and invalid measure/condition argument shapes.

This distinction allows a reported `group_distribution`/median mistake to be
corrected from explicit requested intent before the full operation check, while
retaining the original wire failure in the report. Missing root metadata may
receive a request to copy the known active table/version from context; it is not
silently filled or used to switch datasets. The supplied count/row shapes are
tested both with complete root envelopes and exactly as the reported excerpts.

At most one extra local-model request is permitted, across all repair reasons.
Feedback names allowed operation arguments, not raw responses, source rows or
calculated answers. Wrong field identities, missing business definitions,
unreviewed meanings, unsupported requests and approvals are not guessed. Every
candidate must pass full structure, live semantics, request consistency and
unchanged-context checks before the same read-only DuckDB execution path runs.
Failed repairs return no numerical result.

The existing numeric-role/row-grain review, new-rate approval, protected write
refusals, SQL AST scope, relationship validation, memory/time bounds, literal
loopback transport, redirect/cloud/tool rejection and no-external-fallback policy
remain in force. Existing saved plans are not rewritten. Replay adapts only old
inert display/default metadata to reproduce the same calculation, then performs
current full validation; it does not invent fields, measures or approvals.

## Verification and limits of the evidence

[Full validation receipt](local-ai-operation-validation.json) ·
[Real-model availability](local-ai-operation-availability.json) ·
[Security review](local-ai-operation-security.json)

The systematic tests cover every operation, allowed/incompatible chart and sort,
argument counts, fresh/inherited forms, filters, automatic/explicit limits,
historical failure shapes, malformed output, clarification/refusal, failed
repairs, complete overflow, labeled previews and saved replay. Existing
independent DuckDB/Pandas/NumPy expected results are retained. A changed test that
previously demanded blanket rejection of null now checks the correct bounded
operation semantics; no numerical expected answer was changed to hide a failure.

The mocked three-run test uses fresh workspaces/conversations and a synthetic
fixture with Bank Marketing's published dimensions/vocabulary. That tests the
reader and protocol, **not the actual UCI data or Qwen**. The original sixteen
synthetic questions/reference SQL/checker are unchanged. The public-data runner
independently checks the original top-five question against DuckDB over the
caller-supplied UCI data, including all counts, ordering and bar-chart values.

**Ollama is unavailable here. The real three-run command recorded zero model
cases. No new actual Qwen score, actual UCI-model run or Mac performance result
is claimed.** The latest supplied real result remains 14/16 at `c2cfd89`.
Finite explicit-language constraints do not prove arbitrary natural-language
interpretations. The required real-Mac evidence is the next acceptance gate.

## Mac verification: three complete passes plus public data

This revision must first reach the existing Mac clone through a separately
authorized push or chosen transfer. Do not test an older checkout and attribute
its result to this repair. Keep the existing environment, Ollama and
`qwen3.5:4b`; start the already installed Ollama app first. No installer, model
pull, alternate model or external AI service is used.

```bash
cd ~/DA_project
git log -1 --oneline
ollama --version
mkdir -p outputs
curl --fail --location --proto '=https' --tlsv1.2 \
  'https://archive.ics.uci.edu/static/public/222/bank+marketing.zip' \
  --output outputs/uci-bank-marketing.zip
./"Check Local AI.command" --runs 3 --diagnostics \
  --bank-data outputs/uci-bank-marketing.zip
```

The one-time public-data download is from [UCI Bank Marketing](https://archive.ics.uci.edu/dataset/222/bank+marketing).
The reader handles the nested official archive without extracting paths, or
accepts the existing classic `bank-full.csv`. It checks 45,211 rows, 17 columns
and the twelve published job categories, records the CSV SHA-256 and retains
only `job` in a new isolated acceptance workspace. These checks and the official
download establish reproducible caller provenance; no independent remote-byte
verification is claimed. Other attributes never enter the prompt or report.
No raw UCI data is committed or synthetic diagnostics enabled for that dataset.

For a longer explicit wait, use the existing environment directly:

```bash
.venv/bin/python scripts/accept_local_ai.py --runs 3 --diagnostics \
  --timeout 120 --bank-data outputs/uci-bank-marketing.zip
```

Read the new `outputs/local-ai-reliability/<run>/summary.json`, each
`pass-N/synthetic/report.json`, its redacted `rejected-plans.jsonl`, and
`pass-N/public-bank/report.json`. Reports include the contract fingerprint and
separate model attempts, wire validity, application corrections, execution,
numerical/chart reconciliation and application-guarded refusals.

Success requires **16/16 on all three consecutive complete real synthetic runs**
and the real public-bank top-five check on each pass. There are thirteen
model-planned synthetic cases and three deterministic application guards, with
the bank question as a separate check. Each pass starts fresh application
conversations/workspaces under the same deterministic model configuration; this
is not a statistical independence/accuracy claim. No failed case or failed pass
is overwritten, retried indefinitely or turned into a pass. Contract drift,
missing service and any failing pass prevent full acceptance. Mock clients can
never set `full_real_acceptance_passed` to true. Existing user workspaces and
saved analyses are not selected, renamed or migrated.
