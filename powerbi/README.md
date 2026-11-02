# Power BI deliverable — generated candidate

`InsightPilot.pbip` references a report and an import-mode TMDL semantic model. The four PBIR pages contain twelve bound visuals (seven tables, an executive monthly trend, a waiting-interval distribution and three category slicers). They are intentionally basic until real-data interpretation and Windows visual review are possible; this is **not yet an executive-ready, visually accepted report**. The trend, waiting-interval distribution and category slicers are prepared but untested in Desktop; drill-through and scenario controls remain backlog items. The Management Decisions page clearly labels its recommendation as a hypothesis.

Nine DAX measures are defined in `measures.json` and Cases.tmdl. The measure definition for document volume excludes blank document IDs; percentile calculations exclude null spans. Measures refer to case grain, not event grain. Transition interval measures use raw TransitionEvents rows related to Cases, so category-context percentiles are computed directly rather than averaged from summaries. A separate Transitions summary table is retained for audit and is not used by the filtered interval visuals.

## Authoritative format references

The Microsoft documentation URLs attempted were:

- https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-overview
- https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-report

These pages were blocked in the first milestone. On continuation they were successfully retrieved and reviewed, along with https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-dataset and https://learn.microsoft.com/en-us/analysis-services/tmdl/tmdl-overview. This review identified and corrected the missing required PBIR definition/version.json. Microsoft's official [`microsoft/json-schemas`](https://github.com/microsoft/json-schemas) repository was accessible. The project follows those machine-readable contracts; 12 schemas and their MIT license are retained under `schemas/` for offline validation. They were retrieved on 2026-11-02 from `main`; this is a retained snapshot, not a claim that schemas never change. The narrative authoring documentation has now been reviewed; Windows execution remains outstanding.

```sh
uv run --extra test python powerbi/validate.py
```

This validates all 22 PBIP/PBIR/PBISM/report/page/visual JSON files and static semantic-field bindings and their referenced schema dependencies. **It does not parse TMDL, execute DAX, open Desktop, validate data refresh, or prove visual layout.** Explicit regeneration (`python powerbi/generate.py`) replaces generated files; preserve manual edits before using it. No LLM tool invokes this script. The agent exports proposals only, with explicit UI approval; a human applies and validates edits in Desktop.

## Windows acceptance procedure

1. Use a supported Windows installation of Power BI Desktop (on Apple Silicon, use an appropriate Windows environment or remote Windows machine; do not claim native macOS Desktop support).
2. Inspect the linked current Microsoft project/TMDL/PBIR documentation and use a Desktop version supporting the PBIR definition; record any required feature settings. Open `InsightPilot.pbip`. Record Desktop version and any migration prompts; investigate all errors instead of assuming compatibility.
3. Export genuine data using `insightpilot export ... data/exports`. Copy exports to a Windows directory. Set the model's `DataFolder` parameter to that directory, for example `C:\InsightPilot\exports`, then refresh. A local fixture export must remain labeled synthetic.
4. Confirm all four pages load; inspect labels, table readability, empty values, page navigation, context and warnings. Add the backlog visuals and verified business definitions before presentation.
5. In DAX Query View execute `EVALUATE ROW("Items",[Items],"Documents",[Purchase Documents],"Median",[Median Observed Days],"P90",[P90 Observed Days],"Repeats",[Repeat Cases],"Rate",[Repeat Rate])`.
6. Compare with SQL over the **same imported export and filter context**:

```sql
SELECT count(*) AS items, count(DISTINCT purchase_document) AS documents,
 median(observed_span_days) AS median_days,
 quantile_cont(observed_span_days,0.9) AS p90_days,
 count(*) FILTER(WHERE repeated_event_count>0) AS repeats,
 count(*) FILTER(WHERE repeated_event_count>0)*1.0/count(*) AS repeat_rate
FROM case_metrics;
```

7. Repeat reconciliation for each matching category, null category, one-item cohort and missing-timestamp cohort. Compare nulls separately; allow only documented floating-point rounding tolerance. Save executed DAX/SQL outputs, source hash, screenshots and discrepancies in `docs/validation.md`. No DAX reconciliation result is claimed yet.

## Precise Windows acceptance checklist

- Open PBIP and ensure TMDL parses: parameter, partitions, calculated Start Month column and all measures. Linux has no Analysis Services TMDL parser installed.
- Set DataFolder and refresh exported Parquet. Check first_event timezone/date conversion, missing fields and the export manifest/source hash.
- Run `reconcile.dax` in DAX Query View; run `reconcile.sql` against the exact source snapshot. Compare overall, category and missing-category contexts, then single-item and missing-timestamp cohorts. Counts must match exactly; compare null/BLANK explicitly and use documented floating-point tolerance for quantiles.
- Confirm the executive monthly trend uses Start Month and counts items; test the category slicer against cards/tables/trend. Cases has proposed one-to-many relationships to Signals and raw TransitionEvents. Test page-local category slicers on Executive, Performance and Exceptions; verify that transition counts/quantiles and signal rows follow the correct case cohort. The retained aggregate Transitions table is disconnected and must not be treated as dynamically filtered. Validate waiting-band boundaries at 1/24/168 hours and band sorting.
- Review all four pages for labels, legends, truncation, scrolling, units, page navigation, readable variants and review-only exception language. The decision table contains a hypothesis, not a real-data finding.
- Save Desktop version, refresh result, executed DAX/SQL output and screenshots. Resolve all errors before using the report in an interview. Structural validation alone cannot mark these checks passed.
