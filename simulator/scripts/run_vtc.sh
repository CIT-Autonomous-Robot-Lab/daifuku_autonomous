#!/usr/bin/env bash
# Entry point for the usim VTC mapping -> navigation workflow.
#
#   USIM_ROOT=/path/to/usim bash simulator/scripts/run_vtc.sh [--engine podman|docker] [...]
#
# All options are run_vtc.py's (--help). USIM_ROOT (or --usim-root) is required;
# a sibling usim checkout is never guessed. The verdict is <run-dir>/result.json.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
simulator=$(dirname "$here")
exec uv run --project "$simulator" --no-sync python -m daifuku_sim.vtc.run_vtc "$@"
