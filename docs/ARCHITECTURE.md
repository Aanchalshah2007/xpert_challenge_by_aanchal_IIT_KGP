# Architecture v2 (post data review)

## Pipeline (linear, no agents)
1. ingest      — load jsonl, sort per chat, normalise text, tag kinds ([IMG] caption, [DOC] name, [VOICE] transcript).
                 Reference "now" = last message ts. Loop window = last 16 days.
2. profile     — deterministic per-chat/per-person features: 1:1 vs group, volume, ME share, active hours,
                 address-term and role lexicon hits (kinship, staff, vendor, client, colleague), co-membership.
3. orgs        — ONE LLM call: all group chat names + member-overlap clusters + seed org names
                 → 5–8 orgs (type/side/role) + group→org map. Always include Home (residence). Fallback: rules.
4. people      — batched LLM over 1:1 chats only (compact cards ≤ ~800 tokens: head/tail + lexicon-hit snippets)
                 → circle + confidence. Seed circles pinned. Group-only people: rule-based from org cluster
                 (own-company groups → colleague). Links: colleague/partner → runs company; staff → supports home.
                 looks_after from staff ↔ family co-mentions.
5. chat ties   — groups → org; 1:1 → person (→ org only if LLM is high-confidence it's purely org business).
6. loops       — (a) rules flag candidates in the last 16 days: incoming requests/questions to ME
                 ("pls", "can you", "possible to", "?", pay/send/confirm/decide) + ME commitments ("will", "I'll", "let me", "kar dunga").
                 (b) group nearby candidates into windows (context before + forward slice after).
                 (c) batched LLM: confirm real loops, pick the EARLIEST raising message as source_msg,
                     owner, type, due, P(open/done/dropped) + closing msg, help features.
                 (d) dedup: one loop per source_msg; merge same-task loops within a chat.
7. decide      — status: done/dropped only if P > 2·P(open). help (open loops only):
                 assist only if owner=ME, private, reversible, not sensitive, and P(assist) > 2·P(leave);
                 remind if only Arjun can act and P(remind) > 2·P(leave); else leave (with reason).
8. goals       — keyword retrieval over ME messages → ONE LLM call → ≤8 goals, each with 2–5 evidence ids.
                 Verify ids exist and are Arjun's or about Arjun.
9. assemble    — build output, check.validate(), write. Fallback to last valid stage on error.

## Layout (stdlib only)
run.sh → python3 -m coldstart.main
coldstart/{main,ingest,profile,orgs,people,chats,loops,decide,goals,assemble,util}.py
coldstart/lexicons.py        generic multilingual patterns only
coldstart/prompts/*.txt
llm.py (starter, wrapped with retry/timeout/concurrency via threads)
eval/{run_eval.py, help_goal_score.py, anonymise.py}
scripts/leak_check.py

## LLM client
ThreadPoolExecutor (concurrency 8), 60s timeout, 3 retries with backoff, temperature 0,
response_format json_object when supported. Disk cache keyed by prompt hash (dev only, off for cost runs).

## Cost target
Hidden ≈ 15k msgs. Budget ≈ 250–350k total tokens (~20k tokens / 1k msgs).
Largest cost = loops on the last 16 days. Benchmark 2–3 cheap models on dev; pick the cheapest within ~2 points of the best.

## Evaluation
- check.py for circles/orgs/chats/loops.
- eval/help_goal_score.py against hand labels in data/dev/labels/ (help + goals), using the same rules as check.py.
- anonymise.py: name-swap dev; score must hold within ~2 points.
- Ablations: forward-slice size, loop window days, status/help thresholds, model choice.

## Phases
1 skeleton + ingest + profile + rules-only orgs/people/chats + eval harness + leak check
2 loops (biggest points) + decide
3 LLM orgs + people
4 goals
5 tuning, cost trimming, name-swap test; run the pipeline once under a real Python 3.9 (docker python:3.9-slim) before submission
6 WRITEUP.md ($ per 1k msgs on dev, where it's wrong, honest error rates)