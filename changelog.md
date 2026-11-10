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

