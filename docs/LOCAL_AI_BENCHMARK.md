# Local AI question evaluation

**Follow-up review:** Standard now reconciles **48/51** after correcting its
default comparison ordering. Three unsupported Standard questions remain failed;
expected results are unchanged. See [the four-case investigation](LOCAL_AI_BENCHMARK_REVIEW.md)
and [new receipt](local-ai-standard-review.json). The original evaluation below
and its receipts are retained as historical evidence for `75fe6d5`.

Run on Linux/Python 3.12.14 on 2026-11-04. **No Ollama installation, Qwen weights
or actual model inference was available.** Model accuracy, cold start, inference
speed, model RAM and Apple Silicon behavior are unmeasured. A genuine model run
is still required; [Mac commands](LOCAL_AI_MAC_GUIDE.md#4-run-the-real-model-benchmark).

## Questions, sources and independent references

The reproducible catalog contains **51 questions** with manually specified
reference intents. It covers counts, field metadata, missingness, distinct values,
means/medians/min/max, compound count/mean, ordering/top-N, numeric/category
filters, shares, eligible rates, unknown-code policies, distributions/skewness,
outliers, grouped quantiles, scatter, time/period comparison, four linked
follow-up requests, ambiguity, unsupported causes, mutation and prompt injection.
Clarification/refusal cases are explicit expectations, not omitted failures.

| Input | Rows | Provenance / role |
| --- | ---: | --- |
| Synthetic reviewed observations | 22 | Explicit synthetic fixture, known count/mean/rate/date answers; rare high-mean group tests cohort preservation |
| Recorded trips | 6,433 | Pinned seaborn-data extract attributed to NYC TLC; observed sample, not population coverage |
| Palmer penguin measurements | 344 | CC0, Horst/Hill/Gorman and Palmer Station LTER/Gorman et al.; held out from AI prompt examples |

`scripts/local_ai_benchmark_cases.py` verifies the original public-file SHA-256
before preparing isolated benchmark workspaces. Pandas reads missing markers and
writes derived benchmark CSVs with blank missing values; these are normalized
evaluation inputs, not byte-identical originals. Original public files are never
overwritten. Original hashes/attribution are retained separately from each
derived imported source hash. No private user dataset is used or committed.
Sampling, independence and business interpretations are not inferred from names.

The generic model prompt/schema were recorded before assembling this catalog in
`local-ai-prompt-boundary.json`. No prompt examples or runtime domain branches
contain these schemas or answers. Palmer data existed in historical app tests;
this is a **holdout from AI prompt examples**, not a newly discovered blind source
or independent real-model accuracy estimate. Generic Standard-parser fixes were
made after observing evaluation failures. Future real-model evaluation should
record the exact prompt, schema, application revision and model version.

Reference calculations use Pandas/NumPy on input frames, independently of the
production SQL/Metric compiler. Comparisons check numeric values, missing groups,
selected fields, grouping, ordering, explicit denominator components and follow-up
cohorts. Numerical tolerance is relative `1e-9`, absolute `1e-8`. Full per-case
expected and actual tables, validated plans, explanations, timing and evidence
identifiers are retained in the JSON receipts.

## Observed results

| Measure | HTTP mock integration | Standard deterministic parser | Actual Qwen |
| --- | ---: | ---: | --- |
| Reconciled answers or expected clarification/refusal | 51/51 | 47/51 | 0 cases run; unmeasured |
| Fields selected on executable cases | 44/44 | 41/44 | Unmeasured |
| Operations/measures selected on executable cases | 44/44 | 41/44 | Unmeasured |
| Expected clarification/refusal | 7/7 | 7/7 | Unmeasured |
| Prompt-holdout schema cases | 8/8 | 8/8 | Unmeasured |
| Failed reconciliation | 0/51 | 4/51 | Unmeasured |

The integration mock supplies **manually authored JSON plans** through the real
HTTP adapter; it exercises validation, calculations, evidence and session state.
It does not interpret language. Of 51 pipeline requests, 47 reach this mock and
four are handled by application safety guards. These scores are engineering
integration results and must not be described as Qwen understanding or accuracy.

Standard's four failures remain visible in the receipt:

- `s18`, `s19`: conditional rates safely ask for reviewed definitions/Local AI;
  the zero-model parser does not invent a denominator or substitute group shares.
- `s22`: a free-form period comparison safely asks for explicit Local AI/Advanced
  analysis; Standard does not discard period boundaries.
- `t06`: mean and median fare by payment match independently, including the
  missing-payment group. Default ordering differs: Standard orders by mean;
  reference orders by record count. The benchmark counts this as a failure rather
  than relaxing its comparison. All three group calculations reconcile.

Earlier failed checks exposed a distribution/grouping parser error and a real
floating-point histogram boundary error on the public distance values. The
production fixes preserve filtered scope and calculate/correct bin edges
consistently. Regression assertions include adjacent floating-point values;
the NumPy reference was not changed to accept the error. Browser testing also
found and fixed a stale visible workspace label after persisted rename.

## Performance scope and model metrics

Latest integration run: 11.130 seconds for the harness, median per-question
0.2220 seconds, maximum 0.3168 seconds; peak **client process** RSS 165.3 MiB.
Standard: 9.671 seconds, median 0.1885, maximum 0.2859; client peak 164.5 MiB.
These include a small local analytical workload, depend on Linux scheduling and
do not measure model inference, model RAM or speed on a Mac. Model memory remains
`null`. Real Ollama server load/evaluation timings are recorded only when returned
by a genuine model; no initialization number is synthesized from a mock.

Real mode preflights the installed model and writes
`local-ai-actual-model.json`: `actual_model_unavailable`, `cases_run: 0`, every
model-quality score `null`. Missing service means unavailable, not 0% accuracy.
When real inference is possible, scores contain numerator, denominator and
fraction. Model-only field/operation/clarification/refusal metrics exclude
application-guarded questions. End-to-end includes the full pipeline. Accepted
plans failing reference reconciliation count as incorrect plans, including
filter/definition/ordering mistakes. Zero eligible cases have a null fraction;
an application refusal is not credited as a model-generated refusal.

Receipts: [integration](local-ai-integration-benchmark.json),
[Standard](local-ai-standard-benchmark.json),
[real-model availability](local-ai-actual-model.json),
[browser](local-ai-browser.json). The browser uses a real Streamlit server and
system Chromium with an explicitly named `test-double:latest` HTTP server; it
executes real calculations and downloads, **not Qwen**.

Reproduction (after acquiring pinned public files as in the Mac guide):

```bash
uv run python scripts/benchmark_local_ai.py --mode integration --approve-benchmark-definitions --output outputs/local-ai-integration-benchmark.json
uv run python scripts/benchmark_local_ai.py --mode standard --approve-benchmark-definitions --output outputs/local-ai-standard-benchmark.json
uv run python scripts/benchmark_local_ai.py --mode ollama --model qwen3.5:4b --approve-benchmark-definitions --output outputs/local-ai-actual-model.json
uv sync --locked --extra test --extra browser
uv run python scripts/browser_local_ai.py
```

The benchmark approval flag is restricted to exact predeclared rate definitions;
ordinary UI rates still require explicit human approval. Commands never install,
start or pull a model. Standard exits 1 for its documented four mismatches; an
unavailable actual model exits 2. Do not suppress these exit codes in acceptance.
