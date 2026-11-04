#!/bin/bash
# Everyday startup uses the installed environment; never sync, pull or install.
set -u
INSIGHTPILOT_PROJECT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)" || exit 1
cd "$INSIGHTPILOT_PROJECT" || exit 1
INSIGHTPILOT_PYTHON="$INSIGHTPILOT_PROJECT/.venv/bin/python"
if [ ! -x "$INSIGHTPILOT_PYTHON" ]; then
    printf '\nInsightPilot cannot find its existing Python environment.\n'
    printf 'Open Terminal in this project and run once:\n  cd %q\n  uv sync --locked --extra test\n' "$INSIGHTPILOT_PROJECT"
    INSIGHTPILOT_EXIT=1
else
    "$INSIGHTPILOT_PYTHON" "$INSIGHTPILOT_PROJECT/scripts/launch_insightpilot.py" "$@"
    INSIGHTPILOT_EXIT=$?
fi
if [ "$INSIGHTPILOT_EXIT" -ne 0 ] && [ -t 0 ]; then
    printf '\nPress Return to close this window. '
    read -r INSIGHTPILOT_REPLY
fi
exit "$INSIGHTPILOT_EXIT"
