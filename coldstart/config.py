"""Tunable knobs in one place (loops + decide). Change here, re-run eval/run_eval.py."""
import os


def _env_int(name: str, default):
    """Dev-only override (e.g. a free tier with a small tokens-per-minute limit); unset in scoring runs."""
    v = os.environ.get(name, "").strip()
    return int(v) if v.isdigit() else default


# ---- ablations (dev only; empty in scoring runs): COLDSTART_ABLATE=burst,delegate,me_name,orgs_llm,
# status_r1.5,status_r1.0,help_private,no_help_followup,reasoning_medium. Each switches one change on; frozen_config records the set.
ABLATE = frozenset(x.strip() for x in os.environ.get("COLDSTART_ABLATE", "").split(",") if x.strip())

# ---- LLM client
# Provider-specific body fields, chosen by the host in LLM_BASE_URL (reasoning effort is set differently per gateway).
LLM_EXTRA_BY_HOST = {
    # provider: only endpoints that support every field we send (response_format, reasoning, max_tokens, temperature),
    # fall back to another such endpoint on errors, and none that collect prompts. eval/route_check.py counts what
    # stays eligible (2026-11-11: 21 of 26 endpoints, 16 providers live).
    "openrouter.ai": {"reasoning": {"effort": "medium" if "reasoning_medium" in ABLATE else "low"},
                      "provider": {"require_parameters": True, "allow_fallbacks": True, "data_collection": "deny"}},
    "api.groq.com": {"reasoning_effort": "low"},
}
LLM_CONCURRENCY = _env_int("COLDSTART_CONCURRENCY", 8)

# ---- circles (circles.py): the LLM's circle replaces the rules' guess at or above this confidence
CIRCLES_MIN_CONF = 0.6
CIRCLES_SAMPLES = 3 if "circles_consensus" in ABLATE else 1  # 3 = majority vote over passes; not adopted (changelog 26)
CIRCLES_CHAR_BUDGET = 16000  # cards per call, as measured (5.85/12 on dev); completion uncapped

# ---- candidate windows
WINDOW_BEFORE = 6  # context messages before the first candidate
WINDOW_AFTER = 20  # forward slice after the last candidate (closures, replies)
WINDOW_JOIN_GAP = 20  # candidates this close share a window (= WINDOW_AFTER, so slices don't overlap)
WINDOW_MAX_CANDIDATES = 8 if "burst" in ABLATE else 10**6  # no cap (v1, chosen on slice_v1); 8 = v2: a busy stretch is split per request
WINDOW_SPLIT_BEFORE = 3  # context before a continuation piece of a split stretch
WINDOW_SPLIT_AFTER = 8  # forward slice of a piece that the next piece continues
WINDOW_MAX_SPAN = 60  # ...unless first-to-last candidate would span more messages than this
WINDOW_TOPIC_EXTRA = 5  # later same-chat messages sharing a key noun, appended after the slice
WINDOWS_PER_CALL = 10**6  # no count cap: calls are packed by size only (as on slice_v1)...
PROMPT_CHAR_BUDGET = _env_int("COLDSTART_CHAR_BUDGET", 7000)  # a new call starts once a prompt passes this size (~2k tokens); slice-tested
SPLIT_OVERLAP = 6  # a window larger than the budget is cut into pieces; each repeats this many messages of the last
# completion cap per loops call: 2,000 at reasoning low (slice_v1); medium reasoning needs ~1,750 for reasoning alone
LOOPS_MAX_TOKENS = _env_int("COLDSTART_MAX_TOKENS", 6000 if "reasoning_medium" in ABLATE else 2000)
# per-call timeout: room for a full reply at the slowest provider seen (44 tokens/s), never below 120 s
LLM_TIMEOUT_S = max(120, LOOPS_MAX_TOKENS // 40 + 30)
TRUNCATION_SPLITS = 2  # a reply cut at max_tokens: split its batch in two and retry, at most this many levels deep
MAX_MSG_CHARS = 280  # message text is truncated to this in prompts
PROMPT_COMPACT = False  # False = v1 format (chosen on slice_v1); True = v2: one date line per day, HH:MM per message
LOOPS_USE_ME_NAME = "me_name" in ABLATE  # default False: v2 only: tag messages using the user's runtime display name and tell the LLM the name

# ---- status (decide.py). Wrong "done/dropped" on an open loop costs 1; wrong "open" on a done loop costs 0.
STATUS_RATIO = 1.5 if "status_r1.5" in ABLATE else 1.0 if "status_r1.0" in ABLATE else 2.0  # done/dropped only if p > STATUS_RATIO * p_open

# ---- help (decide.py). assist/remind where the key says leave costs 1; leave where it says act costs 0.
HELP_PRIVATE_GATE = "help_private" not in ABLATE  # f1 ablation drops it: assist always needs the user's yes anyway
# f2 (adopted, changelog 22): a followup (ME waiting on someone) is the other person's move, whatever owner says
HELP_FOLLOWUP_THEIRS = "no_help_followup" not in ABLATE
# help rule (changelog 36): "both_agree" = act only when the ownership rule and the 2x probability rule agree
HELP_RULE = "ownership" if "help_ownership" in ABLATE else "both_agree"
HELP_RATIO = 2.0  # the probability rule: act only if P(action) > HELP_RATIO * P(leave)
REMIND_HOUR = 10  # remind_at: 10:00 IST on the due date, or the next morning
MIN_CONFIDENCE = 0.5  # loops the LLM is less sure about are dropped

# ---- deterministic steps after the LLM (loops.py)
SHIFT_SOURCES = True  # context line -> the same sender's explicit ask right after it
SHIFT_MAX_MSGS = 3  # look this many messages ahead...
SHIFT_MAX_SECS = 600  # ...within this many seconds
MEDIA_CLOSURE = True  # the user's media right after a payment request = evidence it was paid
MEDIA_CLOSURE_MAX_MSGS = 10  # look this many messages ahead...
MEDIA_CLOSURE_MAX_SECS = 48 * 3600  # ...within this long
MEDIA_CLOSURE_SHIFT = 0.5  # move this share of p_open to p_done; decide.py's 2x rule still applies
