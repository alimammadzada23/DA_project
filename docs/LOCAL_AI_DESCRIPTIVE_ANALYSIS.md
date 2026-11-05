# Descriptive analysis without unnecessary setup

Baseline: `b9451ca`, branch `codex/insightpilot-local-ai`. This repair stays local
until a separately authorized push. Streamlit, DuckDB, local Ollama/Qwen, the
finite plan contract, read-only SQL validation and workspace storage remain.

## Changes

The unavailable-field preflight used to interpret the first ordinary word after
`column` or `field` as an identifier. It now distinguishes grammatical continuations
such as “column contains” from literal references. Quoted references and references
introduced by `named` or `called` remain strict. Genuine unknown fields still ask
for clarification; live validation still rejects unavailable plan fields.

The existing full-table profile already verifies conversions and computes numeric
statistics. A shared check now allows descriptive calculations without business
approval when **every nonblank value parses as a finite number**, the field is
inferred numeric, its name is not identifier-like, and it has no explicit
nonnumeric role. Binary 0/1 fields remain ambiguous outcomes/codes and require
review. Explicitly reviewed numeric fields keep their existing behavior. Partial
numeric text and literal `NA`/`?` markers are not silently converted or reclassified
as missing.

Definitions and the result screen identify technical descriptive statistics and
unconfirmed units. Asking a question changes no roles, grain, units or approvals.
Saved result blocks replay these calculations. Saved non-count **business metric
definitions**, outcome rates, eligible denominators and calendar meanings retain
their existing review/approval gates. Source-row counts do not infer customers,
transactions or conversions.

The Standard parser and Local AI request checks interpret categorical-size
rankings without another named measure as source-record counts. Explicit limits
and ordering remain binding; no requested limit means complete groups within the
existing 100-group bound. An invented numeric ranking measure cannot execute;
the existing one-repair budget remains in force.

An unspecified “highest-value column” now asks which field or comparison the user
means before calling a model. An explicit “Compare maximum numerical values across
columns” uses a small `column_maxima` operation in the existing registry. It reuses
profiling for eligibility and executes trusted DuckDB maxima/counts within sidebar
filters, keeping original field order and identifying units/review status. It
declares no cross-unit winner. Recalculation preserves filtered-scope evidence
instead of presenting cached full-table extrema as filtered results.

Quality & Preparation prefills generic `measure`/`category` suggestions in the
existing bulk editor. Users can review several together and save once. Identifier
candidates, binary codes, dates and uncertain conversions are left for review;
no financial or outcome role, units, date purpose or date basis is filled
automatically. Opening the form saves no suggestions. Saving still requires
explicit confirmation and a reviewed row description.

## Limits and verification

- Numeric verification establishes stored values, not meaning. Unrecognized
  numeric category codes can still look numeric; outputs remain raw descriptions.
  Mark known codes as categorical when that distinction matters.
- Identifiers, binary fields, explicit nonnumeric roles and failed conversions
  require review before arithmetic. NULL/blank handling is unchanged; other
  missing codes require explicit preparation.
- If two actual numeric names are ambiguously mentioned (for example both
  `campaign` and `value` exist), the parser asks for a specific field.
- Per-column maxima allow at most 40 verified fields and no top-N or mixed-unit
  ranking. Overflow clarifies instead of omitting columns. Apply sidebar filters;
  conditional/follow-up wording this path cannot preserve asks for clarification.
- Standard analysis retains three reported benchmark gaps: conditional-rate
  wording (`s18`, `s19`) and natural-language period boundaries (`s22`). They
  safely clarify and remain failures against their unchanged references.
- **No real Qwen or native Mac acceptance was performed for this repair.** HTTP
  mocks establish software behavior, not model reliability or Mac performance.

The regression suite covers prose versus literal fields, unreviewed numerical
statistics, ambiguity, grouped counts/rankings, renamed columns, limits, numeric
codes/conversion failures, business review, read-only guards, saved replay and the
bulk review screen. See [validation receipt](local-ai-descriptive-validation.json)
and [prepared schema/prompt fingerprints](local-ai-descriptive-contract.json).

## Repeat on the existing Mac

After this repair is separately authorized and reaches your existing clone, stop
the app, preserve local edits and update the **same feature branch**. No rebuild,
dependency installation, model change or migration is required. Record
`git log -1 --oneline`, `git status --short`, `ollama --version` and the installed
model tag/digest. Reuse the existing `qwen3.5:4b`.

