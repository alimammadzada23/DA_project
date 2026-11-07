# Working with large tables

InsightPilot handles tables of up to 100 million rows on this Mac. Every aggregation
runs in DuckDB, and pages only receive bounded results. No rows are sampled or dropped.

## Limits

| Limit | Value |
| --- | --- |
| Rows per table | 100 million. A larger import is refused as a whole |
| Columns per table | 150 |
| File size | No fixed limit. The free-disk check decides |
| Browser upload | 512 MB per file. Use a path import for anything larger |
| Excel | One sheet or all sheets, up to Excel's own 1,048,576 rows per sheet |

## Engine settings

DuckDB uses about 55% of physical RAM and one thread per CPU core, which is 9 GB and
10 threads on a 16 GB, 10-core Mac. Work that doesn't fit in memory spills to a private
temporary folder outside your projects, so spill files never enter a saved project.

To override these, set environment variables before starting the app. For example, use
less memory when running Qwen alongside a big job:

```bash
export INSIGHTPILOT_DUCKDB_MEMORY=6GB
export INSIGHTPILOT_DUCKDB_THREADS=8
export INSIGHTPILOT_DUCKDB_TEMP=/Volumes/Fast/insightpilot-spill
```

Finder doesn't inherit these from Terminal, so start the app from that same Terminal.

Query time limits grow with table size, starting at 2 million rows: 500 seconds for
ordinary calculations and 250 seconds for SQL Lab at 50 million rows, up to 900 seconds.
If a calculation hits its limit, you get a clear message and no partial result.

## Import a file or folder by path

Go to **Data Hub → Upload datasets → File or folder on this Mac** and enter a path.

- **One file:** CSV, TSV, Parquet, JSON, JSON Lines or Excel.
- **A folder:** its top-level files of one type become one table, with a `source_file`
  column. Subfolders are not read. Files must have the same column names, in any order
  and any capitalization.
- **Type differences:** monthly exports often change a column's type, such as whole
  numbers in one month and decimals in the next. These are widened to a common type and
  reported. Any other structural difference is refused.
- **Provenance:** each file's name, SHA-256 hash and size are kept under
  **Detected fields, missingness and provenance**.

Files are copied into the project, so your originals are never changed. Rejected files
stay in the project's `sources` folder with a receipt, as they do for other imports.

## Excel

For a workbook with several sheets, choose one sheet or **All sheets into one table**,
which adds a `sheet` column. All sheets need the same column names.

If identical rows appear in more than one sheet, the dataset page shows how many and
lists the first 20. **Nothing is removed, so totals count those rows more than once
until you decide.** To keep one copy of each row, go to **Quality & Preparation**, remove
the `sheet` column, then deduplicate. That also removes rows repeated inside one sheet,
which can be genuine repeat transactions, so check the counts first.

Excel is the slowest format, at about 11,000 rows per second including the overlap
check. Workbook cells are stored as text, the same as CSV.

## CSV without a header row

Turn on **These CSV/TSV files have no header row** and enter the column names. The
preview shows them above your first rows, so you can check they line up. The first row
is imported as data.

UK Land Registry Price Paid files use these names, which the app also shows for copying:

```text
transaction_id,price,transfer_date,postcode,property_type,new_build,duration,paon,saon,street,locality,town,district,county,ppd_category,record_status
```

## Disk space

Before copying anything, an import checks for room for the source copy, the table and a
2 GB working margin. If there isn't enough, it says how much it needs and changes nothing.

Each of these also uses disk:

- **Save Project and opening a saved project** each make a full copy of the database
  and source files.
- **Each preparation step** stores a new full version of the table.
- **Failed imports** can leave the database file larger, because DuckDB doesn't always
  shrink it.

## Measured performance

Measured on a 16 GB, 10-core Mac with 50 million rows and 7 columns. Every total was
checked against the known generated values. Full results are in
[large-data-benchmark.json](large-data-benchmark.json).

| Operation | Seconds |
| --- | --- |
| Import 12 Parquet files by folder (223 MB) | 40 |
| Import one CSV (2.5 GB) | 69 |
| Import a two-sheet workbook, 1.07 million rows (49 MB) | 97 |
| Full profile of one table, first time after the app starts | 41–45 |
| Overview, first visit | 16 |
| Overview, repeat visit | 0.3 |
| Explore, numeric distribution, first time | 14 |
| Monthly trend, histogram or median | 3.5–9 |
| Group by a category, or a sum | under 1.1 |
| Records page, including a deep page | under 0.1 |
| SQL Lab: group by, full sort or distinct | under 1 |
| BI Studio: total by month | 11 |
| BI Studio: last year by month | 26 |
| **BI Studio: year-to-date by month** | **199** |
| Check relationships, when asked | 33 |
| Save Project | 6 |
| Open a saved project | 4 |

Peak memory for the whole process was 9.1 GB.

**How to read these numbers**

- **The data is synthetic and very repetitive.** The 2.5 GB CSV became a table of about
  500 MB. Real data with more varied text will take more disk and run slower.
- **Only server time is measured.** Browser drawing time is not included. Tests drove
  the real pages without a browser, and the import screens were also checked by hand
  in one.
- **Several Explore charts ran after Overview had computed the same query,** so their
  page times are not first-run times. The engine timings above are the fair comparison.
- **A real `online_retail_II.xlsx` and real taxi or Land Registry files were not tested.**

## What to expect while working

- **First look at a table:** profiling reads every row and runs once per table version
  each time the app starts. It took about 45 seconds at 50 million rows.
- **Repeat views are fast.** Table versions never change, so slow results are reused
  until the app restarts. SQL Lab always runs your query again.
- **Relationship checks wait for you** when any table is over 5 million rows, because
  they read every row. Join previews are skipped when keys would produce more than 200
  million joined rows.
- **Year-to-date measures in BI Studio are slow on big tables,** because each one scans
  the table many times and parses its dates each time.

To repeat the measurements (uses a temporary folder and never touches your projects):

```bash
.venv/bin/python scripts/benchmark_large.py --rows 50000000 --output outputs/large.json
```
