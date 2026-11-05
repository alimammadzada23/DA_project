# Complete grouped results without an invented top-N

This focused repair uses `codex/insightpilot-local-ai` at parent `d9a0717`.
The user reported two remaining real-Qwen failures: `groups` and `compound`.
Both questions requested grouped calculations without a limit, but the model
inserted `limit: 10`. The synthetic fixture has twelve job categories, so two
valid groups were omitted. This was an interpretation/execution-scope defect;
the individual DuckDB counts and averages were not the reported problem.

## Result-scope contract

A new grouped aggregate with no explicit top-N/limit now requires a **complete
grouped result**. The application reconciles an invented model limit to
`limit: null`, reports that correction, and validates the complete-result policy
before execution. A missing limit on a grouped aggregate also defaults to null.
The generic prompt and [current schema](local-ai-plan-schema.json) describe this
representation. No acceptance case IDs or particular field names are used in
the implementation.

Explicit integer limits remain exact: top five, top three and ten smallest
continue to use 5, 3 and 10. A follow-up referring to a previous selected result
retains its selection size and membership. Absence of a new limit in a sorting,
chart or measure follow-up does not expand a previous top-N selection.

The existing **100-group safety bound remains**. Complete-result execution
requests one overflow row through the existing read-only SQL Lab path. At most
100 groups can be returned as a complete answer; if another group exists, the
application returns a clear error and **no partial table, chart or saved answer**.
It does not call the first 100 groups complete or replace the request with a
top-100 calculation. An explicit top-100 request can still return its requested
subset. Time/memory, SQL, relationship and follow-up cohort guards are unchanged.

Null limits are accepted only for grouped aggregates. Scalar, source-row,
distribution and other operations retain bounded integer limits. Pie charts
still require complete composition and at most six groups. Numeric role and
row-grain review, new-rate approval, shared filters, DuckDB numerical authority,
read-only access and local-only Ollama routing remain enforced.

Successful results identify complete grouping in evidence metadata and explain
that all groups are included. Saved canonical plans retain null and replay the
same complete result. Previously saved plans with integer limits keep their
historical behavior; no workspace migration, dataset rewrite, rebuild or model
change is performed.

## Verification

[Validation receipt](local-ai-complete-groups-validation.json) ·
[Contract and prompt hashes](local-ai-complete-groups-contract.json) ·
[Security review](local-ai-complete-groups-security.json)

Regression tests reconstruct the reported unwanted limit around explicit HTTP
mock plans for both questions. They compare all twelve categories, every count
and average, and chart values against the original independent DuckDB queries.
They also cover omitted/null/other unrequested limits, 17 and 100 groups including
a missing category, the 101-group refusal, explicit limits, follow-up selection
preservation, pie safeguards, other-operation bounds and old/new saved replay.

The existing mocked acceptance runner exercises all sixteen cases with ordinary
plans and with reconstructed reported failures, diagnostics enabled and disabled.
The original sixteen questions, fixture, reference SQL and comparison rules are
unchanged. Existing benchmark references remain separate from these regressions.
The two older Standard ranking tests now expect null instead of the old default
15 when no N was requested; their expected numerical results are unchanged.

**These are deterministic and mocked results, not real Qwen acceptance.** The
latest user report still contains the two real failures. No new model score is
claimed for this repair.

## Repeat real acceptance on the existing Mac

This revision is committed locally only. After a separately authorized push or
other chosen transfer makes it available on the Mac, keep the existing clone,
environment, Ollama and `qwen3.5:4b`, then run:

```bash
cd ~/DA_project
./"Check Local AI.command" --diagnostics
```

Keep the new `report.json` and `rejected-plans.jsonl` from
`outputs/local-ai-acceptance/<run>/`. Diagnostics remain explicitly enabled,
restricted to the byte-verified synthetic fixture and redacted before storage.
Existing user workspaces are never selected or modified by this check.

Verify `groups` and `compound` each contain all twelve job categories, the final
plan has `limit: null`, values/charts reconcile independently, and any original
model `limit: 10` appears separately from application corrections. Also verify
top-five, top-three, sorting/chart follow-ups and ten-smallest retain their exact
scope. Real-Qwen success requires the Mac rerun; no failure is relabeled or hidden.
