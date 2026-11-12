#!/usr/bin/env bash
# One paid full-dev run through the real ./run.sh path, under the spend ledger, with a fresh cache folder.
#   eval/paid_run.sh <run_id> "<COLDSTART_ABLATE flags, comma-separated or empty>" <run budget USD> <total cap USD> [seed_run]
# MODEL=... overrides the model (default openai/gpt-oss-120b); DATA=... the data folder (default data/dev).
# seed_run: start from a copy of that run's cache, so only calls whose prompt changed are new (paired ablations).
set -euo pipefail
cd "$(dirname "$0")/.."
id=$1; abl=$2; budget=$3; cap=$4; seedrun=${5:-}
if [ -e ".llm_cache/$id" ]; then echo "refusing: .llm_cache/$id exists (runs must be fresh)"; exit 1; fi
if [ -n "$seedrun" ]; then cp -r ".llm_cache/$seedrun" ".llm_cache/$id"; fi
start=$(date +%s)
COLDSTART_ABLATE="$abl" COLDSTART_SPEND_LEDGER=eval/runs/spend.jsonl COLDSTART_RUN_ID="$id" \
COLDSTART_RUN_BUDGET="$budget" COLDSTART_SPEND_CAP="$cap" COLDSTART_LLM_CACHE=".llm_cache/$id" \
COLDSTART_STATS="eval/runs/stats_$id.json" \
  eval/with_env.sh .env.openrouter env LLM_MODEL="${MODEL:-openai/gpt-oss-120b}" ./run.sh "${DATA:-data/dev}" "eval/runs/out_$id.json" \
  > "eval/runs/$id.log" 2>&1
echo "== $id (ablate: ${abl:-none}) wall $(( $(date +%s) - start ))s"
grep -E "cut at max_tokens|spend cap|run budget|given up|calls failed" "eval/runs/$id.log" | sort | uniq -c | head -5 || true
python3 check.py "${DATA:-data/dev}" "eval/runs/out_$id.json" | grep -E "Circles|Organ|Chats|found|closed|Total"
python3 eval/help_goal_score.py "${DATA:-data/dev}" "eval/runs/out_$id.json"
python3 eval/spend.py summary --run "$id" | sed -n 2,4p
