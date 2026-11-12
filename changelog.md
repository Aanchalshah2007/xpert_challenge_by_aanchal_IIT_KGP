# Changelog

Pipeline changes with their measured effect. Offline = re-scored on cached LLM answers, zero calls.
Dev loop key is noisy (system-generated, one source per loop); small deltas are indicative only.

## Phase 2 — loop logic fixes (zero-call analysis, 2026-11-11)

Baseline for these entries: cached small-batch Gemini answers (gemini-3.5-flash), default batching,
47 windows (24 key loops) and the 41-window sub-sample (19 key loops) used in model_comparison.md.

### 1. Source shift: context line → the explicit ask (`loops.shift_sources`, `config.SHIFT_*`)
After the LLM, a loop whose source has no request/commitment cue (only time or money, or none) moves to the same
sender's next message that has a request or imperative cue, within 3 messages and 10 minutes.
Measured with `eval/offline_rules.py` on cached answers, zero calls:

| Set-up (41-window sample, 19 key loops) | F1 before → after | Matched | Wrong "done" |
|---|---|---|---|
| Gemini small | 0.12 → 0.18 | 2 → 3 | 0 → 0 |
| Groq gpt-oss-120b | 0.11 → 0.17 | 4 → 6 | 0 → 0 |
| Groq gpt-oss-20b | 0.14 → 0.17 | 5 → 6 | 0 → 0 |

On all 47 windows: 120b 0.13 → 0.18, 20b 0.20 → 0.23. Every move went from a context line to the ask. No
accepted source was lost, and the number of proposed loops is unchanged. Test: `tests/test_patterns.ContextThenAsk`.

### 2. Media after a payment request = closure evidence (`loops.media_closure`, `config.MEDIA_CLOSURE_*`)
If the user's own media-only message follows a payment request within 10 messages and 48 h, with no new request
from the counterpart in between, 50% of p_open moves to p_done and `close` is set. Status still goes through
the done-only-if-P(done) > 2·P(open) rule.
**Measured effect: none.** It fires after only 1 of 306 incoming payment requests in dev, and on 0 of the 13
key loops sourced on a payment request (11 of those are "done" in the key, closed by text). No status changed in
any set-up, and it never contradicts the key. Kept as approved; a candidate for removal if it stays inert on more data.
Test: `tests/test_patterns.MediaClosure`.

### 3. No user name in prompts or fields (`needs_me_only`)
The help feature that came from the Phase 2 spec carried the user's first name. It is renamed `needs_me_only`
in the prompt, `loops.py`, `decide.py` and the tests. The eval label-sheet text says "the user (ME)". No behaviour
change: replaying the cached answers gives identical scores. The v1 prompt is archived verbatim in
`eval/prompt_archive/loops_system_v1.txt` (eval only; the leak check scans `coldstart/`). The eval replay tools
key their cache lookups on it and map the old field name, so the model comparison stays reproducible.
`scripts/leak_check.py`: OK.

### 4. Prompt v2 + window cap + runtime name (built, NOT yet run: waits for the model choice)
- **c, source wording:** src = "the message that makes the task concrete and actionable"; context or
  announcement lines before it are not the src; repeated tasks keep the first concrete message.
