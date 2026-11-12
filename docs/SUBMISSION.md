# Submission checklist

## 1. Final defaults (what runs when only LLM_BASE_URL / LLM_API_KEY / LLM_MODEL are set)

| Setting | Value | Where |
|---|---|---|
| Model to name on the form | `openai/gpt-oss-120b` | organisers set `LLM_MODEL` |
| Reasoning effort | low: `{"reasoning": {"effort": "low"}}` on openrouter.ai | `config.LLM_EXTRA_BY_HOST` |
| Loop prompt | v1 (chosen on slice_v1 by rule 1): `coldstart/prompts/loops_system.txt`, `PROMPT_COMPACT=False`, `WINDOW_MAX_CANDIDATES=10**6`, `LOOPS_USE_ME_NAME=False` | `config.py` |
| Concurrency | 8 (adapts down 8 → 4 → 2 on overload errors) | `config.LLM_CONCURRENCY` |
| Per-call timeout / retries | 120 s / 4 retries with jitter; honours provider retry hints | `config.LLM_TIMEOUT_S`, `llm_client` |
| Batching | ≤10 windows per call, ~16,000 prompt chars (the slice runs used 7,000) | `WINDOWS_PER_CALL`, `PROMPT_CHAR_BUDGET` |
| max_tokens | not set (a cap lets reasoning eat the reply: seen on OpenRouter) | `LOOPS_MAX_TOKENS = None` |
| Watchdog | 25 min, last valid output stays on disk | `COLDSTART_DEADLINE_S` (unset = 1500) |
| Status / help thresholds | done only if P > 2·P(open); act only if P > 2·P(leave) | `STATUS_RATIO`, `HELP_RATIO` |
| Circles confidence | 0.6 | `CIRCLES_MIN_CONF` |

Done: the code is switched to v1 (changelog 12); tests (57) and the leak check pass. v2 is archived in
`eval/prompt_archive/loops_system_v2.txt`.

Dev-only variables must be **unset** in any final test: `COLDSTART_CHAR_BUDGET`, `COLDSTART_MAX_TOKENS`,
`COLDSTART_CONCURRENCY`, `COLDSTART_LLM_CACHE`, `COLDSTART_STATS`, `COLDSTART_DEADLINE_S`.

## 2. Fresh-clone test (you run it after committing)

```bash
git clone <repo> /tmp/cs && cd /tmp/cs
cp -r <path-to>/data ./data                 # data is never committed
./run.sh example out.json && python3 check.py example out.json          # no LLM vars: rules-only, valid
python3 -m unittest discover -s tests -q
python3 scripts/leak_check.py
# Python 3.9, nothing installed (Phase 5):
docker run --rm -v "$PWD":/w -w /w python:3.9-slim ./run.sh data/dev out.json
docker run --rm -v "$PWD":/w -w /w python:3.9-slim python3 check.py data/dev out.json
# With an LLM (one free-tier run, or a paid run only if you choose to spend):
env -i PATH="$PATH" HOME="$HOME" LLM_BASE_URL=... LLM_API_KEY=... LLM_MODEL=openai/gpt-oss-120b \
  bash -c 'time ./run.sh data/dev out.json' && python3 check.py data/dev out.json
```
Pass criteria: exit 0, `final: valid` in the log, `check.py` prints scores, wall time well under 30 min,
`usage.jsonl` shows only the named model.

## 3. Must NOT be in the repo

Covered by `.gitignore`: `data/` (incl. `data/dev_small/`, `data/dev_anon/`, `labels/`), `*.zip`,
`usage.jsonl`, `output.json`, `.env`, `.env.*`, `__pycache__/`, `eval/runs/` (outputs, slices, stats),
`.llm_cache/` (cached replies quote real messages), `*.tmp`.
Not ignored, so check by hand:
- `out.json` or other outputs written outside `eval/runs/`. Only `output.json` is ignored.
- The dev user's first name appears in `CLAUDE.md`, `docs/ARCHITECTURE.md` and
  `eval/prompt_archive/loops_system_v1.txt`. These aren't pipeline files, but if the no-names rule should cover the
  whole repo, edit them before committing (`scripts/leak_check.py` scans only `coldstart/`, `run.sh`, `llm.py`).
- No keys anywhere: `git grep -nE "gsk_|sk-or-|AIza"` should return nothing.

Check before pushing (read-only): `git status --short`, `git check-ignore -v data .env .env.groq .llm_cache eval/runs usage.jsonl`.

## 4. Exporting ai-logs

- Claude Code keeps each session as JSONL under
  `~/.claude/projects/-mnt-c-Users-[USER]-OneDrive-Desktop-aanchal-xpert-challenge/` (one file per
  session). Copy them into `ai-logs/`, or run `/export` inside a session to save a readable transcript.
- Before committing them: `grep -lE "gsk_|sk-or-|AIza" ai-logs/*` must find nothing. Commands only ever
  sourced the .env files and never printed keys, but check anyway.
- The transcripts quote dev messages (names, numbers). The README asks for transcripts as they are; decide
  whether to redact. The dev data itself must stay out of the repo.
- Add other AI tools' exports (ChatGPT etc.) the same way.
