# Use the local AI upgrade on your Mac

This guide targets a 16 GB Apple Silicon Mac. Qwen3.5 4B and native macOS
performance **have not been executed in this Linux environment**. The engineering
and browser checks use explicit test doubles; your real-model acceptance remains
necessary. The application also works without Ollama.

## 1. Obtain this branch and start the application

The completed local upgrade is on `codex/insightpilot-local-ai`. It has **not been
pushed**. The published `codex/unified-insightpilot` branch at `e3874ee` is the
baseline, not this upgrade. A normal fetch currently cannot retrieve the new
branch. Transfer the upgraded checkout/archive, or import the accompanying bundle
if available. Do not reset or overwrite an existing checkout or its `data` folder.

For a Git bundle already downloaded to your Mac:

```bash
cd ~/Projects/DA_project  # replace with your existing checkout's location
git status --short       # preserve any local edits before switching branches
git bundle verify ~/Downloads/insightpilot-local-ai.bundle
git fetch ~/Downloads/insightpilot-local-ai.bundle codex/insightpilot-local-ai:codex/insightpilot-local-ai
git switch codex/insightpilot-local-ai
```

If you use an extracted source archive, open Terminal in that extracted project
folder instead. No Git fetch or branch switch is needed for an archive. Keep
your existing workspace files in their original checkout unless you deliberately
copy them; never replace them with evaluation fixtures.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if needed,
then run in the upgraded project folder:

```bash
uv python install 3.12
uv sync --locked --extra test
uv run streamlit run app.py --server.address 127.0.0.1
```

Open **http://localhost:8501** on your Mac. Leave Terminal running. Use Control-C
to stop the app. Standard analysis needs no AI account, model or API key. This
command uses the default workspace root `data/universal`; existing workspace
paths and datasets are preserved.

## 2. Install Ollama and explicitly download local weights

Download the current macOS Ollama application from
**https://ollama.com/download/mac**, install it and open it. Installation and
model downloading are your explicit actions; InsightPilot never performs them.
Use a current version supporting Qwen3.5 and JSON Schema output.

Disable Ollama's cloud features before using Local AI. For the macOS application:

```bash
launchctl setenv OLLAMA_NO_CLOUD 1
```

Quit Ollama completely and reopen it so it receives that setting. Repeat after
logging out if necessary. Alternatively, quit the Ollama app and start its
server in a separate Terminal with `OLLAMA_NO_CLOUD=1 ollama serve`. Use one
server instance; do not run a second server on the same port. Keep its listener
on the default loopback interface; no network exposure is needed.

In another Terminal, explicitly download the model and verify it:

```bash
ollama --version
ollama pull qwen3.5:4b
ollama list
ollama run qwen3.5:4b "Reply with the word ready."
curl --fail http://127.0.0.1:11434/api/tags
```

The first pull requires Internet access and storage. Download size is not peak
RAM. Close memory-heavy applications, select only one local model, and watch
Activity Monitor during initial loading and a full question. No speed or RAM
promise has been measured on your Mac. `ollama ps` shows loaded models;
`ollama stop qwen3.5:4b` unloads this model when you finish.

## 3. Ask a question and inspect the evidence

1. Select or create a workspace with a friendly name. Import your own file in
   **Data Hub**. Check the separator preview before importing a CSV. If it shows
   one field unexpectedly, choose the correct separator and review again.
2. In **Quality & Preparation**, review field roles, units, row grain and dates.
   For a numeric mean, confirm that field is actually a quantitative measure.
   For a trend, choose an actual reviewed date basis and shared grain.
3. Open **AI Analyst**, choose **Local AI Analyst**, click **Refresh local models**
   and select `qwen3.5:4b`. Confirm that the status shows the selected local model.
4. Start with `How many records are in this dataset?`. Then name an actual
   categorical and numeric field from your dataset. If those fields are `job`
   and `age`, ask `Which job categories have the most records, and what is their
   average age?`. These field names are examples, not required application data.
5. Inspect the result table, chart axes, definitions and **Analysis evidence**.
   Counts and means must match a direct calculation in SQL Lab or your reference
   tool. Shared filters remain in force.
