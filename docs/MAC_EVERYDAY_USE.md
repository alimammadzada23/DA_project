# Start InsightPilot tomorrow

Keep using **`~/DA_project`**, your existing clone and its existing environment.
There is no need to download another ZIP, clone again, reinstall Python, reinstall
Ollama or download Qwen again.

The published baseline is `b9451ca` on `codex/insightpilot-local-ai`. The separate
reliability repair `716bd42` and BI workflow upgrade are **local until you authorize
a push**. A pull cannot retrieve unpublished commits. After publication, update
the existing clone using the safe steps below. See the [workflow and screenshot
guide](BI_WORKFLOW_UPGRADE.md); no new installation or data migration is needed.

## Everyday startup

1. In Finder, open your Home folder, then **DA_project**.
2. Double-click **Start InsightPilot.command**.
3. Leave its Terminal window open. Your browser opens when Streamlit is ready.
4. Use **Workspace Home** to open your project and continue through Data Hub,
   Quality, Explore, Investigate and Reports. For model assistance, open
   **AI Analyst → Local AI Analyst**, refresh models and select `qwen3.5:4b`.

To stop, press **Control-C** in the launcher's Terminal. You can then close that
window. Tomorrow, double-click the same file again. Stopping and restarting does
not remove imported data, names, reviewed roles, saved analyses or report blocks.
The separate Ollama application may remain running; this launcher does not stop
another application's service.

The real `.command` files must stay inside the project. You may create a Finder
alias for your Desktop. Moving the **whole project folder** is supported because
the launcher finds its own directory; do not copy just the command file elsewhere.
A Python environment is not portable between different Macs: on a different
machine, perform the one-time environment setup there.

## What the launcher does

It uses `.venv/bin/python` directly. It checks installed modules, imports the app,
starts Streamlit bound to `127.0.0.1`, waits for its health endpoint, and opens the
correct URL. It does not run `uv sync`, Git updates, model pulls or installers.

A second launch reuses this project's running server. If the preferred port 8501
belongs to another application, it selects an available nearby port and opens
that URL. The URL includes a project-specific path so readiness cannot be confused
with another Streamlit app. The correct link is also printed in Terminal. An
existing manually started server is never killed or taken over. Control-C stops
only the server owned by this launcher.

Ollama is optional. If the selected local endpoint/model is already reachable,
the launcher reports its metadata and starts no second daemon. If the default
service is offline on macOS, it tries to open the **already installed** Ollama
application. Before that launch it sets `OLLAMA_NO_CLOUD=1` and
`OLLAMA_HOST=127.0.0.1:11434` in the macOS GUI launch environment. It never runs
`ollama serve`, installs Ollama or pulls weights. If startup fails, InsightPilot
still opens and Standard analysis works.

An already running Ollama retains its own configuration. Model metadata and
loopback checks are not an inference test or proof about an untrusted daemon's
network traffic. For an existing server whose configuration you have not reviewed,
set the two variables above with `launchctl setenv`, quit Ollama and reopen it
once. Local AI rejects remote endpoints, redirects and reported cloud models;
there is no automatic external AI fallback.

## Verify the real Qwen repair

The latest real Mac report was **14/16 at `c2cfd89`**: count and source-row plans
were rejected for null limits. Automated tests do not establish real-model
success. Follow the
[operation-audit and three-run procedure](LOCAL_AI_OPERATION_AUDIT.md)
to compare actual plan shapes, application corrections and a new real-model result.

After Ollama is running, double-click **Check Local AI.command**. No complicated
commands or personal dataset setup are needed. It exercises InsightPilot's actual
local planner, schema, semantic validator and calculation engine using 70 clearly
synthetic observations in an isolated directory. Existing workspaces are not
selected, imported into, modified or renamed.

The 16 checks include the exact reported job-count question, five groups in
descending order, exact counts, bar-chart values, numeric summaries, filtered
source rows, missing-value exclusion, ten smallest groups, top-three/chart/sort
follow-ups, clarification and refusal. Each calculated answer is compared with
separately executed DuckDB queries over the original fixture. Results go to:

`outputs/local-ai-acceptance/<run>/report.json`

The report identifies model attempts, repair use, valid model output, plan
validation, execution, numerical correctness, charts and clarification/refusal.
An application safety guard may refuse before calling Qwen; those entries report
zero model attempts and do not count as model-generated refusals. A missing
service reports **zero executed cases**, not a successful or 0%-accurate model.
Failures remain failures, with the validated plan and independent reference.
Expect initial model loading to take longer; no particular Mac speed is promised.

The equivalent Terminal command, using the existing environment, is:

```bash
cd ~/DA_project
./"Check Local AI.command" --diagnostics
```

The full reliability target requires three fresh complete passes and the public
UCI Bank Marketing question. After this revision is available on the Mac, use:

```bash
mkdir -p outputs
curl --fail --location --proto '=https' --tlsv1.2 \
  'https://archive.ics.uci.edu/static/public/222/bank+marketing.zip' \
  --output outputs/uci-bank-marketing.zip
./"Check Local AI.command" --runs 3 --diagnostics \
  --bank-data outputs/uci-bank-marketing.zip
```

Read `outputs/local-ai-reliability/<run>/summary.json` and every per-pass report.
All three synthetic runs must pass 16/16 and all three public-bank checks must
pass. Failures are retained. Synthetic diagnostics never run on the public data;
only the public job column enters its isolated workspace. The original single-run
command remains available, but it does not establish the three-run target.

