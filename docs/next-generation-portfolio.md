# Factual portfolio and interview notes

## CV description

**InsightPilot — local-first data analytics platform (Python, DuckDB, Streamlit,
Plotly):** Extended an existing procurement/complaint application with multi-format
and multi-table ingestion, uncertainty-aware profiling, reversible preparation,
explicit KPI definitions, cardinality-safe analytical joins, statistical
investigations and reproducible evidence reports. Validated authentic NYC flight
and Palmer penguin extracts against independent calculations and benchmarked
synthetic workloads through one million rows on Linux. Optional model planning
is consent-controlled and numerical findings are calculated locally.

Use the current validation document for exact test/benchmark counts. Do not claim
commercial deployment, users, revenue, implemented cost savings, stakeholder
adoption, native Mac acceptance or live-provider validation from this work.

## Interview talking points

1. **Why explicit semantics?** A numeric ID is not a measure; an amount is not
   automatically revenue. Explain role uncertainty, units, row grain and formulas.
2. **Why joins need receipts:** demonstrate duplicate/unmatched keys, row-count
   prediction and the unique-right-key restriction preventing fact multiplication.
3. **Reproducibility:** trace a KPI to source hash, immutable version, filters,
   transformation/error history and executed SQL; replay without replacing data.
4. **Analytical correctness:** show weighted denominators, unavailable zero rates,
   equal-window contributions and the rate/mix accounting identity. Explain why
   observed contributors do not establish causes.
5. **Statistics:** discuss Welch intervals/effect sizes, complete-pair OLS,
   independence, sparse categorical tests, selection bias and practical importance.
6. **Performance:** discuss SQL aggregates, source-order bounded views, pagination,
   full-data profiling and actual fresh-process timing/RSS, with hardware limits.
7. **Responsible AI:** finite schema-only planning, explicit consent, local
   calculations, rejected injection/invalid plans, zero-token deterministic mode;
   live acceptance remains distinct from mock protocol tests.
8. **Evidence honesty:** distinguish authentic package/publisher extracts,
   synthetic test cases and the blocked genuine CFPB source. Explain why missing
   data was not replaced with manufactured portfolio findings.

## Demonstration sequence

Run the pinned public-case script, open its flights workspace, review source/grain,
select mean recorded delay, compare origins, drill a bar to records, validate the
airline relationship and export the report. Discuss missing arrival outcomes and
route/season composition. Then open penguins and demonstrate why within-species
analysis differs from pooling species. Use synthetic files only for controlled
import/error/forecast demonstrations and label them visibly.

Screenshots in `docs/screenshots/next-generation` come from real Chromium. The
`SYNTHETIC-` example is not a genuine commercial dataset. Read case-study caveats
before presenting a statistic as a population inference or business implication.
