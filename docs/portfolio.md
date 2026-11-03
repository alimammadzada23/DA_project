# Portfolio and interview package — provisional

This is a portfolio project using public anonymized data and a hypothetical procurement sponsor. Until the official log has been imported and validated, present the software demonstration as synthetic and do not claim company findings.

## Five-minute presentation

1. **0:00–0:45 · Challenge:** procurement leaders need trustworthy visibility into recorded processing patterns, not assumed inefficiency.
2. **0:45–1:30 · Requirements:** separate document/item/event grains; make rules testable; preserve evidence and disclose missing observations.
3. **1:30–2:30 · Methods:** stream XES into DuckDB, inspect data quality, mine directly-follows patterns and independently test calculations.
4. **2:30–3:15 · Findings:** currently only fixture-level verification; replace this section with traceable real-data results after source acceptance.
5. **3:15–4:00 · Decisions:** validate matching rules, investigate stratified waits and consider measured pilots. Do not promise savings or automatic compliance.
6. **4:00–5:00 · Demo:** run a deterministic investigation, inspect SQL/source hash, show the optional agent audit, then disclose Power BI acceptance status.

## Demonstration runbook

Install using README; run tests; import the genuine source or explicitly announce a fixture; inspect the quality report; run `ask ... categories`; inspect returned SQL and caveats; execute a custom SELECT; show denied file access; discover a process graph; open Streamlit; inspect proposal export approval; on Windows open the PBIP and perform the documented reconciliation. If Desktop or API access is unavailable, state that before the demo and show the tested components. Never present mocked provider tests as a live API demonstration.

## Case-study narrative

The business problem is turning event-level procurement records into defensible management information. The engineering work implements raw-preserving ingestion, explicit grain, deterministic ordering, SQL views, descriptive PM4Py discovery and controlled investigations. The quality approach uses small hand-calculable fixtures and adversarial SQL tests. The main trade-off is conservative interpretation: observed intervals and review signals are useful without pretending completion or compliance semantics are known. Actual operational discoveries, future-state justification and savings remain unfinished until the source/rules are verified.

## Fifteen interview questions and defensible answers

1. **What was the business problem?** A simulated sponsor needed visibility into delays and exceptions. This is not evidence of a real sponsor engagement.
2. **How did you elicit requirements?** I translated a hypothetical scenario into measurable BRD requirements and traceability. I did not conduct stakeholder interviews; a real deployment would validate them with process/AP/compliance owners.
3. **Why use PO items as cases?** Items can have different categories and event paths within a single document. Document-level aggregation would conflate flows.
4. **How did you establish data accuracy?** Source-byte hashes, count comparisons, quality reports and independent fixture references. Official-source validation is still pending, so I do not claim real-data accuracy yet.
5. **Why streaming XES?** Trace-by-trace parsing and bounded batches avoid redundant whole-log copies. Full-volume Mac performance is not benchmarked yet.
6. **How are timestamps ordered?** UTC timestamp then source position; missing timestamps last. Tied ordering is reproducible, not proof of causal sequence.
7. **What does cycle time mean here?** The implemented measure is observed first-to-last span. A true completed-case measure needs validated terminal conditions and censoring rules.
8. **How do you detect rework?** Count excess non-null activity occurrences per case. It signals repetition; it does not prove avoidable work, especially with multiple receipts/invoices.
9. **What is process mining beyond counting?** The system derives ordered variants and directly-follows edges and uses PM4Py to construct a descriptive Petri net. Normative conformance is not yet implemented.
10. **Why not flag invoice-before-receipt as a violation?** Applicability differs by matching category and incomplete observation. The implemented output is review_required, not noncompliance.
11. **Explain a key SQL query.** `category_performance` groups item-level spans by raw matching category and computes median/P90. It does not average event-level durations or claim causation.
12. **Explain your DAX.** Items counts case rows; document count excludes blanks; MEDIAN and PERCENTILEX.INC summarize non-null observed spans; Repeat Rate divides repeat cases by item volume. Desktop execution and SQL reconciliation are pending.
13. **Can the agent change data?** Its tool gate permits one SELECT/WITH, uses a read-only connection and disables external access. There is no shell or report-writing tool. LLM prose still needs evidence review.
14. **What recommendation can you defend?** Verify source/rules and investigate statistically meaningful category-stratified signals before a pilot. I cannot yet defend a specific operational bottleneck or savings number.
15. **What would challenge your conclusion?** Censoring, legitimate repetitions, resource misclassification, confounding, missing observations, timestamp quality and filter-context differences. Each needs explicit checks and stakeholder validation before operational action.

## Exact continuation

Apply the saved network-domain additions in environment settings. Retrieve the official DOI record and license, inspect actual attribute mappings and category definitions, import the genuine log into a new database, explain every published-count discrepancy, benchmark full-volume ingestion, implement validated completion/resource/conformance rules with new edge-case tests, generate an evidence-linked findings report, finish Power BI visuals on Windows and execute DAX/SQL reconciliation, test the live LLM using approved API access, then revise the decision brief and portfolio narrative with real results. Preserve the existing code and do not replace missing evidence with fictional findings.
