#!/usr/bin/env bash
# The one command we run:  ./run.sh <input_dir> <output.json>
# Change what it calls, not how it is called. It must finish in 30 minutes.
set -euo pipefail
if [ "$#" -ne 2 ]; then
  echo "usage: ./run.sh <input_dir> <output.json>" >&2
  exit 2
fi
PYTHONPATH="$(cd "$(dirname "$0")" && pwd)${PYTHONPATH:+:$PYTHONPATH}" python3 -m coldstart.main "$1" "$2"
