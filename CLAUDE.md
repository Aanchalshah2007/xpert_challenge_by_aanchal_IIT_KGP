# Xpert Cold Start Challenge — Project Rules

## Context
Pipeline that reads one person's (Arjun, sender id "ME") WhatsApp export and writes output.json:
people/circles, organisations, chat ties, goals, open loops (+status, +help).
Design: docs/ARCHITECTURE.md. If the code or data contradicts it, flag the conflict — don't silently deviate.
Official rules: README.md and check.py. check.py is the source of truth for scoring.

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
- In dev, most loop sources are messages FROM the other person (requests/questions to Arjun), mostly in 1:1 chats.
- Status: calling an open loop done/dropped = −1; calling a done loop open = 0 → default "open"; "done" only if P(done) > 2·P(open).
- Help: assist/remind where the key says leave = −1; leave where it says assist = 0 → default "leave"; act only if P(action) > 2·P(leave).
- Organisations: Jaccard pairing ÷ max(#ours, #theirs) → keep orgs FEW; cluster group chats (one community org can hold 30+ groups).
  Org membership = tied chats + people links. Links (colleague/partner → runs, staff → supports) are part of the org score; orgs with no
  chats (workspace, vacation home) can only be matched through people links. Never emit links to orgs we're unsure of.
- Chats: every chat tied once. Groups → organisation. 1:1 → person by default; 1:1 → org only when high-confidence. 56 of 138 dev chats are unscored.
- Goals: match = same area + ≥1 shared evidence id. Use several evidence ids per goal. Max 10.
- Help and goals are not labelled in the dev key: unmeasurable on dev without hand labels (data/dev/labels/, eval/ only).
- Circles: only people Arjun placed are scored; seeded people are excluded. Label everyone anyway.
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