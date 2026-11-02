# Data contract and analytical methods

## Source status

The requested DOI is `10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1`. Official metadata, original files and license remain uninspected because requests were denied by the environment proxy. Published figures in the request are expectations only. No real-data conclusion is available.

## Grain and mapping

| Table / column | Grain / definition | Source / caveat |
|---|---|---|
| cases.case_id | One purchase-order line item | trace `concept:name`; mandatory and unique |
| cases.purchase_document | Purchasing document grouping | trace `Purchasing Document`; provisional mapping |
| cases.item | Item reference within document | trace `Item`; no uniqueness assumed alone |
| cases.matching_category | Flow label | trace `Item Category`; raw value retained, no universal rule |
| cases.vendor | Anonymized vendor | trace `Vendor`; not used to identify companies |
| cases.spend_classification | Spend classification | trace `Spend classification text` |
| events | One recorded event | Parent case plus zero-based source_order; duplicates retained |
| events.activity | Recorded activity | event `concept:name` |
| events.event_time | UTC timestamp | `time:timestamp`; offset mandatory if present |
| events.resource | Recorded resource ID | `org:resource`; missing retained |
| events.lifecycle | Lifecycle marker | `lifecycle:transition`; no duration assumption |
| source_attributes | All direct source attributes | JSON; no guessed type conversions; nested XES attributes not normalized |
| provenance | One import | SHA-256 of source bytes, mapping, import time, user-supplied synthetic flag |

The supported XES contract is a standard log containing traces and flat scalar trace/event attributes. Extension declarations are accepted; nested lists/containers and log-global default inheritance are not normalized. Inspect the genuine log for these constructs before readiness acceptance. The original bytes remain unchanged; preserving raw data permits extending the mapping without loss.

Raw source belongs under `data/raw`; the database belongs under `data/analytics`; analytical Parquet snapshots under `data/exports`. No observations are silently filtered. Missing case IDs, duplicate trace IDs, empty traces, malformed XML and malformed/offset-free dates reject the import atomically. Missing activities/timestamps/resources are retained and counted. Duplicate attribute keys reject the import. A failed import leaves no target database. Source hashes identify bytes, not publisher authenticity.

## Ordering and metrics

Analytical ordering is timestamp ascending, null timestamps last, then original event position. Ties have a deterministic order but do not establish causality. Source order is retained; reversals and timestamp ties are reported. Timestamp review window is 2000 through execution time: a screening convention, not a source-derived exclusion criterion.

| KPI | Definition | Interpretation |
|---|---|---|
| Item volume | count(cases) | Not document volume |
| Document volume | count distinct non-null purchase_document | Missing IDs reported separately |
| Event volume | count(events) | Recorded events, including duplicate signatures |
| Observed span | max timestamp minus min timestamp / 86400 | Calendar days; single timestamp = 0; no timestamps = null |
| Median / P90 span | median / continuous 90th percentile over non-null spans | Not completed-case cycle time |
| Repeated occurrences | non-null activity count minus distinct non-null activities | Missing activities excluded from this metric and reported separately |
| Repeat cases / rate | cases with repeated occurrences > 0 / all cases | Repetition signal, not proven avoidable rework |
| Variant | Ordered full activity sequence | Missing activity labeled; case-sensitive, no filtering |
| Transition wait | Consecutive recorded timestamps difference in hours | May include service, waiting and off-hours; not activity duration |
| Completion | unknown for all cases | Source-specific terminal rules and observation cutoff require verification |
| Sequence signal | First recorded invoice receipt precedes first goods receipt | Review only; two-way, three-way and consignment need separate interpretation |

Missing-activity records are excluded from the repeat numerator, retained in event counts and labeled in variants. Repeat rates can therefore understate true repetition when activity coverage is incomplete; report missing-activity coverage alongside them.

Actual observed boundaries will be derived from first/last activities and the source documentation; there is no claim of complete procure-to-pay visibility. Recorded start/end activities do not prove business start/completion. Human/batch classification is deliberately unknown until an authoritative resource mapping is inspected. Resource frequencies are available, but no invented ID heuristic is used.

PM4Py converts an aggregated directly-follows graph into a Petri net. This provides actual process discovery without materializing another full event-log DataFrame. It is descriptive: no normative model or token-replay compliance conclusion is claimed. Event durations require compatible lifecycle pairs, which are not implemented. Category-stratified conformance and censoring models remain pending official field/rule verification.

The importer streams trace-by-trace, flushes bounded event batches, and constrains DuckDB to two threads and 1 GB for ingestion. Memory also depends on largest trace and analytical aggregation. Full 1.6-million-event performance has not been measured.
