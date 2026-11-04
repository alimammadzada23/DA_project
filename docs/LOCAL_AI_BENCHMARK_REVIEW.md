# Investigation of the four Standard benchmark mismatches

Starting point: `75fe6d5a6c1ccb11af01deb26e5f36f829f12759`,
`codex/insightpilot-local-ai`. The original four mismatches were reproduced
before editing the planner. They concern **Standard mode**, not measured Qwen
behavior. No real model inference occurred in this review.

## Classification

None of the four cases showed an arithmetic error. Three are valid requests
outside the finite Standard interpreter; one exposed inconsistent default
ordering. **None is an expected benchmark refusal**: all four reference cases
still require executed answers. The separate seven expected clarification/refusal
cases remain distinct and pass.

| Case / request | Independent check | Finding and action |
| --- | --- | --- |
| `s18`: share of all selected rows with result `yes` | 12 / 22 = 0.5454545454545454 | Standard does not parse this conditional-rate wording. It produced a clarification and no numeric result. Retained as a capability failure; clarified the message to identify the parser limitation. |
| `s19`: exclude `unknown`, then share `yes` among nonmissing result codes | 12 / 21 = 0.5714285714285714 | Same Standard parsing limit, with a distinct eligible denominator. Retained as a capability failure. No guessed denominator or automatic approval added. |
| `s22`: compare job-count contributions, January versus March 2025 | January 9, March 6, total change −3; all four group contributions reconcile | Standard does not parse the requested natural-language date ranges. It produced a clarification, not a period calculation. Retained as a capability failure; use the existing typed period plan/Advanced path. |
| `t06`: compare mean and median fare by payment | Every group's count, mean and median matches Pandas, including missing payment | Ordering defect: Standard imposed a mean ranking even though no ranking was requested, whereas the plan validator's existing default is descending source-row count. Fixed the planner's default; kept explicit metric rankings and inherited order. |

The correct fare calculations before and after the fix are:

| Payment | Source rows | Mean fare, USD | Median fare, USD |
| --- | ---: | ---: | ---: |
| credit card | 4,577 | 13.694749836137207 | 9.5 |
| cash | 1,812 | 11.592991169977925 | 8.5 |
| missing | 44 | 11.988636363636363 | 8.5 |

The old row order was credit card, missing, cash; the canonical default is credit
card, cash, missing. No numbers, categories or missing rows changed. Explicit
highest/lowest/top requests continue to sort by the requested metric. Ordinary
comparisons use descending source-row count with the existing stable group tie
break. This aligns Standard and validated-plan defaults rather than weakening the
benchmark's order-sensitive comparison.

Independent checks used Pandas directly on the unchanged public trip CSV and
the synthetic input retained by the reproduced benchmark. They did not use the
production SQL/Metric compiler or the benchmark comparison function. Conditional
rates and period contributions also reconcile through the existing execution
path when supplied with the manually authored validated plans; that is mock
integration evidence, not evidence of Qwen interpreting these questions.

## Fixes and regression coverage

`analyst_planner.py` now uses the validator's canonical count order when the
question does not request a ranking. Requested metric ranking and prior validated
follow-up order take precedence. The conditional-rate clarification now explicitly
states that Standard does not parse the wording, rather than suggesting an
already explicit numerator/denominator is missing.

New order/follow-up regressions exposed an additional parser defect: `top two`
returned a clarification although the existing UI workflow used that wording.
The finite parser now recognizes `two`. A previously weak UI assertion was
strengthened to require an executed two-row result. It does not merely check that
a second conversation entry appeared.

Four new regression cases independently check default comparison order, numeric
means/medians, explicit highest/lowest/top-three ordering, and inherited ordering
after `top two`. All four failed before the fixes and pass afterward. The focused
Analyst/session/Streamlit suite passes **59 tests**. The full-suite outcome is
recorded in [the review receipt](local-ai-benchmark-review.json).

## Results without changed expectations

| Check | Before | After |
| --- | ---: | ---: |
| Standard strict reconciliation | 47/51 | **48/51** |
| Remaining Standard mismatches | s18, s19, s22, t06 | **s18, s19, s22** |
| Mocked integration reconciliation | 51/51 in original receipt | **51/51 rerun** |
| Expected clarification/refusal cases | 7/7 | **7/7** |

The three remaining failures continue to exit the Standard benchmark with status
1. They have not been reclassified as successful answers. The integration path
still requires explicit approval of the fixed rate definitions. No security,
semantic-review or rate-approval restriction was weakened to increase the score.

The original receipt remains [unchanged](local-ai-standard-benchmark.json).
The full new Standard result is [recorded separately](local-ai-standard-review.json).
Both benchmark source files are byte-identical to the starting commit:
`scripts/local_ai_benchmark_cases.py` and `scripts/benchmark_local_ai.py`.
All 51 question texts, expected statuses and numerical reference tables are
identical across the before/after Standard and new integration runs. Their shared
SHA-256 is
`a71bcefb84f24aea3d6d951d3af68265af02e99b2c7263d908b1369e3c05b1b7`.
No oracle, tolerance, row comparison or expected status was relaxed.

Reproduce with the existing environment and pinned public inputs:

```bash
uv run pytest -q --junitxml=outputs/local-ai-review-tests.xml
uv run python scripts/benchmark_local_ai.py --mode standard --approve-benchmark-definitions --output outputs/local-ai-review-standard-after.json
uv run python scripts/benchmark_local_ai.py --mode integration --approve-benchmark-definitions --output outputs/local-ai-review-integration-after.json
```

The first benchmark exits 1 for the three disclosed capability gaps. The second
uses manually supplied plans through the HTTP mock and exits 0. Real Qwen quality
and Apple Silicon performance remain unmeasured. This review authorizes no push,
merge or deployment; changes remain on the local feature branch.
