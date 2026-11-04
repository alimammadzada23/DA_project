# Local AI upgrade acceptance

**Follow-up:** [The benchmark review](LOCAL_AI_BENCHMARK_REVIEW.md) fixes default
comparison ordering and the `top two` follow-up. Standard now reconciles 48/51,
with three disclosed parser limitations. The original acceptance results below
describe `75fe6d5`; the review receipt records the new verification results.

Engineering implementation is complete on **`codex/insightpilot-local-ai`**, based
on the published unified baseline
`e3874eeef01275700852bef683eb06ccae056a74`. The new branch is for a local commit
only. No push, merge, pull request or deployment is part of this upgrade.

**Actual Qwen execution and native Apple Silicon acceptance remain unverified.**
Ollama is absent/unreachable in this Linux executor. No model or daemon was
installed, started or downloaded. All model-assisted engineering/browser evidence
uses explicit mocks/test doubles; numerical execution and browser downloads are
real. [Actual-model availability receipt](local-ai-actual-model.json).

## Behavior before and after

| Area | Unified baseline | This upgrade |
| --- | --- | --- |
| Analyst planning | Finite summary/group/trend patterns; separate opt-in external planner | Expanded Standard mode plus optional installed local Ollama model producing validated JSON analytical intent |
| Compound question | Original job-category/count/average-age wording rejected | Count and reviewed mean execute together with distinct units, supporting rows and separate charts |
| Analysis scope | Selected metric, shared filters and three planner operations | Explicit question measures/filters within shared scope; schema/missingness, numeric aggregates, distributions, skewness, outliers, grouped quantiles, scatter, rates and reviewed time/period plans |
| Follow-ups | No conversational analytical state | Six-turn session memory; top-five cohorts preserved when measures change; chart-only follow-up; context changes isolate workspaces and datasets |
| Evidence and saving | Existing engine/SQL/report infrastructure | Current plan/definitions/filters/provenance/SQL, calculated tables/charts, downloads, explicit save to Reports and retained-version replay without a model |
| Workspaces | Technical directory paths, unnamed creation | Persistent friendly names/descriptions, named creation/rename, advanced identity/path, duplicate-name validation; no directory or data movement |
| Repeated imports | Internal table keys | Filename, row count and import occurrence while retaining separate stable internal keys |
| CSV separators | Preview inside submit form | Immediate bounded preview, plausible delimiter mismatch warning and manual correction before import; original bytes and legitimate one-column imports preserved |

The original compound question was independently verified on explicitly synthetic
data: the leading group has **7 source records**, with **6 valid age values** and
a mean of **22.5 years**. This is software acceptance evidence, not a business
finding. Browser-heldout means on 344 public penguin measurements reconcile with
Pandas. All charts use executed data and reviewed units. Human-facing result
headers identify measures and eligible inputs; raw aliases remain in technical
evidence for reproducibility.

The application remains one Streamlit entry (`app.py`) and one workspace catalog.
Data Hub, Quality/Preparation, Overview/Explore, Advanced, guarded SQL Lab, saved
analyses, reports/exports, source/version preservation and existing backend/CLI
compatibility remain. Existing tests and assertions were retained. A narrow legacy
undefined-variable rate guard and a real histogram roundoff error were fixed with
regression coverage; this is not a replacement analytical engine.

## Verification

| Check | Observed result |
| --- | --- |
| Clean baseline suite | 294 passed, 0 failed; 73.01 s |
| Upgraded full regression suite | 412 passed, 0 failed, 0 skipped; detailed receipt below |
| HTTP-mock analytical integration benchmark | 51/51 reconciled; 44/44 executable field/operation selections, 7/7 expected clarification/refusal, 8/8 prompt-holdout cases |
| No-model Standard benchmark | 47/51 reconciled; three safe unsupported-scope clarifications and one default-order mismatch; all failures disclosed |
| Actual Qwen benchmark | 0 cases executed; unavailable service, quality metrics null |
| Real Chromium workflows | Passed with explicitly labeled local HTTP test double, real engine and evidence downloads; no JavaScript errors or browser external requests observed |
| Workspace preservation | Rename retains directory, stable identity, database bytes/versions/saved objects; named creation, reload persistence and duplicate names checked |
| Local security failures | Remote/cloud/credential URLs, redirects, tool/code/SQL output, unknown fields/keys/types, invalid JSON, timeouts, stale context and unauthorized definitions rejected |
| Packaging / secret review | Separate reproducible receipts below; raw data, databases, credentials and generated secrets excluded |

The final regression command is
`uv run pytest -q --junitxml=outputs/local-ai-final-tests.xml`.
`local-ai-validation.json` records the exact observed runtime and counts; the
unmodified JUnit/log stay in ignored `outputs`. New tests cover the adapter,
runtime semantic validation, independent results/denominators, source-scope
intersection, approved joins, follow-up cohorts, concurrent context changes,
deduplicated inference, no-model fallback, saved replay, names and delimiter
feedback. Security tests are mocks; they do not establish real model compliance.

Chromium checks include the original compound request, two calculated charts,
Local mode discovery, follow-up chains, repeated-click inference deduplication,
report saving, shared-filter invalidation, cross-schema/workspace isolation,
rename persistence, invalid model JSON, stopped local service with Standard/SQL
fallback, separator warning before import, two retained imports with clear
context, new workspace/reload, keyboard focus and a 768-pixel no-overflow check.
This is a focused browser acceptance check, not a comprehensive accessibility or
all-device certification. [Browser receipt](local-ai-browser.json).