6. Try `Now show only the top five.` then `And compare their average age.` The
   latter should keep the displayed groups. `Can you visualize that?` should
   reuse the validated intent. Rename your workspace; its data/history should
   remain. Change the table/filter/version; prior conversation should reset.
7. Ask `Which group is best?` or an undefined conversion rate. Expect a
   clarification. A new explicit conditional rate must show its numerator and
   eligible denominator for your approval before calculation. Never approve a
   guessed business definition.
8. Expand **Save this analysis to Reports** to save evidence and all charts.
   Question text is excluded unless you choose to include it. Replay the saved
   calculation without the model, and download a standalone report if wanted.

Choose **Standard analysis** at any time to use the finite deterministic
interpreter. SQL Lab, Explore and Reports remain available if Ollama stops.
**Start new conversation** clears the six-turn session history. There is no
automatic chat-history persistence and no cloud fallback.

## 4. Run the real-model benchmark

Use public benchmark inputs separately from your personal workspaces. Commands
run from the upgraded repository root:

```bash
uv run python scripts/acquire_unified_sources.py
uv run python scripts/reproduce_public_cases.py --download
uv run python scripts/benchmark_local_ai.py --mode ollama --model qwen3.5:4b --approve-benchmark-definitions --output outputs/local-ai-mac-real-model.json
```

Acquisition downloads documented public datasets; it does not upload your data.
The benchmark creates isolated evaluation workspaces under `outputs`. The
explicit `--approve-benchmark-definitions` flag approves only the fixed benchmark
rate definitions when the proposed definitions exactly match the reference. It
does not approve arbitrary model rates or your own business metrics. Without it,
approval-required cases remain pending and are reported. The report records
per-question plans/results/errors/timings, separate metric numerators and
denominators, and marks actual model quality only for real-model mode.

For engineering comparison only:

```bash
uv run pytest -q
uv run python scripts/benchmark_local_ai.py --mode integration --approve-benchmark-definitions --output outputs/local-ai-mac-integration.json
uv run python scripts/benchmark_local_ai.py --mode standard --approve-benchmark-definitions --output outputs/local-ai-mac-standard.json
```

Integration uses manually authored plans through an HTTP mock and cannot measure
Qwen's understanding. Standard is deliberately limited; its evaluation reports
unsupported wording and ordering differences. A benchmark failure exits 1;
unavailable real model exits 2 and records zero executed model cases. Do not
describe mocked scores as model accuracy.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Ollama unreachable | Open the Ollama app, test the curl command, keep endpoint `http://127.0.0.1:11434`, then refresh. The app cannot distinguish an uninstalled service from a stopped service over HTTP. |
| No models / model absent | Run `ollama list`; explicitly pull the tag above. Select installed local weights and refresh. Cloud models are excluded. |
| Model incompatible / HTTP failure | Update Ollama; check that `/api/show` advertises completion. Use the exact installed tag. The app will not fall back to a remote provider. |
| Timeout / memory pressure | First load may be slower. Close other apps, try 90 or 120 seconds, reduce selected fields/context, or choose a smaller compatible installed local model. Record its tag when comparing results. |
| Invalid output or unsupported operation | Shorten/restate the question, inspect roles and exact field names, and retry explicitly. No partial plan is executed and there is no automatic retry loop. |
| Context too large | Select fewer relevant fields, use shorter reviewed definitions and ask a smaller question. Source rows are never required in the prompt. |
| Wrong but valid interpretation | Inspect plan, filters and definitions; start a new conversation and restate the intended measure. Report the question and nonprivate schema plus evidence. Schema validation does not prove correct language understanding. |
| Port 8501 occupied | Stop the other app or add `--server.port 8502`; open `http://localhost:8502`. |
| Local model stopped | Choose Standard analysis or use SQL Lab. No paid account is needed. |

Personal acceptance: reconcile a count, compound count/mean, filtered count,
distribution and explicit denominator independently; check top-five follow-ups,
workspace isolation/rename persistence, saved replay and graceful stopped-model
behavior. Record model version, quantization, cold/warm timings and Activity
Monitor memory on your Mac. Real Qwen and Apple Silicon acceptance are still open.
