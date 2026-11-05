# InsightPilot on your Mac

Keep using your existing **`~/DA_project`** clone and its installed environment.
No new clone, ZIP, Python installation or model download is required for everyday
use. The published baseline is `b9451ca`; this descriptive-analysis repair remains
local until a separately authorized push. See the [unreviewed workflow and real-Mac
checks](LOCAL_AI_DESCRIPTIVE_ANALYSIS.md).

## Start tomorrow

In Finder, open **DA_project** and double-click **Start InsightPilot.command**.
Leave its Terminal open; the browser opens when the local server is ready.
Press Control-C in that Terminal to stop. Saved datasets and workspaces remain.

Double-click **Check Local AI.command** to exercise the actual installed Qwen
through InsightPilot with independent DuckDB references and isolated synthetic
data. Existing personal datasets are untouched. Real Qwen and native Finder were
unavailable in the Linux development environment; Mac acceptance is still needed.

## Installation only if something is missing

If the existing application already works, skip these setup steps. For a missing
environment, install [uv](https://docs.astral.sh/uv/getting-started/installation/)
if needed, then run once:

```bash
cd ~/DA_project
uv python install 3.12
uv sync --locked --python 3.12 --extra test
```

Reuse your installed Ollama and `qwen3.5:4b`. Only if genuinely absent, install
Ollama from https://ollama.com/download/mac and explicitly pull the model yourself.
The launcher never installs software or downloads weights.

## Occasional updates

After the repair is published, stop InsightPilot, inspect `git status --short`,
confirm the current branch is `codex/insightpilot-local-ai`, and preserve local
edits before running:

```bash
cd ~/DA_project
git pull --ff-only origin codex/insightpilot-local-ai
```

Do not pull the feature branch into `main`, discard local work or reset data.
Only a genuine dependency change requires an explicit `uv sync --locked` once.

The complete [everyday-use guide](MAC_EVERYDAY_USE.md) explains cloud-disabled
Ollama startup, executable permissions, safe updates with local edits, persistence,
real-model acceptance reports and troubleshooting.
