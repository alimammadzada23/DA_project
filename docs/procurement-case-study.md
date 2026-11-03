# Procurement intelligence — preserved case study

The original BPI Challenge 2019 module, event-log engine, four dashboards, 20-query catalog, optional tool-calling analyst and Power BI candidate remain intact. This extension adds independent datasets without replacing procurement.

**Business context:** a hypothetical procurement manager wants to investigate observed waits, variants, repeated handling and long-span groups. Stakeholders include procurement operations, purchasing, accounts payable and process/data owners. No actual client engagement or sign-off is claimed.

**Questions:** where are long recorded intervals? Which case paths and repeats are common? Which matching-flow/vendor/company groups have longer observed spans? Which cases should be reviewed before changing an operating policy?

**KPIs:** item/document/event grains are distinct; observed span is max minus min recorded timestamp, not verified completion time. Median/P90 summarize available spans. Repetition is non-null activity frequency beyond the first per case and may be legitimate. Waiting is the interval between consecutive ordered events. Sequence signals require category-specific review and do not certify noncompliance.

**Source and findings:** the genuine original file has not been obtained; 4TU previously returned upstream HTTP 503. The publisher description is verified separately in [official-source-verification.json](official-source-verification.json). Published totals are expected source counts, not computed results. **No original-company findings exist in this task.** The 1,200-item / 400-document / 4,750-event seeded portfolio is synthetic. It illustrates the interface and analytical method only.

**Recommendations:** use observed differences to prioritize case review, validate matching-flow rules and activity semantics, then test operational hypotheses with owners. Do not claim avoidable rework, bottleneck causality, savings or verified compliance from demonstration patterns.

**Validation:** the original 96 automated tests are retained; real Chromium regression checks procurement filters, reset, case lookup, native box selection and guided SQL. Independent synthetic source/SQL reconciliation checks ten metrics; [portfolio-demo-validation.json](portfolio-demo-validation.json) records that source. These checks validate software, not original-log findings.

**Acceptance still required:** download the [official record](https://data.4tu.nl/articles/_/12715853/1), retain its original XES/XES.gz and source license, import without the synthetic flag, reconcile source bytes/metrics and published-count differences, run actual analysis, and review interpretations with the matching-flow definitions. Follow [download-and-run.md](download-and-run.md). Windows TMDL/DAX/refresh/rendering, live LLM and native Mac execution remain separate checks.

The existing [BA requirements/backlog/UAT package](business-analysis.md), [KPI dictionary](data-and-methods.md), [interview package](portfolio.md) and [Power BI project](../powerbi/README.md) remain available. A genuine current-state study and evidence-backed operating recommendations await the original log.