See [benchmark report](LOCAL_AI_BENCHMARK.md) for every failed case, independent
reference method, metric denominators, prompt-holdout limits and measured client
latencies. Integration harness median was 0.2220 seconds per question, peak client
RSS 165.3 MiB. Neither number measures a model or a Mac. No cold-start, inference
speed or model-memory promise is made for 16 GB Apple Silicon.

## Visual evidence

These are unmodified captures of the running app, using only synthetic/public
fixtures. Local screenshots explicitly show `test-double:latest`; **they do not
show Qwen execution**. No fabricated or generated screenshots were used.

- [Before: original Analyst rejection and technical workspace path](screenshots/local-ai/before-analyst-and-workspace.png)
- [After: compound question and friendly workspace](screenshots/local-ai/standard-compound.png)
- [Calculated counts, means and source/eligible counts](screenshots/local-ai/standard-compound-result.png)
- [Separate average-age chart and units](screenshots/local-ai/standard-compound-mean-chart.png)
- [Local follow-up result preserving five groups](screenshots/local-ai/local-followup-result.png)
- [Follow-up query, cohort and downloadable evidence](screenshots/local-ai/local-followup-evidence.png)
- [Public heldout result and chart](screenshots/local-ai/heldout-result.png)
- [Local connection status and persistent renamed workspace](screenshots/local-ai/friendly-workspace.png)
- [Unavailable local service](screenshots/local-ai/local-unavailable.png)
- [Explicit delimiter/import context](screenshots/local-ai/delimiter-and-import-context.png)
- [768-pixel layout](screenshots/local-ai/tablet.png)

## Code and security boundaries

| Modules | Responsibility |
| --- | --- |
| `local_ai`, `analyst_plan` | Official REST adapter; bounded local-only configuration, JSON Schema and live semantic validation |
| `analyst_planner`, `analyst_execute` | Generic bounded prompt, finite Standard interpreter, trusted deterministic calculation/chart/evidence templates |
| `analyst_session`, `analyst_ui` | Signature-bound session state, follow-ups, approval/save/replay, statuses and existing-page UI |
| `workspace_labels`, `ui_workspaces`, `import_preview` | Additive persistent names, stable selector values and bounded delimiter feedback |
| Existing `engine`, `sql_lab`, `assistant`, `store`, `ui`, `ui_data` | Small integration/filter/statistical fixes, report chart bundles and import warnings |
| `tests/test_*local_ai*`, `test_analyst_plans`, `test_workspace_names_and_preview` | Regression, mock integration, analytical and safety contracts |
| `scripts/*local_ai*` | Reproducible 51-question benchmark and actual Chromium workflows, explicitly separated from actual model evidence |

Gitleaks scans the intended repository snapshot and Git history with redacted
reports. Dataset-path review permits only the three unchanged explicitly
synthetic CSV fixtures from the baseline. New tracked data consists of
synthetic/public aggregate receipts and screenshots, not private datasets.
Environment files, model weights, workspace databases, raw source datasets and
generated outputs stay excluded. No credential contents were inspected or
printed. [Security receipt](local-ai-security-review.json).

Loopback-only HTTP, no redirects/cloud routing, parameterized queries, strict
operation/field/type validation and read-only SQL restrictions form the security
boundary. Text guards provide additional refusals, not a proof that a model
cannot be prompt-injected. A trusted local daemon is necessary; disable its cloud
features. Question/schema/metric metadata can be sensitive even without rows.
Conversation memory is session-only; explicit saved evidence can contain source
values and should be handled as data. The separate external planner still needs
its original explicit consent and is never a Local fallback.

## Limits and personal acceptance still required

Local inference is integrated and mock-tested; Qwen's real interpretation quality
is unknown. A schema-valid allowed plan can misunderstand a question. Review
measure definitions, eligible populations, filters and evidence, and reconcile
important results independently. Finite supported operations do not provide
arbitrary chat, predictions, causal proof, unrestricted SQL/Python or invented
business definitions. One shared reviewed date basis/grain and conservative
unique-key approved joins remain deliberate limits. Standard remains a finite
parser with disclosed unsupported wording/order differences.

On your Mac, install Ollama manually, disable cloud features, explicitly pull
`qwen3.5:4b`, load this **new local branch's source**, install dependencies and run:

```bash
ollama pull qwen3.5:4b
ollama list
uv python install 3.12
uv sync --locked --extra test
uv run streamlit run app.py --server.address 127.0.0.1
```

Open http://localhost:8501, choose **AI Analyst → Local AI Analyst**, refresh and
select the installed model. The published unified baseline alone does not contain
this upgrade. Full transfer/cloud-disable/installation/troubleshooting and real
benchmark commands are in [LOCAL_AI_MAC_GUIDE.md](LOCAL_AI_MAC_GUIDE.md).

Personally check count/mean/filter/distribution/rate answers against your
reference, top-five follow-ups, dataset isolation, workspace rename/reload and
saved replay. Stop Ollama and confirm Standard/SQL still work. Run the real-model
benchmark and record exact model/Ollama version, quantization, cold/warm latency
and Activity Monitor memory before accepting Apple Silicon performance.