- **d1, bursts:** the prompt says to judge every `*` line and that each separate request is its own loop.
  Windows hold at most 8 candidates (`WINDOW_MAX_CANDIDATES`). Pieces of a split stretch share lean context
  (3 messages before / 8 after; the stretch's last piece keeps 20 after).
  Zero-call effect: windows 165 → 296; messages sent 5,401 → 6,692 (+24%; with full context per piece it
  was +64%); max candidates per window 46 → 8; all 74 key sources still inside a window.
- **d2, runtime name:** `ingest._me_names` reads the user's display name from ME's own `sender_name` (full name
  plus first name). It is skipped if blank or generic ("You", "Me", …), and no name is hardcoded. Incoming
  messages that use it get the candidate reason `addressed`: 26 on dev, 4 of them key sources. Strict candidate
  recall stays at 67/74. The prompt header says how ME is addressed. With no usable name, the header is
  byte-identical to v1.
- **d3, delegations:** the user's own "please ask / pls check with …" are loops **owned by ME** (type followup),
  so help can be remind.
- **Replay tools** (`batch_compare`, `offline_rules`, `source_analysis`) force v1 settings (`bc.v1_settings`) and
  the archived v1 prompt, so the v1 numbers stay reproducible (verified identical). The comparison sample is
  pinned as message ids in `eval/runs/sample_v1.json`, so v2 can be scored on the same messages.
- Tests: `tests/test_patterns.py` (bursts, name-addressed + generic-name fallback, delegations,
  context-then-ask, media closure). 52/52 pass on Linux and Windows. Leak check OK.


## Zero-spend plan (2026-11-11)

### 5. Fixed loop evaluation slice (`eval/make_slice.py` → `eval/runs/slice_v1.json`, seed 11, reproducible)
58 positive windows (every window holding a key-loop source, open and done), 26 hard negatives (v1 false
positives from cached answers), 10 seeded random negatives (rule candidates, no key loop): 94 windows, 1,840
messages, 74/74 key loops. 27 hard-negative sources were proposed by ≥2 of 3 v1 set-ups and look like real asks
the noisy key misses; they are listed in the file. Estimate (Groq 120b): v2 28 calls ~99k tokens; filling v1
for the same slice 26 calls ~85k.

### 6. Lossless compact loop prompt (`config.PROMPT_COMPACT`)
One `-- Mon 24 Aug` line per day, `HH:MM` per message, whitespace collapsed; one repeated instruction removed
from the system prompt. `eval/format_check.py`: all 48 full-dev prompts parse back line by line to the v1 content
(id, cue mark, date, time, sender, text); 63/63 key source ids and all their window context present. Prompt
chars −9.5%. v1 replay keeps the old format (`bc.v1_settings`), scores identical. Accuracy NOT yet measured:
the next v2 run tests v2 + compact format together.

### 7. Goals stage wired (`coldstart/goals.py`, one call)
120b on dev: 32 keyword-filtered ME messages → 8 goals. The model cites one id per goal although the prompt asks
for 2–5; requiring ≥2 dropped all 8, so a goal now needs ≥1 verified own-message id. Against the 3 hand labels:
F1 0.364 (2 of 3 labels found, 8 proposed) → 2.91/8. Adding keyword-related evidence ids did not change it and
was removed. Caveat: the label sheet used a similar keyword filter, so filter recall (7/7 ids) is optimistic.

### 8. Circles LLM pass (`coldstart/circles.py`, seed circles pinned)
One card per unpinned person with a 1:1 chat (68 on dev): saved name, size, shared group names, kin words,
10 messages (cue-bearing first). Prompt clarifies company employees = colleague, household help = staff.
Cards the model skips are re-asked once. A generic "family" answer keeps the rules' kin circle, else relative.
120b, 5 calls + 1 re-ask, ~18k tokens: circles 12/41 (3.51/12, rules) → 20/41 (5.85/12) at the preset
`CIRCLES_MIN_CONF` 0.6 (0.0: 5.56, 0.8: 4.98).

### 9. Reliability
`llm.py` logs rate-limit headers, the limit/used part of 429 messages and the provider code of 400/413/422.
Groq's daily limits refill continuously (no fixed reset). `eval/quota_ledger.py --tpd` shows a 24 h window and
a refill estimate. 20b integration run on `data/dev_small` (21 chats, 889 messages): valid output, 483 s,
21 calls, 69.5k tokens, 25 failed attempts (24 TPM 429 waits, one 400 that switched JSON mode off).
15/20 loop replies were near-empty; `eval/slice_run.py` stops early if that repeats.

### 10. 20b near-empty diagnosis, JSON-mode fallback, goal evidence, v1 reuse (zero calls)
- 20b replies were not cached (integration run), so diagnosed from usage.jsonl + rebuilt prompts: all 20 loops
  calls finished "stop" (no truncation); near-empty ones used 4 reasoning tokens (reasoning did not eat
  max_tokens). 11 of the 15 were one news-forward 1:1 chat with no key loops (empty is correct). 4 held 6 of the 9
  key loops in dev_small and returned nothing: 20b weakness or JSON mode off (it was off for 19 of 20 calls);
  not separable without calls. No cached 120b v1 answers overlap dev_small (0%).
- JSON mode: a 400 now drops it for that call only (retried once without); it goes off for the run only after
  3 such 400s in a row, logged. Tests added.
- Goals: up to 4 more of ME's candidate messages sharing ≥2 content words with the goal are added as evidence.
  Dev: 1 of 8 goals gained 3 ids (1 is label evidence); 2/3 labels, 8 proposed, precision 0.25, F1 0.364 before
  and after.
- v1 on slice_v1: cached v1 answers reused per window (33 of 70 pieces) → 20 calls ~65k tokens instead of
  26 calls ~85k (exact-call cache) — ~20k saved.

### 11. Pre-registered prompt decision rule (fixed 2026-11-10 ~21:00 UTC, before any v2 slice result)
On slice_v1, Groq gpt-oss-120b, same scoring (shift + media + merge + decide, eval/slice_run.py):
1. choose the prompt with the higher loop F1;
2. if |ΔF1| < 0.03, choose the one with fewer wrong "done" on open loops;
3. if still tied, choose v2.
The report names the rule that decided it.

### 12. Slice decision: v1 (rule 1, higher F1)
slice_v1 (94 windows, 1,840 messages, 74 key loops), Groq gpt-oss-120b, reasoning effort low:

| | proposed | matched | F1 | status right | wrong done | calls / tokens |
|---|---|---|---|---|---|---|
| v1 + shift | 105 | 26 | **0.291** | 16 | 0 | 20 new + 33 pieces reused; 52.3k in + 11.8k out |
| v2 (compact) | 141 | 24 | 0.223 | 10 | 2 | 28; 84.4k in + 34.0k out |

ΔF1 = 0.068 ≥ 0.03, so **rule 1** decided; v1 also has fewer wrong "done" (0 vs 2). One v1 call (call 7) timed
out and was not retried, so v1 is scored without ~2 pieces; that can only change its numbers slightly and not the
decision. v2 had one call cut off at max_tokens (JSON repaired).
Pipeline switched to the tested v1 set-up: v1 prompt (only change: the name-bearing feature field is now
`needs_me_only`), no window candidate cap, v1 line format, no runtime-name cue (`LOOPS_USE_ME_NAME=False`).
Verified zero-call: same 165 dev windows and byte-identical user prompts as the v1 replay settings. v2 prompt
archived in `eval/prompt_archive/loops_system_v2.txt`; v2 options stay in config and tests. Eval tools pin their
settings explicitly (`bc.v1_settings`, `bc.v2_settings`); slice_v1 still reproduces (same md5).
Cost re-projected with v1's measured ~337 output tokens per window: dev ~$0.015, hidden ~$0.006 per 1K messages.

### 13. Organisers' help/source guidance, tested batch size, frozen config (zero calls)
- **Help (decide.py) follows the organisers' "whose move is it" guidance** and replaces the 2x-leave ratio rule.
  Someone else's move -> leave, unless the user is waiting on it (followup, or a question the user sent) and it
  is overdue -> remind. The user's move (a promise, a decision, an ask to the user) -> assist when every safety
  gate passes and there is a concrete next step, else remind. **Overdue** = the due date (stated or clearly
  implied, resolved by the model) is before today in IST; a loop with no due date is never overdue.
  Offline on cached v1 slice replies vs the 25 hand labels (15 found open): 2.4 -> 3.6 of 10 (right 8 -> 11,
  acted where the label says leave 2 -> 2). On v2's cached replies: 1.2 -> 1.2. Caveat: two refinements
  (waiting-on-other for questions the user sent; no age-based "overdue") were chosen after looking at the
  mismatches on these same labels, so 3.6 is optimistic.
- **Loop prompt: one sentence added**, "If unsure, choose the concrete message.", after the src rule, following
  the organisers' source guidance. Not slice-tested. Nothing else in the prompt changed.
- **Batching set to what slice_v1 tested for v1**: 7,000-char calls packed by size only (no 10-window cap),
  windows over the budget cut into pieces with 6 overlapping messages (moved from eval into `loops.split`),
  max_tokens 2000. Circles keep their measured 16,000-char budget, uncapped. Note: 33 of v1's 70 slice pieces were
  answered in the earlier 16,000-char v1 batches, 37 in 7,000-char ones.
- **Bug fixed:** `slice_run.py --prompt v1` saved its result to `eval/runs/slice_v1.json`, overwriting the slice
  (after reading it, so scores were unaffected). Results now go to `slice_result_<prompt>.json`; the slice was
  regenerated (`make_slice.py --negatives 10 --seed 11 --write`) and both runs replay from cache to the same
  scores (v1 F1 0.291, v2 F1 0.223), so its content is unchanged. The changelog-12 "same md5" claim was wrong.
- Frozen settings: `eval/frozen_config.py` (saved to eval/runs/frozen_config.json; `--check` detects drift).


## Session 2: paid-run safeguards (2026-11-11, zero paid calls)

### 14. Housekeeping
- The Groq full-dev harness (eval/full_run.py) was not running (last log 21:54 IST, 21/91 calls). Its replies stay in
  `.llm_cache/`. Pipeline caching via COLDSTART_LLM_CACHE now uses one sub-folder per provider host, so Groq replies are
  never reused for OpenRouter (same model id). Eval replay tools pass explicit cache dirs and are unaffected.
- Docker (python:3.9-slim, Python 3.9.25, LLM vars unset): `./run.sh data/dev` -> valid output; check.py 12.5/56
  (circles 3.5, orgs 1.8, chats 7.1, loops 0). Run via `docker.exe` (WSL socket needs the docker group).
- `.env.openrouter` has CRLF endings: `source` would put `\r` into the URL, key and model id. `llm.py` now strips
  whitespace from the three variables; `eval/with_env.sh` loads env files safely. The .env files are unchanged.

### 15. Spend ledger + hard cap (`coldstart/spend.py`, `eval/spend.py`) — dev only
Active only when COLDSTART_SPEND_LEDGER is set (never in the scoring run; request body unchanged). Before each call,
reserve a worst-case cost (prompt chars/2.5 tokens at $0.5/M, max_tokens or 8,000 at $1.5/M — above the dearest
eligible endpoint) and refuse if ledger + in-flight + reserve > cap (default $3.00, clamped to $4.00 in code) or >
COLDSTART_RUN_BUDGET. After each call, one ledger line with OpenRouter's own `usage.cost` (USD; returned on every
response per the usage-accounting docs; `usage.include` is deprecated), provider, generation id, prompt / completion /
reasoning / cached tokens; failed attempts logged with no cost. `eval/spend.py reconcile` adds GET /generation
(provider_name, total_cost, native reasoning tokens); `eval/spend.py key` gives the key's own usage (USD) as a
cross-check. Key at session start: usage $0, limit $4.00 (not $4.50). Tests: tests/test_spend.py (9).