Run the existing actual-model protocol, which retains every failed pass:

```bash
cd ~/DA_project
./"Check Local AI.command" --runs 3 --diagnostics --bank-data outputs/uci-bank-marketing.zip
```

The bank path must be the existing official public UCI Bank Marketing archive or
`bank-full.csv`, not personal data. If absent, omit `--bank-data` and record that
the public-data check remains outstanding. Diagnostics are opt-in and restricted
to the built-in synthetic fixture. Require three complete 16/16 synthetic passes
and each public-data top-five reconciliation before claiming that protocol passed.
Check report commit, dirty marker and schema/prompt fingerprints against the
prepared contract. Never enable raw diagnostics on private data.

That protocol reviews its fields, so it does **not** establish the new unreviewed
workflow. Generate an additional isolated synthetic CSV and independent references:

```bash
.venv/bin/python - <<'PY'
import csv
from pathlib import Path
import duckdb
directory=Path('outputs/descriptive-mac-check');directory.mkdir(parents=True,exist_ok=True)
source=directory/'SYNTHETIC-descriptive.csv'
rows=[]
for group,n in enumerate(range(12,0,-1)):
    for i in range(n):
        rows.append([f'G{group:02}', '' if group==0 and i==1 else 20+group+i,
                     10*(group+i+1),1+i%3,f'{len(rows):05}',i%2])
with source.open('w',newline='') as stream:
    writer=csv.writer(stream);writer.writerow(['job','age','duration','campaign','entity_id','binary']);writer.writerows(rows)
with duckdb.connect() as connection:
    connection.execute('CREATE TABLE original AS SELECT * FROM read_csv_auto(?)',[str(source)])
    connection.execute('SELECT max(duration) maximum_duration,avg(age) mean_age,median(campaign) median_campaign FROM original').df().to_csv(directory/'numeric-reference.csv',index=False)
    connection.execute('SELECT job,count(*) records FROM original GROUP BY job ORDER BY records DESC,job').df().to_csv(directory/'groups-reference.csv',index=False)
print(source)
PY
```

Start **Start InsightPilot.command**. Create a separate acceptance workspace and
import only this synthetic CSV. **Do not approve roles, grain or units.** Choose
**AI Analyst → Local AI Analyst**, refresh models and select the existing Qwen.
Ask each as a fresh analysis except the stated follow-up:

| Question/check | Required behavior |
| --- | --- |
| “What is the maximum duration?” | 120; technical descriptive status, unconfirmed units |
| “What is the average age?” | `2111 / 77` (about 27.4155844156); 77 finite inputs |
| “Show the median campaign value.” | 2; no role/grain approval |
| “Which job category has the most records?” | G00 first with 12; complete 12-group table without a limit |
| “Rank job categories from highest to lowest.” | All 12 groups, counts 12 through 1 |
| “Show the five largest job categories.” | G00–G04, counts 12, 11, 10, 9, 8 |
| Follow with “Now sort those results from lowest to highest by record count.” | Same five groups, counts 8 through 12; unchanged membership |
| “Which column contains the highest individual value?” | Comparison clarification; no unavailable `contains` field or calculation |
| “What columns have the highest value?” | Clarification; no arbitrarily chosen field |
| “Compare maximum numerical values across columns.” | Separate maxima: age 31, duration 120, campaign 3; no identifier/binary arithmetic or overall winner |
| “Show the average of field called absent_field.” | Unavailable-field clarification; no substitute |
| “What is the average entity_id?” / “What is the average binary?” | Review/clarification; no automatic arithmetic |
| “What is the conversion rate?” | Request reviewed business definitions; no guessed conversion |
| “Create a data file with the average age.” | Read-only refusal; no file creation |
| Save average-age result, restart, reopen | Same version/result; data and approval flags retained |
| Open Quality & Preparation | Generic safe suggestions; units/date meanings empty; opening approves nothing |

Per-column maxima and preflight clarifications/refusals are deterministic; do not
count them as Qwen planning evidence. For numeric/group questions, verify the
backend reports an actual completed Ollama request. Keep failures and redacted
plans instead of changing expected answers. For a filtered check set shared `job`
to G00: maximum age 31, mean age `285 / 11`, maximum duration 120. Single-field
questions and per-column maxima must agree. Clear the filter afterwards and repeat
ordinary questions with different synthetic numeric/category field names.
