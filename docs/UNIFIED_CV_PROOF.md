# Factual portfolio wording

Built a local-first analytics application with Python 3.12, Streamlit, DuckDB,
Plotly and SciPy, consolidating multiple source-specific dashboards into one
versioned workspace, reviewed semantic model and evidence pipeline. Implemented a
restricted SQL/CTE workbench with validated joins, resource budgets, saved queries,
chart/report handoff, safe metric expressions and replayable source-hash manifests.

Verified the workflow against four authentic public datasets, including a source
selected after the general engine was implemented. Independently reconciled
aggregates, weighted sample shares, grouped means, statistical calculations,
histograms and relational joins; the operational case retained 336,776 records and
52,606 unmatched dimension keys. The complete regression run passes 294 tests;
actual Chromium checks cover all four datasets and tablet layouts.

In one Linux synthetic benchmark, one million rows imported in 1.43 seconds,
profiled in 1.55 seconds and used 282 MiB peak process RSS. State the machine and
single-run scope when quoting these numbers. Do not describe them as native Mac,
cold-disk, genuine-data or multi-user production benchmarks.

Useful interview decisions: immutable preparation plus semantic snapshots;
explicit timestamp purpose rather than column-order defaults; ratios of eligible
aggregates instead of averaged rates; exact-key, unique-dimension joins; AST SQL
validation plus execution barriers; finite optional AI plans with locally computed
numbers; retained original bytes and parameterized evidence.

No claim of real users, adoption, commercial savings, predictive calibration,
causal effects, live model validation or native Power BI behavior is supported by
this work. Use [acceptance receipts](UNIFIED_ACCEPTANCE.md) for precise boundaries.
