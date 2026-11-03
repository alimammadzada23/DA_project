# Customer Experience & Risk Intelligence — case study and analytical contract

**Status: implementation and synthetic reference validation, not a completed real-data case study.** On 2026-11-03 the cloud proxy denied the official CFPB website/API and documentation site with HTTP 403. **Genuine imported complaints: 0.** No real company finding, ranking or recommendation is asserted here. A source failure is retained in [source verification](cfpb-source-verification.json).

## Business context

A hypothetical customer-experience manager needs to prioritize investigation of reported financial-service complaints. Public complaint records can show reported volumes, concentrations, patterns and reported response categories. They do not by themselves establish incidence among customers, service quality, operational causes or financial impact.

## Stakeholders and decisions

Complaint operations would review concentrated issues and response exceptions. Product owners would compare complaint mix and changes. Customer-experience leaders would choose topics for a structured investigation. Data stewards would validate freshness, definitions, IDs and coverage. This is a portfolio scenario; no client engagement, interviews, stakeholder approval or implemented improvement is claimed.

## Questions

Which reported products/issues dominate a defined period? Which companies are represented most frequently? How do product mix and known response-timeliness categories differ among selected companies? Which issues increased across equal observation periods? What additional information would distinguish reporting effects from a service problem?

## Data and provenance

Source: [CFPB Consumer Complaint Database](https://www.consumerfinance.gov/data-research/consumer-complaints/). API documentation: [official page](https://cfpb.github.io/ccdb5-api/) and the [CFPB-maintained implementation](https://github.com/cfpb/ccdb5-api). The repository README says the hosted documentation is not being updated, so the current repository and schema checks are important. The inspected source revision and hashes are recorded in the verification JSON.

The official OpenAPI definition describes company, product, issue, complaint ID, received date, sent date, response, timely and related fields. The implementation’s JSON metadata declares CC0 for the API. That is source-code/documentation evidence, not a verified live response or independent dataset-license acceptance. Retain and review the current publisher database/about pages and usage limitations after obtaining a snapshot. Do not infer endorsement or unrestricted rights to private uploads from software licensing.

The connector requests a full, bounded CSV export rather than a ranked page. It validates actual returned headers, independently retrieves an exact API hit count, checks for an indexing issue, stores source freshness metadata and retains raw bytes/hash, retrieval time, schema, quality, exclusions and requested coverage. An API count disagreement is an incomplete snapshot, not a successful import. The documented upper date bound and deployed implementation may differ: a local half-open period is enforced, boundary records are counted, and the raw export is preserved.

Manual exports are labeled user supplied, even when the user downloaded them from CFPB, because the application cannot verify that browser action. Synthetic references are prominently labeled and are never a fallback for an unavailable genuine source.

## KPI definitions

| Metric | Calculation and denominator | Interpretation limit |
|---|---|---|
| Reported complaints | Retained positive complaint IDs, first duplicate occurrence retained | Count of published records in this extract, not all consumer experiences |
| Represented companies | Distinct nonempty company names | Missing company values do not become a fictional company |
| Reported complaint volume by group | Complaints grouped by company/product/issue | Raw volumes do not establish service quality or exposure-normalized rates |
| Product/issue share | Group count / all selected complaints | Missing category stays visible; donut Other preserves the complete denominator |
| Timely-response share | Reported Yes / (reported Yes + No) | Unknown/invalid/missing excluded; no inferred actual response duration |
| Known-response coverage | (Yes + No) / selected complaints | Discloses whether the timeliness percentage represents most of the cohort |
| Daily volume | Counts by valid received date | Missing dates excluded; recent publication lag and reporting behavior may affect patterns |
| Equal-period issue change | Later count − earlier count for adjacent equal-length windows within coverage | Odd unmatched day excluded/disclosed; no statistical significance or causation claimed |
| Relative issue change | (later − earlier) / earlier | Undefined when earlier is zero, rather than an invented percentage |

Filters compose in SQL. Inclusive dashboard dates intersect the snapshot’s locally enforced coverage; missing dates remain in totals until a date filter is applied. A drill-down adds a parameterized dimension/value condition to the same selection. It cannot escape the active company/product/date filters.

## Quality and validation

Raw rows are retained in `raw_complaints`. Invalid/missing IDs and duplicate IDs are counted and excluded under the declared policy. Conflicting duplicates are reported. Invalid dates stay NULL for unverified manual imports, with trend exclusions disclosed. Timeliness accepts only normalized Yes/No; unknown does not become late. A received date in the future is a quality signal, not silently corrected.

`cfpb-validate` independently reads the CSV using Python counters and a separately implemented date parser. It compares counts, named companies, known/Yes/No timeliness, missing dates, product/issue distributions, daily distributions, timely share and the original SHA-256 against SQL. Source-byte agreement does not authenticate an uploaded publisher claim. Automated references test company/date/null filters, empty cohorts, donut totals, response denominators, equal windows, read-only SQL and data isolation.

Actual Chromium checks are recorded separately from backend tests and genuine-source acceptance. The reference fixture has **8 retained synthetic complaints from 10 source rows**, **3 named synthetic companies**, **4 Yes / 6 known responses** and **2 unknown responses**. Those numbers only test calculations. They are not CFPB observations.

## Findings

**Pending a genuine official extract.** No observed real-company complaint count, issue concentration or response-performance percentage can be reported from this cloud run. The user should not present the synthetic reference as a real-data case study.

After a successful official import, run:

```sh
uv run --frozen insightpilot cfpb-validate data/cfpb/2026-09-01-week/complaints.csv data/cfpb/2026-09-01-week/analytics.duckdb
uv run --frozen insightpilot cfpb-analyze data/cfpb/2026-09-01-week/analytics.duckdb outputs/cfpb-report
```

This generates `case-study.md`, `management-summary.md`, `source-validation.json`, `query-evidence.json`, `manifest.json` and `Complaints.parquet` from the actual snapshot. Inspect the validation result, provenance, source freshness, coverage and company/product slices before using those findings in an interview.

## Recommendation framework

Once observations exist, prioritize a concentration or equal-period increase for structured complaint review. Test hypotheses involving product mix, publication lag, customer exposure or operational changes with additional evidence. Track equal-period issue counts, known-response coverage and late-response share. Set targets only after confirming data maturity and business definitions. No financial savings, ROI, implemented improvement or causal effect is calculated.

## Limitations and remaining acceptance

CFPB complaints are a selected subset of experiences and reporting behavior. Publication decisions, lags, company size and customer exposure may differ. Narratives and categories do not prove an operational cause. Public company counts are not a definitive quality ranking. A short period cannot establish a persistent trend. The tool is descriptive customer-experience investigation, not a validated financial-risk model.

Live CFPB schema/count/usage validation, real findings, live-provider narrative acceptance, native Apple Silicon execution and actual Windows Power BI acceptance remain unexecuted. The existing procurement case study remains [separate](procurement-case-study.md); its demonstration also does not constitute genuine BPI findings.