### 16. OpenRouter provider routing (`config.LLM_EXTRA_BY_HOST`)
`provider: {require_parameters: true, allow_fallbacks: true, data_collection: "deny"}` added to every OpenRouter
request. Docs: require_parameters = only providers supporting all request parameters; allow_fallbacks default true;
data_collection deny = only providers that don't collect user data; no documented conflict with response_format,
reasoning, max_tokens or temperature. `eval/route_check.py` (public endpoints, zero cost), gpt-oss-120b:
none 26 endpoints / 24 live / 20 providers; require_parameters 21 / 19 / 16 (5 lack response_format);
data_collection deny >= 26 / 24 / 20 (all on the ZDR list, which is stricter); both 21 / 19 / 16. gpt-oss-20b both:
8 / 8 / 8. Adopted: many providers stay eligible. Freeze re-saved with this change.

### 17. Run 1: full dev, frozen config, OpenRouter gpt-oss-120b, fresh calls, real ./run.sh (2026-11-11 ~22:45 IST)
- First attempt: HTTP 404 on the first two calls, $0 spent. Cause: `.env.openrouter` names `openai/gpt-oss-120b:free`
  (left from the route check); the free variant is gone. Not our routing: 5 tiny probes (`eval/route_probe.py`,
  $0.00004) succeed with the paid slug under every provider setting. Paid runs now pass LLM_MODEL explicitly.
  `llm.py` now logs the provider message of a 404 too. Ledger lines now carry a prompt hash (per-provider analysis).
- Result (out: eval/runs/out_run1.json): **20.9/56** vs baseline 17.5 — circles 6.1 (4.7), orgs 1.8 (1.4),
  chats 7.1 (6.0), loops found 3.3 (2.0), closed right 2.5 (3.4). Help 2.0/10 on the 25 hand labels (offline
  estimate was an optimistic 3.6); goals 2.9/8 on 3 labels.
- 93 calls (circles 2, goals 1, loops 90 incl. re-asks), 0 failed, 2 loops replies cut at max_tokens.
  Tokens: in 274,499 (34,732 cached), out 49,534 (15,309 reasoning). **Actual $0.02477** (usage.cost) =
  generation total_cost $0.02477; key usage $0.024808 = run + probes. **$0.00550 per 1K messages** (4,502 msgs).
  Stages: loops $0.02318, circles $0.00147, goals $0.00011. Wall 114 s.
- 11 providers served it (Crusoe 30, AkashML 22, Sail Research 18, PrimeIntellect 12, DekaLLM 5, 6 others 1 each);
  each charged its listed endpoint price (ratio 1.00). Effective blended $/M tokens: 0.04–0.20.