Alternatively, use `.venv/bin/python scripts/accept_local_ai.py --diagnostics`.
The optional flag adds `rejected-plans.jsonl` beside `report.json`, restricted to
the byte-verified synthetic fixture. It redacts unknown keys/values and model
free text. Without the flag no plan diagnostic file is created. The unavailable
field, file-creation and deletion cases now run through deterministic application
guards; the other thirteen cases require the model. Inspect both files and
record `ollama --version` after the rerun. Keep `qwen3.5:4b` unchanged.

Use the direct `.venv/bin/python scripts/accept_local_ai.py` entry point with
`--runs 3 --diagnostics --timeout 120 --bank-data outputs/uci-bank-marketing.zip`
if loading exceeds the normal acceptance wait.
The app's ordinary Local AI controls offer their own bounded wait/context
settings. A missing operation or explicit missing-value scope error may receive
one repair attempt using remaining planning time, with two total attempts at
most. There is no unlimited retry loop. If a case still fails,
inspect its first failure reason and plan, confirm the exact field/role/model tag,
record `ollama --version`, and retain the sanitized diagnostic files for review.
Do not change the model, denominator, field role or expected result to make a
test pass.

**Real Qwen and native Finder were unavailable in the Linux development
environment.** Linux browser/process and mocked protocol tests do not establish
that the original request succeeds on your Mac. This acceptance command is the
remaining real-model check. After it passes, repeat the original question against
your own reviewed dataset and independently reconcile it in SQL Lab.

## Keep your work

Persistent local assets use the existing storage, with no migration:

| Asset | Location / behavior |
| --- | --- |
| Imported sources and immutable versions | `data/universal/<workspace>/sources/` and `workspace.duckdb` |
| Workspace names and descriptions | `.insightpilot-workspace.json` in that same workspace |
| Reviewed roles, units, metrics, saved views/queries and analysis blocks | The same workspace catalog database |
| Explicitly saved AI results, figures and evidence | Existing Reports blocks, replayable without the model |
| Downloaded HTML/CSV/XLSX reports | Your chosen browser download location |
| Conversation and unsaved widget state | Temporary browser session; use **Save this analysis to Reports** or a saved view to resume a specific analysis |

Back up the entire workspace folder, including its catalog, metadata and sources,
while the application is stopped. No workspace is deleted or recreated by the
launcher. You may need to reselect a workspace or saved view after a fresh browser
session. Custom workspace environment variables remain supported and are inherited
by the launcher; Finder does not inherit temporary exports from another Terminal.

## Big files

InsightPilot now handles tables of up to 100 million rows. DuckDB sizes itself to this
Mac (about 55% of RAM and all CPU cores) and uses disk for anything that doesn't fit.

- **Files over 512 MB, or a folder of files:** use **Data Hub → Upload datasets → File
  or folder on this Mac** and enter the path. A folder becomes one table with a
  `source_file` column.
- **Excel with several sheets:** choose one sheet or all sheets. Rows repeated across
  sheets are reported and kept.
- **CSV with no header row:** turn on the no-header option and enter the column names.
- **Disk space:** an import checks for room first. Saving a project makes another full
  copy of its data, so a big project needs at least twice its size free.
- **First opening of a big table:** profiling reads every row, about 45 seconds at 50
  million rows, once per table version each time the app starts.

If the Mac feels short of memory while Qwen is also running, start the app from Terminal
with less memory for DuckDB:

```bash
INSIGHTPILOT_DUCKDB_MEMORY=6GB ./"Start InsightPilot.command"
```

Limits, measurements and details are in [Working with large tables](LARGE_DATA.md).

## Occasional safe update

Stop InsightPilot first. In Terminal:

```bash
cd ~/DA_project
git status --short
git branch --show-current
```

Confirm the branch is **`codex/insightpilot-local-ai`**. If a different branch is
selected, preserve your edits first, then use `git switch codex/insightpilot-local-ai`.
Do not pull this feature branch into `main`. When the working tree is safe:

```bash
git pull --ff-only origin codex/insightpilot-local-ai
```

This reuses the clone. It does not download models or reset datasets. If Git
reports divergence, conflicting edits or a failed fast-forward, stop and preserve
the files; do not use reset/clean/discard to force the update. For local source
edits you may keep a backup or use `git stash push --include-untracked` before the
pull, then `git stash apply` afterward. Applying keeps the stash backup; resolve
any conflicts before launching. Ignored `data/` and `.venv/` are not included by
that stash command and remain in place.

If the update changes `pyproject.toml` or `uv.lock`, or the launcher reports a
missing dependency, run **once**:

```bash
uv sync --locked --extra test
```

Then double-click Start again. This repair adds no new Python dependencies.
Ordinary startup never updates Git or synchronizes packages automatically.

## One-time fixes only when needed

- **Missing environment:** install [uv](https://docs.astral.sh/uv/getting-started/installation/)
  if needed; in `~/DA_project`, run `uv python install 3.12` and
  `uv sync --locked --python 3.12 --extra test` once. Existing working environments
  do not need to be deleted or recreated.
- **File lacks execute permission:** run `chmod +x "Start InsightPilot.command" "Check Local AI.command"`
  once. Git normally preserves the executable flag. If macOS asks whether to open
  a trusted command, use Finder's contextual **Open** action; do not disable system
  security globally.
- **Ollama missing/offline:** open your installed Ollama app. Only if it is not
  installed, use https://ollama.com/download/mac. Check `ollama list`; only if the
  model is genuinely absent, explicitly run `ollama pull qwen3.5:4b` yourself.
- **App cannot start:** read the Terminal error. The browser opens only after
  readiness. No existing listener is stopped to resolve a port conflict.

Your normal routine remains one double-click, not the setup or update commands.
