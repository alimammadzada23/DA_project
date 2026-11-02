# Power BI deliverable — generated candidate

`InsightPilot.pbip` references a report and an import-mode TMDL semantic model. The four PBIR pages contain seven bound table visuals. They are intentionally basic until real-data interpretation and Windows visual review are possible; this is **not yet an executive-ready, visually accepted report**. Trends, interactive filtering, distribution visuals, drill-through and scenario controls remain backlog items. The Management Decisions page clearly labels its recommendation as a hypothesis.

Six DAX measures are defined in `measures.json` and Cases.tmdl. The measure definition for document volume excludes blank document IDs; percentile calculations exclude null spans. Measures refer to case grain, not event grain. Transition performance is a separate aggregate, so its percentiles cannot be recomputed over underlying transitions by averaging summary rows.

## Authoritative format references

The Microsoft documentation URLs attempted were:

- https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-overview
- https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-report

These pages were blocked by the environment proxy. Microsoft's official [`microsoft/json-schemas`](https://github.com/microsoft/json-schemas) repository was accessible. The project follows those machine-readable contracts; 11 schemas and their MIT license are retained under `schemas/` for offline validation. They were retrieved on 2026-11-02 from `main`; this is a retained snapshot, not a claim that schemas never change. Narrative authoring documentation still requires review when access is restored.

```sh
uv run --extra test python powerbi/validate.py
```

This validates all 16 PBIP/PBIR/PBISM/report/page/visual JSON files and their referenced schema dependencies. **It does not parse TMDL, execute DAX, open Desktop, validate data refresh, or prove visual layout.** Explicit regeneration (`python powerbi/generate.py`) replaces generated files; preserve manual edits before using it. No LLM tool invokes this script. The agent exports proposals only, with explicit UI approval; a human applies and validates edits in Desktop.

## Windows acceptance procedure

1. Use a supported Windows installation of Power BI Desktop (on Apple Silicon, use an appropriate Windows environment or remote Windows machine; do not claim native macOS Desktop support).
2. Inspect the current Microsoft project/TMDL/PBIR documentation and enable required preview features in that Desktop version if applicable. Open `InsightPilot.pbip`. Record Desktop version and any migration prompts; investigate all errors instead of assuming compatibility.
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
