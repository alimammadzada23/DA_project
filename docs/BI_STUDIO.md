# BI Studio — Power BI-style modelling, DAX and report pages

BI Studio is the eleventh wheel destination. It lets you practise the core Power BI
skills — semantic model, DAX measures, calculated columns, time intelligence and report
pages with slicers — on any dataset in your project, on a Mac, with no Power BI Desktop.

## Tabs

| Tab | What you do |
| --- | --- |
| **Report** | Create pages; add Card (with % change vs another measure), Bar, Column, Line, Donut and Matrix visuals (with Total row); add slicers (category values or a date range) that filter every visual on the page. |
| **Measures (DAX)** | Write measures in a DAX editor, *Check* them (value plus the SQL InsightPilot generated), save them with a format, or start from Quick measure patterns (sum, % of total, CALCULATE filter, YTD, last year, YoY %, MoM change, rank). Cheat sheet included. |
| **Model** | Model diagram (fact table plus automatic **Date** table on the shared date basis, one-to-many), calculated columns with preview. |

In DAX the fact table is referenced by its InsightPilot table name, e.g. `orders[amount]`.
The Date table is `'Date'` with columns Date, Year, Quarter, Month, Month Number, Weekday and Day.

## Supported DAX

Aggregations `SUM AVERAGE MIN MAX MEDIAN COUNT COUNTA COUNTBLANK COUNTROWS DISTINCTCOUNT DISTINCTCOUNTNOBLANK`;
iterators `SUMX AVERAGEX MINX MAXX`; filter context `CALCULATE FILTER ALL REMOVEFILTERS ALLEXCEPT KEEPFILTERS`, `IN {..}`;
time intelligence `TOTALYTD TOTALQTD TOTALMTD DATESYTD DATESQTD DATESMTD SAMEPERIODLASTYEAR DATEADD PREVIOUSMONTH PREVIOUSQUARTER PREVIOUSYEAR PREVIOUSDAY`;
logic `IF SWITCH AND OR NOT ISBLANK COALESCE HASONEVALUE SELECTEDVALUE RANKX DIVIDE ABS ROUND INT`; row functions for
calculated columns `YEAR MONTH DAY QUARTER UPPER LOWER LEN VALUE CONCATENATE &`.

Not supported: `VAR/RETURN`, calculated tables, multiple fact tables/`USERELATIONSHIP`, and measures inside `FILTER`
conditions. Unsupported input is rejected with guidance; nothing runs.

## How it is evaluated

`dax.py` tokenizes and parses DAX into a syntax tree, accepts only whitelisted functions and existing columns, and
compiles each measure to DuckDB SQL. Filter context is a set of column conditions (row fields, slicers, CALCULATE
filters); every aggregation is a correlated subquery over the selected rows matching that context, so `ALL`,
`CALCULATE` overrides and `RANKX` follow DAX semantics. Time intelligence moves the date condition (e.g. last year =
rows whose date plus one year falls in the visible period); with no date on the row the visible period is the Date
table's span in that context. Literals are escaped, identifiers quoted; queries run through the existing Engine with
its timeout, shared sidebar filters (report-level filters) and immutable dataset version.

Measures, calculated columns and pages are stored in the project workspace and follow Save Project / Discard.

## Verification

- `tests/test_dax.py` — 21 tests comparing every measure family with independent pandas results (grouping, blanks,
  % of total with ALL, CALCULATE overrides, SUMX, FILTER, SAMEPERIODLASTYEAR, TOTALYTD, PREVIOUSMONTH, RANKX,
  calculated columns, slicers, shared filters, error guidance, self-reference and injection attempts).
- `tests/test_bi_studio_ui.py` — the real app: measure check/save, page, card, bar, slicer, invalid DAX, model tab.
- `scripts/browser_bi_studio.py` — real Chromium render of a prepared report page and all tabs (synthetic data).
- `scripts/browser_redesign.py` now derives the wheel destination count from `ui_workflow.PAGES` (11).

This is a practice-grade DAX subset over DuckDB, not Microsoft's engine: results match DAX semantics for the
supported functions on a single fact table with an automatic Date table. Validate production Power BI models in
Power BI Desktop.
