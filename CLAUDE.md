# Xpert Cold Start Challenge — Project Rules

## Context
Pipeline that reads one person's (sender id "ME") WhatsApp export and writes output.json:
people/circles, organisations, chat ties, goals, open loops (+status, +help).
Design: docs/ARCHITECTURE.md. If the code or data contradicts it, flag the conflict — don't silently deviate.
Official rules: README.md and check.py. check.py is the source of truth for scoring.

## Current state (updated 2026-11-11 22:30 IST; keep this section short and current)
- Deadline 12 Nov 23:59 IST; experiment freeze 12 Nov 14:00 IST (skip anything that can't finish before it).
- Scoring: organisers run our code once on a hidden ~17K-message set (names changed) via OpenRouter, model
  openai/gpt-oss-120b, passing through our request fields. Cost points: full 12 at <= $0.0095 per 1K messages, 0 at
  >= $0.152, log scale between, times our quality share. Dev spend doesn't count. Baseline 17.5/56, $0.038/1K msgs.
- Hidden set: loops scored only from midnight IST 14 days before data end; ~50 voice transcripts; quoted_id null.
  Several source messages can be accepted; if unsure, choose the concrete one. Help: an action on EVERY open loop.
- Frozen config: `python3 eval/frozen_config.py --check` (prompt v1 + source shift + "If unsure, choose the concrete
  message"; reasoning low; loop batches 7,000 chars, 6-message overlap, max_tokens 2,000; circles 16,000 chars, conf
  0.6; concurrency 8; OpenRouter provider routing require_parameters + allow_fallbacks + data_collection deny).
- Paid runs (OpenRouter): load the key with `eval/with_env.sh .env.openrouter ...` (the file has CRLF endings; plain
  `source` breaks it). Set COLDSTART_SPEND_LEDGER=eval/runs/spend.jsonl, COLDSTART_RUN_ID, COLDSTART_RUN_BUDGET.
  coldstart/spend.py: hard cap $4.00 (code), experiments stop at $3.00 (COLDSTART_SPEND_CAP), $0.80 reserved for the
  final run. Show the estimate and get approval before each paid run. `eval/spend.py summary|key|reconcile`.
  .env.openrouter names the retired `:free` slug: add `env LLM_MODEL=openai/gpt-oss-120b` after with_env.sh.
- Run 1 (frozen, OpenRouter): 20.9/56, $0.0248 = $0.0055 per 1K msgs, 93 calls, 114 s (changelog 17).
- Task 2 (changelog 21): ablations a-d no gain; e (threshold) no effect; f2 (followup = their move) +0.22 help, CI
  includes 0 -> adopted (changelog 22). Listed price $0.15/$0.60 per M -> dev $0.016/1K (worst case; hidden ~$0.002-0.007).
  Spend $0.2834. Tools: eval/replay.py, compare_runs.py, ablate_decide.py, provider_quality.py, paid_run.sh.
- LLM cache: COLDSTART_LLM_CACHE gets one sub-folder per provider host; legacy Groq/Gemini replies stay in .llm_cache/.
- Docker: use `docker.exe` (WSL user isn't in the docker group). python:3.9-slim run with LLM off: valid, 12.5/56.
- Tests: `python3 -m unittest discover tests`; leak check `python3 scripts/leak_check.py`. Log every change in changelog.md.
- FROZEN (changelog 28-29): 120b low, f2 on, truncation split; medium failed the pre-registered rule. Final run 20.5/56,
  $0.0170/1K dev at listed price. analysis: eval/runs/analysis.md (eval/analysis.py). WRITEUP.md done.
  ai-logs/session-1.md is unredacted and fails the leak check: move it to data/raw_logs/, run scripts/redact_logs.py.

## Hard constraints
- Invoked as: ./run.sh <input_dir> <output.json>. Must finish < 30 min; internal watchdog at 25 min.
- Python 3.9+ STANDARD LIBRARY ONLY. Clean Linux machine, nothing installed. No pip packages.
- LLM calls only via llm.py (reads LLM_BASE_URL, LLM_API_KEY, LLM_MODEL; logs usage.jsonl). No other network access.
- Never commit data/, answers, usage.jsonl, .env or output.json.
- output.json must ALWAYS be written and must pass check.validate(). Write a valid skeleton first, overwrite per stage.

## Scoring facts (from check.py — design around these)
- Loop "found" = our source_msg ∈ the key's accepted source_msgs. Only source_msg, status and help.action are scored.
- Hidden set: only loops sourced after loops_scored_from (≈ last 14 days) count, on both sides. Our earlier loops are ignored, not penalised,
  so the ~16-day extraction window is a cost decision. The key side uses its EARLIEST accepted source: picking a LATER message than the
  accepted source is a false positive → always pick the earliest raising message.
- In dev, most loop sources are messages FROM the other person (requests/questions to the user), mostly in 1:1 chats.
- Status: calling an open loop done/dropped = −1; calling a done loop open = 0 → default "open"; "done" only if P(done) > 2·P(open).
- Help: assist/remind where the key says leave = −1; leave where it says assist = 0 → default "leave"; act only if P(action) > 2·P(leave).
- Organisations: Jaccard pairing ÷ max(#ours, #theirs) → keep orgs FEW; cluster group chats (one community org can hold 30+ groups).
  Org membership = tied chats + people links. Links (colleague/partner → runs, staff → supports) are part of the org score; orgs with no
  chats (workspace, vacation home) can only be matched through people links. Never emit links to orgs we're unsure of.
- Chats: every chat tied once. Groups → organisation. 1:1 → person by default; 1:1 → org only when high-confidence. 56 of 138 dev chats are unscored.
- Goals: match = same area + ≥1 shared evidence id. Use several evidence ids per goal. Max 10.
- Help and goals are not labelled in the dev key: unmeasurable on dev without hand labels (data/dev/labels/, eval/ only).
- Circles: only people the user placed are scored; seeded people are excluded. Label everyone anyway.
- Dev loop key is system-generated and noisy (duplicates, one chat dominates). Hidden set is hand-labelled. Don't tune to dev quirks.

## Generalisation (non-negotiable)
- Hidden set has different names. NEVER put any person/org/place name, number or chat title from data/ into code, prompts or config.
- Only eval/ may read answers/ or labels/. Nothing in the pipeline imports or reads them.
- scripts/leak_check.py must pass before any commit.

## Engineering style
- Deterministic first; LLM only where context is needed. No agent frameworks.
- Batch LLM calls; never one call per message. Concise prompts, short local ids, JSON-only replies.
- Every LLM output is parsed defensively: strip fences → json.loads → light repair → one re-ask → deterministic fallback.
- Every predicted item keeps message-id evidence; drop items whose ids don't exist.
- Track tokens per stage from usage.jsonl; report tokens per 1k messages. Never hardcode prices.
- Small modules, type hints, no speculative features.

## Workflow
- Stop at each checkpoint and wait for my go-ahead.
- NEVER run git add, commit, push or any other git write command. The user commits manually. Read-only git (status, diff, log) is fine.
- LLM variables live in .env (gitignored). For commands that need the LLM, prefix `set -a; source .env; set +a;`.
  Never print, log or echo the key. The pipeline itself must only read environment variables, never .env.
- After any pipeline change: run ./run.sh data/dev out.json && python3 check.py data/dev out.json, and report the score delta plus token delta.

Overfitting guard: after the medium-vs-low decision, no more tuning on dev. In WRITEUP.md and analysis.md:
- list every variant tested and which were adopted (with the reason each was kept or rejected);
- mark dev scores that are optimistic and why: help (rules found on the same 25 labels), goals (3 labels),
  the chosen config (selected among ~15 variants; expect regression toward the mean);
- give hidden-set expectations below the dev numbers for those components, with a short justification.