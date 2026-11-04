#!/bin/bash
# Explicit real-model acceptance; never download or start a model installer.
INSIGHTPILOT_PROJECT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)" || exit 1
exec /bin/bash "$INSIGHTPILOT_PROJECT/Start InsightPilot.command" --check-local-ai "$@"
