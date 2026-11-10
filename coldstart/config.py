"""Tunable knobs in one place (loops + decide). Change here, re-run eval/run_eval.py."""

# ---- LLM client
LLM_CONCURRENCY = 8
LLM_TIMEOUT_S = 120  # per call; a batch of 10 windows is a long prompt

# ---- candidate windows
WINDOW_BEFORE = 6  # context messages before the first candidate
WINDOW_AFTER = 20  # forward slice after the last candidate (closures, replies)
WINDOW_JOIN_GAP = 20  # candidates this close share a window (= WINDOW_AFTER, so slices don't overlap)
WINDOW_MAX_CANDIDATES = 8  # a busy stretch is split so the LLM judges each request (bursts)
WINDOW_SPLIT_BEFORE = 3  # context before a continuation piece of a split stretch
WINDOW_SPLIT_AFTER = 8  # forward slice of a piece that the next piece continues
WINDOW_MAX_SPAN = 60  # ...unless first-to-last candidate would span more messages than this
WINDOW_TOPIC_EXTRA = 5  # later same-chat messages sharing a key noun, appended after the slice
WINDOWS_PER_CALL = 10  # at most this many windows in one LLM call...
PROMPT_CHAR_BUDGET = 16000  # ...and a new call starts once a prompt passes this size (~4k tokens)
MAX_MSG_CHARS = 280  # message text is truncated to this in prompts

# ---- status (decide.py). Wrong "done/dropped" on an open loop costs 1; wrong "open" on a done loop costs 0.
STATUS_RATIO = 2.0  # done/dropped only if p > STATUS_RATIO * p_open

# ---- help (decide.py). assist/remind where the key says leave costs 1; leave where it says act costs 0.
HELP_RATIO = 2.0  # act only if p_action > HELP_RATIO * p_leave
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