- Fixed: eval/help_goal_score.py crashed printing goals (key name typo `n_goal_labels`).

### 18. Truncation: split and retry instead of re-ask/repair (`loops._ask_splitting`, `config.TRUNCATION_SPLITS = 2`)
The client now exposes each call's finish_reason (saved next to cached replies as `<key>.meta.json`; a cached reply
without it counts as cut when it isn't valid JSON and doesn't end like JSON). A loops reply cut at max_tokens is no
longer re-asked (the re-ask repeats the prompt plus 2,000 cut tokens and can be cut again); its batch is split in two
(or one window into two overlapping pieces) and both halves are asked, up to 2 levels; at the last level the
repaired part is kept. Run 1 had 2 cut calls (both re-asked successfully; they recovered 13 loops vs repair-only).
`eval/replay.py` replays pre-change caches with the old re-ask path. Tests: tests/test_truncation.py (3).
This changes the baseline for Task 2: the replicates (rep2, rep3) include it; run 1 does not.

### 19. Zero-call analyses of run 1 (`eval/replay.py`, `eval/analyse_status_help.py`, `eval/ablate_decide.py`)
`eval/replay.py` rebuilds a run's raw loops from its saved replies; on run 1 it reproduces all 135 loops exactly.
- **Status:** 74 key loops (49 done, 25 open), 25 matched. Matched: open→open 10, done→done 3, done→open 12, no wrong
  "done". p_done is bimodal (0.1 for 92 of our loops, 0.8–0.9 for 16); matched loops at p_done 0.0–0.3 are done in
  the key 12/22 times (0.55), at ≥0.7 3/3. Decision rule: calling done pays 2q−1 vs 1−q for open → done iff
  q > 2/3, i.e. P(done) > 2·P(open). The 0.55 bin stays below 2/3, so r = 2.0 is right, and r = 1.5 / 1.0 / 0.5
  change nothing on run 1 (Δ 0.00). The loss is closure detection, not the threshold: in 6 of the 12 done→open
  misses the chat ends 1–8 messages after the source (no visible closure at all; key closing_msgs is empty for all
  49 done loops, so it can't be checked). Dev spans 13.7 days ≈ the hidden 14-day scored window, but the dev key is
  system-generated with 66% done; the hidden key is hand-labelled, so its done share is uncertain.
- **Help (2.0 vs the 3.6 estimate):** 14 of 25 labels not found (0 points each, whatever the rule); 11 found:
  right 6, zero 4, −1 once. The 3.6 was on Groq slice replies where 15 labelled loops were found open, and two rules
  were chosen on those labels. Misses: #3 (a request to the user typed followup with another owner → leave; label
  assist), #8 (needs_me_only gate → remind; label assist), #11 (all gates pass → assist; label remind), #18 (private
  gate → remind; label assist), #19 (followup with owner ME → assist; label leave: someone else is handling it).
  Candidate fixes: f2 (a followup is someone else's move, per the prompt's own definition "ME asks someone else and
  is waiting on it" and the organisers' "someone else owes it → leave, remind if waiting and overdue"); f1 (drop
  the private gate: assist always needs the user's yes; weaker rationale). Run 1: f2 +0.8 [0.0, +2.4], 6/118
  actions change; f1 +0.4 [0.0, +1.2], 1/118. Both chosen after looking at these labels: optimistic.
- **Listed price:** openai/gpt-oss-120b on OpenRouter (models API = model page, 2026-11-11 22:38 IST):
  $0.15/M input, $0.60/M output ($0.075/M cached read). Run 1 at listed price: $0.0709 = **$0.01575 per 1K msgs**
  (9.81 of 12 cost points before the quality share), vs $0.0055 as routed. Loops are 95% of it. Dev's 13.7 days all
  fall in the 16-day loop window, so dev is the worst case per 1K messages: on a 17K-message hidden set,
  loops run only on the last 16 days. Projection (dev's loop $ per window message, circles x1–x2): 2K window
  messages → $0.0020/1K; 4.5K → $0.0042/1K; 8K → $0.0074/1K; break-even with the $0.0095 line at ~10.6K messages
  in the last 16 days (62% of the set). The organisers' baseline ($0.038/1K, ~490K tokens) is consistent with this
  listed price.

### 20. Ablation flags (dev only, `COLDSTART_ABLATE`, default empty; recorded in frozen_config)
burst (window cap 8 + "each separate request is its own loop"), delegate (v2 delegation line replaces the v1
followup line), me_name (runtime name cue), orgs_llm (one call, `coldstart/orgs_llm.py`, tests/test_orgs_llm.py),
status_r1.5 / status_r1.0, help_private (f1), help_followup (f2). Prompt hash unchanged with no flags.
`eval/paid_run.sh` runs one paid full-dev run (fresh cache, ledger, run budget, total cap).

### 21. Task 2 ablations (OpenRouter gpt-oss-120b, full dev, 2026-11-11 23:00–23:40 IST; $0.2586 spent)
Baseline = frozen + truncation split (rep2, rep3; fresh). Arms of 2 fresh runs each. (d) and (e)/(f) are paired on
saved replies. Two-level bootstrap (`eval/compare_runs.py`: runs within arm + key loops / labels, paired items).
Points = loops found /14 + closed right /14 + help /10.

| arm | runs | found | closed | help | sum | Δ sum [95% CI] | data total | listed $/1K | actual $/1K |
|---|---|---|---|---|---|---|---|---|---|
| base (frozen + split) | 2 | 3.56 | 3.41 | 2.40 | 9.37 | — | 21.25 | 0.0162 | 0.0065 |
| run 1 (frozen, re-ask) | 1 | 3.35 | 2.46 | 2.00 | 7.81 | −1.56 [−4.79, +1.65] | 20.90 | 0.0158 | 0.0055 |
| a burst | 2 | 3.27 | 3.12 | 2.40 | 8.79 | −0.58 [−3.99, +2.74] | 21.04 | 0.0239 | 0.0095 |
| b delegations | 2 | 3.57 | 3.12 | 2.20 | 8.89 | −0.48 [−3.24, +2.49] | 21.50 | 0.0160 | 0.0063 |
| c runtime name | 2 | 3.33 | 2.93 | 3.00 | 9.26 | −0.11 [−3.33, +3.19] | 20.91 | 0.0165 | 0.0064 |
| d orgs LLM (paired) | 2 | 3.56 | 3.41 | 2.40 | 9.37 | 0.00 (orgs 1.7→1.7, 1.8→1.8) | 21.25 | +1 call | +$0.0003 |

- None of a–d wins; all point estimates ≤ 0 and every CI spans ~±3 points. Not adopted. (a) costs +48% tokens.
  (d): the model reproduced the rules' grouping exactly (renamed the social sink, moved 0 groups). No interaction
  test: there are no winners to combine.
- (e)/(f), paired on saved replies of all 9 fresh runs (`eval/ablate_decide.py`, each run replayed with its flags):
  e r=1.5 / r=1.0: Δ 0.00 closed, Δ 0.00 help (1 loop changes status in 1–2 runs, unscored) → keep r = 2.0.
  f1 (no private gate): Δ help 0.00 [−0.13, +0.13] → reject. f2 (followup = someone else's move): Δ help +0.22
  [−0.13, +0.62] (help /10), closed unchanged; changes 2–6 of ~130 actions per frozen-prompt run (20–25 with the
  delegation prompt). f2 can only turn remind/assist into leave, which never costs −1.
- Noise floor: the frozen config over 3 runs: data total 20.9 / 21.4 / 21.1; goals 2.9 / 5.3 / 2.9; help 2.0 / 2.8 / 2.0.
- Per provider (`eval/provider_quality.py`, 8 runs pooled): recall 0.36–0.38 and F1 0.18–0.26 for the five main
  providers (Crusoe 388 prompts, Sail Research 160, AkashML 98, PrimeIntellect 81, Groq 46); no provider stands out.
- Spend: ledger $0.2834 = key usage $0.28337. Task 2: $0.2586 of the approved $0.50.

### 22. Adopted f2: a followup is the other person's move (`config.HELP_FOLLOWUP_THEIRS = True`)
decide.help_for: an open loop of type followup ("ME asked someone else and is waiting on it", the prompt's own
definition) is someone else's move even if the model set owner = ME: leave, or remind when it is overdue
(organisers: "someone else owes it and is handling it → leave; remind if the user is waiting and it's overdue").
Evidence (changelog 21): help +0.22 [−0.13, +0.62] of 10 over 9 fresh runs; closed and found unchanged.
**Caveats:** it was found by looking at mismatches on the same 25 hand labels it is scored on (optimistic), and its CI
includes 0. It only moves actions toward "leave" (remind/assist → leave), which can never score −1.
`no_help_followup` (COLDSTART_ABLATE) turns it off. Test updated: tests/test_patterns.Delegations. Freeze re-saved.

### 23. Precision sweep on saved replies, 9 fresh runs (`eval/precision_sweep.py`, zero calls) — nothing adopted
Rule (from the user): adopt only if the total (found + closed + help) improves in EVERY run.

| setting | loops | sum | Δ [95% CI] | runs better / worse |
|---|---|---|---|---|
| frozen (conf ≥ 0.5, title Jaccard 0.5) | 151 | 9.16 | — | — |
| conf ≥ 0.6 / 0.7 / 0.75 | 151 | 9.16 | +0.00 | 1–2 / 0 |
| conf ≥ 0.8 | 150 | 9.10 | −0.06 [−0.35, +0.03] | 5 / 1 |
| conf ≥ 0.85 | 145 | 8.94 | −0.22 [−0.79, +0.12] | 6 / 3 |
| conf ≥ 0.9 | 101 | 7.63 | −1.53 [−3.05, −0.22] | 2 / 7 |
| title Jaccard 0.35 | 149 | 9.03 | −0.12 [−0.47, +0.04] | 6 / 2 |
| title Jaccard 0.25 | 139 | 7.85 | −1.31 [−2.48, −0.44] | 0 / 9 |
| merge same chat+owner within 30 min | 135 | 8.04 | −1.12 [−2.26, −0.21] | 0 / 9 |
| ... within 120 min | 124 | 7.23 | −1.93 [−3.42, −0.74] | 0 / 9 |

The model's confidence barely separates true from "false" loops (almost all are ≥ 0.8), and the key keeps nearby
requests as separate loops, so merging loses matches. Consistent with the earlier finding that many unmatched loops
look like real asks the noisy key misses.

### 24. 3-call consensus for goals and circles (`eval/consensus.py`, zero calls: 9 fresh runs = 9 samples, 84 triples)
Circles/goals prompts are identical across the loop ablations, so each fresh run is an independent sample.
- Circles (/12): single mean 5.72, SD 0.31 → consensus 5.76, SD 0.23; 26 of 286 people change circle across runs.
  Variance down, score not down → **adopt** (rule from the user). Added cost: 2 more circles call-sets per run =
  +$0.0066 per dev run at the listed price = +$0.0015 per 1K dev messages (hidden: ~+$0.0008 per 1K).
- Goals (/8, 3 hand labels): single mean 3.18, SD 0.76 → consensus 2.99, SD 0.18. Variance down 4x but the mean is
  0.19 lower (the single mean is lifted by one run of nine scoring 5.33 vs 2.91) → **not adopted** under the rule.

### 25. Task 3: medium reasoning at max_tokens 2,000 is invalid (med1, med2 stopped)
med1 (120b, reasoning medium, otherwise frozen): 57 of 133 loops calls cut at 2,000 tokens with ~1,750 reasoning
tokens each; 114 of 186 replies failed even after splitting → loops found 1.3, closed 0.8, data total 16.9; $0.0720
actual (148K reasoning tokens). med2 was stopped mid-run after $0.0396 (wasted; incomplete, not scored). Medium is
retested with COLDSTART_MAX_TOKENS=6000 (med6k_1, med6k_2) so the comparison is about reasoning, not the cap.

### 26. Circles consensus implemented, then NOT adopted (`circles.vote`, `extract_consensus`, flag circles_consensus)
Implemented as 3 passes with identical prompts (a sample index enters only the dev cache key; the request is
unchanged) and a majority vote on the model's labels before people.build. Checked on saved replies of 9 runs
(`eval/consensus_check.py`, 84 triples): single 5.72 (SD 0.31) → consensus 5.69 (SD 0.22). The earlier +0.04 voted
on final circles; voting on raw labels comes out −0.03. Score falls (slightly) → not adopted under the rule;
CIRCLES_SAMPLES = 1 (frozen). Tests: tests/test_circles_vote.py (3).

### 27. Task 3 frontier runs (OpenRouter, fresh calls except where noted)
- **Run-budget sizing error:** med6k_1 and med6k_2 (medium, max_tokens 6,000) stopped after ~50 calls each
  ($0.0233, $0.0235): with max_tokens 6,000 each in-flight call reserves ~$0.011, so 8 in flight (~$0.088) hit the
  $0.10 run budget early. The cap behaved as designed; the budget was sized wrong. **med6k_c** completed the run
  from the union of both partial caches (53 replies reused) plus 46 fresh calls at concurrency 3 ($0.0342).
- Results (help re-decided with f2 for every arm; `eval/compare_runs.py`):

| arm | runs | found | closed | help | loops+help | Δ [95% CI] | data total | listed $/1K dev | actual $/1K dev |
|---|---|---|---|---|---|---|---|---|---|
| 120b low (frozen) | 2 | 3.56 | 3.41 | 2.60 | 9.57 | — | 21.25 | 0.0162 | 0.0065 |
| 120b low (+run 1) | 3 | 3.49 | 3.09 | 2.67 | 9.25 | −0.32 [−2.44, +1.67] | 21.13 | 0.0162 | 0.0062 |
| 120b medium, max_tokens 6,000 | 1 | 3.15 | 4.35 | 3.60 | 11.10 | +1.53 [−1.81, +4.76] | 21.71 | 0.0314 | 0.0137 |
| 20b low | 2 | 3.04 | 2.36 | 2.00 | 7.41 | −2.16 [−5.63, +1.46] | 19.34 | 0.0035 | 0.0049 |

  Medium's complete-run cost: union of the 99 distinct calls actually used, in 279K / out 165K tokens, $0.0616 actual,
  $0.1411 at the listed price. 120b medium at max_tokens 2,000 (med1): invalid, see 25.
- Spend: ledger $0.5214; Task 3 $0.2366 of $0.30 (med1 0.0720, med2 0.0396 wasted, oss20 0.0441, med6k 0.0468,
  med6k_c 0.0342).

### 28. Pre-registered medium decision → FREEZE LOW (2026-11-12 00:18 IST; `eval/decide_medium.py`)
Rule fixed by the user before med_r2/med_r3 ran. Per-call timeout now max(120, max_tokens/40 + 30) s (180 s at
6,000; unchanged 120 s at 2,000), so a full 6,000-token reply at the slowest provider seen (44 tok/s) fits.
med_r2: data 22.2, loops+help 10.82, $0.0561, 270 s; med_r3: data 21.1, loops+help 9.51, $0.0549 (approx.), 495 s.

| condition | result | |
|---|---|---|
| 1. medium mean − low mean ≥ +1.0 (loops+help) | 10.48 − 9.57 = **+0.91** | **FAIL** |
| 2. no medium run below low mean − 1.5 | worst 9.51 vs floor 8.07 | pass |
| 3. truncated/failed < 2% of calls | 12/347 = **3.5%** (fresh conc-8 runs alone: 7/198 = 3.5%) | **FAIL** |
| 4. projected hidden wall time < 15 min | 11.7 min (6.5K window msgs, conc 8) | pass |
| 5. projected hidden $/1K ≤ 0.015 at listed price | 0.0144 (circles + goals x2) | pass |

Decided by conditions 1 and 3. Frozen config = 120b, reasoning low, max_tokens 2,000, f2 on, truncation split,
single-pass circles, no ablations (`eval/frozen_config.py --check`: unchanged). The medium flag stays (dev only).
Experiment spend at freeze: ledger $0.6390 (cap $3.00); the $0.80 final reserve untouched.

### 29. Freeze, final run, validation, analysis, write-up, log redaction (2026-11-12 00:20–02:15 IST)
- **Final fresh run (frozen config, real ./run.sh):** check.py 20.5/56 (circles 5.6, orgs 1.8, chats 7.1, found 3.4,
  closed 2.7), help 3.2/10, goals 2.9/8; 97 calls, 0 failed, 275.5K in / 58.4K out; $0.0276 actual ($0.0061/1K
  routed), $0.0764 at the listed price = $0.0170/1K dev; 185 s. Frozen config unchanged before and after.
- **Name swap (data/dev_anon, fresh run, frozen config):** 19.2/56 vs 20.5 ($0.0311): inside the noise floor.
- **Docker python:3.9-slim:** LLM off valid (12.5); full LLM path replayed offline from the final run's cache with
  `--network none`: valid, byte-identical to the final output, 0 calls.
- **eval/analysis.py → eval/runs/analysis.md + pareto.svg** (zero calls): component CIs, run spread, noise floor,
  error profile, decision theory, final vs run 1 (+1.39 [−1.91, +4.44]), generalisation, cost story with hidden
  sensitivity (full cost points up to ~9.5K messages in the last 16 days), frontier with expected points.
- **WRITEUP.md** rewritten on the template's four sections (noise floor as a result; cost sensitivity table).
- **scripts/redact_logs.py** (zero calls): data/raw_logs/ (gitignored via data/) → ai-logs/, replacing dataset
  excerpts (full, JSON-escaped, and 40-char prefixes), API keys (env values + formats), emails, phone numbers,
  dataset numbers, URLs, password values, chat names, names, person ids; never overwrites a file it didn't write.
  `scripts/leak_check.py` now also scans ai-logs/ (values hidden in its report; numbers matched as whole digit runs,
  so timestamps don't false-alarm). It FAILS on the pre-existing `ai-logs/session-1.md` (57 hits: 51 sender names, 4
  seed-org words, 1 chat name, 1 number) — not created this session, left untouched. Pipeline files: clean.
- A dev sender name in changelog.md (a quote of the organisers' guidance) and in CLAUDE.md replaced by "the user".
- **Spend:** experiments $0.6390 (cap $3.00); final + name-swap validation $0.0587 from the $0.80 reserve.

### 30. Overfitting checks (report only, zero calls; nothing reverted)
- `eval/split_half.py`: dev split at its time midpoint; each adopted change undone alone on the saved replies of
  final, rep2, rep3. Every Δ (with − without) is ≥ 0 in both halves; no change helps in one half and hurts in the
  other. Source shift: found +0.07 / +0.23, closed 0 / +0.33, help 0 / +0.20. Ownership help rule (vs the git-HEAD
  2x-ratio rule): +0.83 / +0.78. f2: 0 / +0.20. Truncation split (vs repair-only): found +0.24 / +0.05,
  closed +0.30 / +0.33, help +0.83 / +0.59. Week 1 has only 8 of 25 help labels.
- `eval/holdout.py make`: data/dev/labels/holdout_sheet.md (gitignored) with 10 open loops of the final run that are
  not among / accepted with the 25 labelled sources, one per chat (4 group chats), seed 23, ±3 messages context,
  no system action shown. `eval/holdout.py score` scores it once filled in (check.py's help rule, out of 10).
- analysis.md section 8 "Overfitting checks" (eval/analysis.py) and one WRITEUP line; hold-out score pending.

### 31. Shared name matcher, re-redaction, blind help hold-out (2026-11-12; zero calls; nothing reverted)
- **scripts/name_match.py:** the one dataset-term matcher used by both leak_check.py and redact_logs.py
  (case-insensitive; full names, chat titles and seed org names as token sequences with any separator; first/last
  name tokens of 4+ letters as whole words; one-word titles of 6+ letters anywhere, as before; 5+ digit numbers as
  whole runs). The old redactor skipped names that are also words in the repo's generic text (the organisers' README
  contains the dev user's first name) — that mismatch caused the 66 hits. Allow-list (leak_allow.txt): the dev
  user's first name alone (user decision: it is in the organisers' public README), and two group titles that are
  plain kinship words. Names come from real data folders only (the name-swapped copy's invented 3-letter names
  collide with ordinary words). Check: checker terms missing from the redactor = 0.
- **New log rules (both scripts):** 32+ hex characters, Bearer / X-API-Key values, values assigned to
  *API_KEY* / *SECRET* / *TOKEN (not TOKENS) variables, every URL, e-mails. Found and removed: an LLM_API_KEY value
  left in claude-ai-chat.md and one in session-1.md. Password rule no longer fires on "password-like".
- Re-redaction of data/raw_logs → ai-logs: api keys 2, urls 45, emails 1, excerpts 2, sender names 9, seed org
  names 4, 1:1 chat name 1, number 1, person ids 9. **Leak check: OK** (1,524 terms; no secret/url/email pattern left).
- **Blind help hold-out** (`eval/holdout.py score`; 10 labels suggested by a separate AI from context only, blind to
  the system's actions): **0.0/10** (right 3, −1 6: the system acts where the label says leave). Same replies under
  other rules: without f2 0.0 (net −2), earlier 2x-ratio rule 3.0, always leave 6.0. Ownership − earlier rule,
  unclipped per 10: hold-out −6.0 [−13.0, 0.0]; 25 labels +0.8 / +1.2 / +0.4 (final, rep2, rep3); pooled 35 −1.1
  [−3.4, +1.1]. Label mix: hold-out 6/10 leave vs 7/25. Not reverted: the evidence conflicts; decision left to the user.

### 32. Stronger ai-logs checks: n-gram excerpts, quoted short messages, user paths, term review (zero calls)
- **scripts/excerpt_match.py** (shared by leak_check.py and redact_logs.py, like name_match.py):
  any run of 5+ consecutive words also found in a dev body (case, punctuation and whitespace ignored, so partial
  and line-wrapped quotes match; overlapping matches merge); whole 2–4 word bodies inside quotation marks; the
  local user's folder name in C:\Users\<name>\, /mnt/c/Users/<name>/, /home/<user>/ and the dash-encoded
  -Users-<name>- form, read from the repo path / home (never hard-coded). The export wraps long lines, splitting
  the folder name across a line break, so the path patterns allow a wrap inside the name.
- Report-only first (current ai-logs/): n-gram spans session-1 3 (20 words), others 0; quoted short bodies
  session-1 4; user-path names claude-ai-chat 1, session-1 93, session-2 42; e-mails 0. Leak check: 143 hits.
- Re-redaction: 5+ word excerpts 3, quoted short 4, user paths 133 (overlaps merged), e-mails 1 (plus the earlier
  categories unchanged). After: n-gram 0, quoted 0, user paths 0 in every file. **Leak check: OK.**
- **scripts/term_review.py** → data/raw_logs/term_review.md (gitignored; terms and counts only, no context):
  45 candidate in-text names (words in dev bodies, capitalised there or not ordinary English, also in ai-logs;
  ordinary English from an offline stdlib + man-page vocabulary of 123K words, our code's words, public brands and
  known names excluded). Nothing redacted automatically; awaiting the user's decision.

### 33. WRITEUP.md: adaptive behaviour + future work; trimmed to 889 words (no code changes, freeze unchanged)
Added at the end of "My approach": the adaptive behaviour already in the pipeline (truncation split-and-retry,
JSON-mode fallback, retry/backoff with adaptive concurrency, probability-gated status and ownership/safety-gated
help, OpenRouter provider fallback) and future work (escalate only uncertain loops to medium reasoning; learn help
preferences from the user's corrections). To fit 900 words: the ablation list is condensed (details stay in
analysis.md and changelog 21–28), the error bullets shortened, and the item-CI goals row dropped from the table.

### 34. Final checks before commit (2026-11-12; no tuning; freeze unchanged)
- **A. Help-rule comparison** (no pre-registered conditions found in any session file, so none applied; zero calls,
  final run): ownership (frozen) 25: 3.2 [1.2, 5.2], hold-out 10: 0.0 [0.0, 3.0], pooled 35: 1.4 [0.0, 3.7];
  2x ratio 2.4 / 3.0 / 2.6 (Δ pooled +1.1 [−1.1, +3.4]); both-agree (act only when both rules give the same
  action) 3.2 / 2.0 / 2.9 (Δ pooled +1.4 [−0.3, +3.4]). Freeze unchanged pending the user's conditions.
- **B1.** `.gitattributes`: *.sh, *.py, *.md, *.json text eol=lf. Only ai-logs/README.md had CRLF; converted (text
  identical apart from line endings, checked by hash). run.sh is LF.
- **B2.** Final output: 139 open loops all with help; 33 assist all with one-line next_step + needs_yes; 65 remind
  all with remind_at; 41 leave all with reason ("someone else's move", the only leave path); 21 done all with
  closed_by. looks_after: 0 people (no logic produces it). Latent: a done loop with no close id from the model would
  get closed_by null (schema allows it; did not occur). Nothing changed.
- **B3.** Voice transcripts use the same candidate rules ([VOICE] tag stripped; dev has no voice messages).
  The candidate lexicons have no Devanagari or Bengali terms: only "?" matches; dev's 19 Devanagari / 3 Bengali
  messages in the loop window give 2 / 0 candidates. Report only.
- **B4.** Scoring-path network: only llm.py's POST {LLM_BASE_URL}/chat/completions. No price/models/generation
  lookups in run.sh, coldstart/ or llm.py; eval/route_check.py and eval/spend.py are dev tools not imported by the
  pipeline; the spend ledger is off unless COLDSTART_SPEND_LEDGER is set.
- **B5.** WRITEUP: a person's group is always tied to an organisation (dev: 0 of 40 scored groups tied to a person).
  WRITEUP is 900 words.

### 35. Pre-commit safety check (no git commands: status computed from .git/index + .gitignore)
- Would commit 67 files: 21 modified tracked + 46 new (incl. .gitattributes, ai-logs/ redacted transcripts,
  docs/SUBMISSION.md, new coldstart/eval/scripts/tests files). Ignored: data/ (incl. raw_logs, labels, hold-out
  sheet), .env*, .llm_cache/, eval/runs/, usage.jsonl, output.json, __pycache__, *.zip, *.tmp; out.json added.
- Leak scan of all 67 files (names/titles/numbers, 5-word dataset overlap, secrets, e-mails, user paths): fixed a
  verbatim 5-word dev phrase in a test fixture (tests/test_patterns.py, amount changed; tests pass) and the user's
  folder name in a path in docs/SUBMISSION.md ([USER]). Remaining hits are coincidences: an ordinary English word,
  a generic 5-digit constant and a public brand that equal dataset tokens; code like `TOKEN = re.compile(...)`;
  "seed/home/family" read as a /home/ path.

### 36. Help rule = "both agree" (adopted); leave reasons; Devanagari/Bengali cue words (zero calls)
- **Decision rule (fixed by the user in advance, in a prompt that hadn't reached this session):** highest pooled
  score over the 35 labels (25 + 10 hold-out); if two are within 0.5, the more conservative one (fewer
  assist/remind). Pooled: both agree 2.9, 2x 2.6, ownership 1.4 (changelog 34). Both agree is highest; 2x is within
  0.5 and the tie-break favours both agree (more conservative by construction). **Adopted.**
- `decide.help_for` = both agree: `ownership_help` (the "whose move" rule, incl. f2) and `ratio_help` (the earlier
  rule: act only if P(action) > 2·P(leave)) must give the same action, else leave. `config.HELP_RULE`
  ("help_ownership" ablation restores the ownership rule alone), `config.HELP_RATIO = 2.0`. Replayed on the final
  run: 25 labels 3.2, hold-out 2.0, pooled 2.9 (reproduces the comparison); other components unchanged.
  **The hold-out was used for this choice, so it is no longer an independent test** (stated in analysis.md, WRITEUP).
- **Leave reasons** (no scoring change): ownership says someone else's → "someone else's to handle"; the rules
  disagree on a sensitive loop → "too personal"; otherwise the probability gate overruled acting → "a reminder
  would likely annoy". Final run (replayed): 41 / 1 / 19; actions remind 54, leave 61, assist 24.
- **Multilingual cue words** (`lexicons.REQUEST_INDIC`, `COMMIT_INDIC`, `QUESTION_INDIC`): generic Hindi
  (Devanagari) and Bengali request / commitment / question words (please, send, do, give, tell me, pay, will do /
  send, when, what, how much, ...), no names. Script-aware boundaries (Python's \w misses the scripts' vowel signs,
  so the old boundary would match a short word inside a longer one). Dev: candidates 1,808 → 1,810 (+2, both
  Devanagari, both in group chats C64 and C12: one question, one commitment), 1 existing candidate gains a reason;
  both new candidates were already inside existing windows as context → windows 186, calls 88 and prompt size
  unchanged (2 lines newly marked as cues). Not retuned otherwise.
- Tests: tests/test_help_both_agree.py (6), tests/test_multilingual.py (4); ownership tests now call
  `decide.ownership_help`. Freeze re-saved (help rule fields + a lexicons hash). eval/holdout.py score takes an
  output path; analysis.md uses the final run's replies re-decided with the frozen rule (out_final_bothagree.json).
- WRITEUP.md: help wording matches the code; the hold-out caveat; 894 words.
