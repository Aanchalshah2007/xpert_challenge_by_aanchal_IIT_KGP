#!/usr/bin/env bash
# Run a command with the LLM variables from an env file, tolerating CRLF line endings and indentation.
#   eval/with_env.sh .env.openrouter <command...>
# Never prints the values. The pipeline itself reads only the environment.
set -euo pipefail
f="$1"; shift
while IFS= read -r line || [ -n "$line" ]; do
  line="${line%$'\r'}"; line="${line#"${line%%[![:space:]]*}"}"
  case "$line" in ''|'#'*) continue ;; esac
  k="${line%%=*}"; v="${line#*=}"; v="${v%\"}"; v="${v#\"}"
  export "$k=$v"
done < "$f"
exec "$@"
